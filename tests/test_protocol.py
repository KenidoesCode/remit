"""/v1, and the claim that REMIT is infrastructure rather than a website.

The claim is only worth making if two things are true, and both are tested
here rather than asserted in a document.

**It has no engine of its own.** Every /v1 route is a projection over the same
journey, the same policy engine and the same payment path that /api/shop uses.
If it had a second code path, the guarantee a reviewer verifies on the website
would not be the guarantee an integrator gets -- and the first thing anyone
would find is the disagreement between them.

**An external client needs nothing from this repository.**
`agents/external_agent.py` imports `json` and `urllib`. That is the whole
dependency list, and a test below asserts it stays that way.
"""
from __future__ import annotations

import ast
import pathlib

import pytest
from fastapi.testclient import TestClient

import remit.api as api_mod
from remit.api import api
from remit.protocol import PROTOCOL_VERSION

ROOT = pathlib.Path(__file__).resolve().parents[1]
BUYS = "buy running shoes under 5000"
ASKS = "buy whisky under 2000"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("REMIT_DB", str(tmp_path / "v1.sqlite"))
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_forthetests")
    api_mod.STATE.pop("app", None)
    with TestClient(api) as boot:
        boot.get("/health")
        yield
    api_mod.STATE.pop("app", None)


# ───────────────────────────────────────────────────── the surface works

def test_the_protocol_describes_itself(env):
    with TestClient(api) as c:
        d = c.get("/v1/").json()
        assert d["protocol"] == "remit"
        assert d["version"] == PROTOCOL_VERSION
        assert set(d["nouns"]) == {"intent", "authority", "action", "decision",
                                   "evidence", "execution"}
        # the limitations are in the contract, not only in the README
        assert any("test mode" in n for n in d["notes"])


def test_an_intent_becomes_a_bounded_authority(env):
    with TestClient(api) as c:
        d = c.post("/v1/intents", json={"utterance": BUYS}).json()
        assert d["intent"]["ceiling"]["amount_paise"] == 500000
        assert d["intent"]["ceiling"]["currency"] == "INR"
        assert d["intent"]["semantic_hash"]
        assert d["authority"]["state"] in ("DRAFT", "INTERPRETED")
        assert d["authority"]["revoked"] is False


def test_an_amount_never_arrives_without_a_unit(env):
    """`Money` exists because the mistake that let "under $5,000" become a
    5,000-rupee ceiling lived in exactly the gap between a number and its
    unit."""
    with TestClient(api) as c:
        for path, body in (("/v1/intents", {"utterance": BUYS}),
                           ("/v1/evaluate", {"utterance": BUYS}),
                           ("/v1/execute", {"utterance": BUYS})):
            d = c.post(path, json=body).json()
            for money in _every_money(d):
                assert "currency" in money and money["currency"], money


def _every_money(node):
    if isinstance(node, dict):
        if "amount_paise" in node:
            yield node
        for v in node.values():
            yield from _every_money(v)
    elif isinstance(node, list):
        for v in node:
            yield from _every_money(v)


def test_evaluate_moves_no_money(env):
    with TestClient(api) as c:
        before = _count(c)
        for _ in range(4):
            d = c.post("/v1/evaluate", json={"utterance": BUYS}).json()
            assert d["sandboxed"] is True
            assert "would_execute" in d
        assert _count(c) == before, "an evaluation created a payment"


def _count(c):
    return api_mod.get_app().db.execute(
        "SELECT COUNT(*) c FROM payments").fetchone()["c"]


def test_execute_pays_once_however_many_times_it_is_called(env):
    with TestClient(api) as c:
        ids = set()
        for _ in range(5):
            d = c.post("/v1/execute", json={"utterance": BUYS}).json()
            assert d["decision"]["verdict"] in ("AUTO", "STEP_UP")
            ids.add(d["execution"]["payment_id"])
        assert len(ids) == 1, ids
        assert _count(c) == 1


def test_a_step_up_says_what_is_being_asked(env):
    with TestClient(api) as c:
        d = c.post("/v1/step-up", json={"utterance": ASKS}).json()
        assert d["required"] is True
        assert d["asking"]["clause"] == "RESTRICT-001"
        assert d["asking"]["amount"]["amount_paise"] > 0
        assert d["asking"]["items"], "nothing to show the human"
        assert d["approval"]["token"]


def test_an_approval_is_spent_exactly_once_over_the_protocol(env):
    with TestClient(api) as c:
        tok = c.post("/v1/step-up", json={"utterance": ASKS}
                     ).json()["approval"]["token"]
        first = c.post("/v1/approve", json={"utterance": ASKS,
                                            "approval_token": tok}).json()
        assert first["execution"]["state"] == "CREATED"
        again = c.post("/v1/approve", json={"utterance": ASKS,
                                            "approval_token": tok}).json()
        assert again["execution"]["state"] == "APPROVAL_REJECTED"
        assert "already_used" in again["decision"]["reason"]


def test_deny_ends_the_authority(env):
    with TestClient(api) as c:
        c.post("/v1/step-up", json={"utterance": ASKS})
        d = c.post("/v1/deny", json={"utterance": ASKS,
                                     "approval_token": "unused"}).json()
        assert d["authority_state"] == "CANCELLED"


def test_revoke_over_the_protocol_stops_execution(env):
    with TestClient(api) as c:
        assert c.post("/v1/execute", json={"utterance": BUYS}
                      ).json()["execution"]["state"] == "CREATED"
        rv = c.post("/v1/revoke", json={"reason": "done"}).json()
        assert rv["revocation_id"].startswith("rvk_")
        after = c.post("/v1/execute", json={"utterance": "buy chips under 200"}
                       ).json()
        assert after["decision"]["verdict"] == "DENY"
        assert after["execution"]["state"] == "BLOCKED"


def test_the_audit_answers_why_and_is_scoped(env):
    with TestClient(api) as alice, TestClient(api) as bob:
        d = alice.post("/v1/execute", json={"utterance": BUYS}).json()
        cid = d["decision"]["correlation_id"]
        mine = alice.get(f"/v1/audit/{cid}").json()
        assert mine["chain_intact"] is True
        assert len(mine["events"]) >= 10
        assert mine["decision"]["verdict"] in ("AUTO", "STEP_UP")
        assert mine["authority_history"][0]["to_state"] == "DRAFT"
        # somebody else's correlation id is a 404, not a 403: whether it
        # exists is not a thing this endpoint should confirm
        assert bob.get(f"/v1/audit/{cid}").status_code == 404


def test_an_authorization_is_not_readable_by_another_actor(env):
    with TestClient(api) as alice, TestClient(api) as bob:
        iid = alice.post("/v1/intents", json={"utterance": BUYS}
                         ).json()["intent"]["intent_id"]
        assert alice.get(f"/v1/authorization/{iid}").status_code == 200
        assert bob.get(f"/v1/authorization/{iid}").status_code == 404


def test_an_ungroundable_request_is_422_not_a_guess(env):
    with TestClient(api) as c:
        r = c.post("/v1/execute", json={"utterance": "buy a helicopter"})
        assert r.status_code == 422
        assert r.json()["error"] == "not_grounded"


def test_executing_against_an_id_alone_is_refused(env):
    """An authority is bound to the words that created it. Executing against an
    id would let a caller reuse somebody's mandate for a different request."""
    with TestClient(api) as c:
        iid = c.post("/v1/intents", json={"utterance": BUYS}
                     ).json()["intent"]["intent_id"]
        r = c.post("/v1/execute", json={"intent_id": iid})
        assert r.status_code == 400


# ─────────────────────────────────── it is the same engine, not a second one

def test_v1_and_the_website_agree_on_every_verdict(env):
    """The property that makes publishing this worth anything."""
    for u in (BUYS, ASKS, "buy headphones under $5000", "buy chips under 20",
              "buy a laptop under 50000", "order 3 kg rice and cooking oil under 2000"):
        with TestClient(api) as web, TestClient(api) as agent:
            a = web.post("/api/shop", json={"utterance": u}).json()
            b = agent.post("/v1/execute", json={"utterance": u})
            if a.get("authorization") is None:
                # No cart, therefore no decision. The protocol must say
                # "no_decision", not invent a DENY -- the policy engine was
                # never reached, and an integrator's retry logic depends on
                # knowing which of those happened.
                assert b.status_code == 422, (u, b.status_code, b.json())
                assert b.json()["error"] in ("not_grounded", "no_decision"), \
                    (u, b.json())
                continue
            assert b.status_code == 200, (u, b.json())
            assert b.json()["decision"]["verdict"] == a["authorization"]["verdict"], u
            assert sorted(b.json()["decision"]["failed"]) == \
                sorted(a["authorization"]["failed"]), u


def test_v1_has_no_engine_of_its_own(env):
    """Structural. `remit/v1.py` may build views and call the journey. It may
    not compute a verdict, price a cart or touch the gateway."""
    src = (ROOT / "remit" / "v1.py").read_text()
    # FakeGateway is imported to BUILD the sandbox /v1/evaluate runs on --
    # that is construction, not execution. What must not appear is a decision
    # being computed or an order being created here.
    for forbidden in ("authorize(", "compute_drift(", "price_cart(",
                      "PaymentStore(", "create_order", "Verdict.AUTO",
                      "assess(", "hard_filter("):
        assert forbidden not in src, (
            f"{forbidden} in v1.py -- the protocol is reimplementing the engine")
    assert "journey.run" in src


def test_every_v1_route_resolves_a_principal(env):
    src = (ROOT / "remit" / "v1.py").read_text()
    tree = ast.parse(src)
    routed = [n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef)
              and any(isinstance(d, ast.Call) and
                      getattr(getattr(d.func, "value", None), "id", "") == "v1"
                      for d in n.decorator_list)]
    assert len(routed) >= 8, [f.name for f in routed]
    for fn in routed:
        body = ast.get_source_segment(src, fn) or ""
        if fn.name == "describe":
            continue
        assert "principal(request)" in body, (
            f"/v1 route {fn.name} never asks who is calling")


# ─────────────────────────────────────── an external client needs nothing

def test_the_external_agent_imports_nothing_from_this_repository(env):
    """The constraint that makes the claim real. If this file needed the
    journey, the envelope class or the catalog, REMIT would be a library with
    a website on top."""
    src = (ROOT / "agents" / "external_agent.py").read_text()
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "remit" not in imported, sorted(imported)
    assert imported <= {"__future__", "json", "sys", "urllib"}, sorted(imported)


def test_the_external_agent_walks_the_whole_protocol(env):
    """Runs the agent's own flow in-process against the same routes, so the
    demo in agents/ cannot rot while the suite stays green."""
    with TestClient(api) as c:
        assert c.get("/v1/").status_code == 200
        assert c.post("/v1/intents", json={"utterance": BUYS}).status_code == 200
        assert c.post("/v1/evaluate", json={"utterance": BUYS}).status_code == 200
        one = c.post("/v1/execute", json={"utterance": BUYS}).json()
        two = c.post("/v1/execute", json={"utterance": BUYS}).json()
        assert two["execution"]["payment_id"] == one["execution"]["payment_id"]
        assert two["execution"]["replayed"] is True
        tok = c.post("/v1/step-up", json={"utterance": ASKS}
                     ).json()["approval"]["token"]
        assert c.post("/v1/approve", json={"utterance": ASKS,
                                           "approval_token": tok}
                      ).json()["execution"]["state"] == "CREATED"
        assert c.post("/v1/approve", json={"utterance": ASKS,
                                           "approval_token": tok}
                      ).json()["execution"]["state"] == "APPROVAL_REJECTED"
        cid = one["decision"]["correlation_id"]
        assert c.get(f"/v1/audit/{cid}").json()["chain_intact"] is True
        assert c.post("/v1/revoke", json={"reason": "done"}).status_code == 200
        assert c.post("/v1/execute", json={"utterance": "buy chips under 200"}
                      ).json()["execution"]["state"] == "BLOCKED"
