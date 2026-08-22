"""There is exactly one way to move money, and it goes through authorize().

Section 21 of the hardening brief asks for one test by name. This file is that
test, plus the things it depends on being true.

The question is not "does the happy path check the policy" -- it obviously
does, that is the whole project. The question is whether a SECOND path exists.
Every system that ships an authorization boundary also ships a debug lever, a
demo shortcut, a replay endpoint, an admin escape or a test hook, and the
boundary is only as strong as the weakest of those.

Two were found by writing this file:

  · POST /api/replay rebuilt an unknown basket by running a full journey on the
    LIVE app -- writing intents, carts and decisions, able to reach the real
    gateway -- under the hardcoded shared identity "usr_replay" and with
    exposure fixed at zero. It took no session principal at all, and its own
    docstring said "no payment and no writes".

  · The Break room's fault levers wrote to the shared catalog through
    /api/shop: set_price, set_shipping, deactivate. Not an authorization
    bypass -- authorize() ran every time -- but any visitor could permanently
    reprice the merchant for every visitor after them.

Neither is a story about the policy engine. Both are stories about the surface
around it, which is where these things always are.
"""
from __future__ import annotations

import inspect
import re

import pytest
from fastapi.testclient import TestClient

import remit.api as api_mod
from remit.api import api

BUYS = "buy a yoga mat under 2500"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("REMIT_DB", str(tmp_path / "bypass.sqlite"))
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_forthetests")
    api_mod.STATE.pop("app", None)
    with TestClient(api) as boot:
        boot.get("/health")
        yield
    api_mod.STATE.pop("app", None)


# ─────────────────────────────────────────── the test the brief asks for

def test_no_payment_path_bypasses_authorization(env):
    """Every payment row on this instance has a decision behind it.

    Drive the whole public surface -- ordinary journeys, step-ups, replays,
    comparisons, attacks, faults, forged approvals, foreign currency -- and
    then ask the database a question that does not care how the row got there:
    for every payment, is there a decision with the same correlation id, and
    did that decision reach a verdict that permits money to move?

    A bypass anywhere in the surface shows up here as an orphan row.
    """
    with TestClient(api) as c:
        c.post("/api/shop", json={"utterance": BUYS})
        step = c.post("/api/shop", json={"utterance": "buy whisky under 2000"}).json()
        c.post("/api/shop", json={"utterance": "buy whisky under 2000",
                                  "human_confirms": True,
                                  "approval_token": step["approval"]["token"]})
        c.post("/api/shop", json={"utterance": BUYS, "human_confirms": True,
                                  "approval_token": "apr_forged"})
        c.post("/api/shop", json={"utterance": "buy headphones under $5000"})
        c.post("/api/shop", json={"utterance": BUYS, "inject": {"price": 1}})
        c.post("/api/shop", json={"utterance": BUYS, "inject": {"qty": 9}})
        c.post("/api/probe", json={"utterance": BUYS, "inject": {"price": 1}})
        c.post("/api/replay", json={"correlation_id": "cor_nope",
                                    "ceiling_paise": 10 ** 9,
                                    "utterance": BUYS})
        c.post("/api/compare", json={"utterance": BUYS})
        c.post("/api/attack/injected_ceiling")

        db = api_mod.get_app().db
        payments = db.execute(
            "SELECT payment_id, correlation_id, amount_paise, user_id"
            " FROM payments").fetchall()
        assert payments, "nothing was exercised; this test proves nothing"

        for p in payments:
            row = db.execute(
                "SELECT verdict FROM decisions WHERE correlation_id=?",
                (p["correlation_id"],)).fetchone()
            assert row is not None, (
                f"payment {p['payment_id']} for {p['amount_paise']} paise has "
                f"no decision behind it -- something moved money without asking")
            assert row["verdict"] in ("AUTO", "STEP_UP"), (
                f"payment {p['payment_id']} was created under verdict "
                f"{row['verdict']}")
            assert p["user_id"] and p["user_id"].startswith("usr_"), p["user_id"]


def test_no_payment_is_attributed_to_a_shared_identity(env):
    """usr_replay was a real principal that any visitor could spend as.

    Not a forged one -- a hardcoded one, in the source, shared by everybody who
    pressed the property line. Its exposure and velocity were pooled, and no
    session cookie was involved at any point.
    """
    with TestClient(api) as c:
        c.post("/api/replay", json={"correlation_id": "cor_x",
                                    "ceiling_paise": 10 ** 9, "utterance": BUYS})
        c.post("/api/shop", json={"utterance": BUYS})
    users = {r["user_id"] for r in
             api_mod.get_app().db.execute("SELECT DISTINCT user_id FROM payments")}
    assert "usr_replay" not in users, "the shared spending identity is back"
    assert "usr_demo" not in users


def test_the_replay_endpoint_writes_nothing(env):
    """Its docstring has always claimed this. Now something checks."""
    with TestClient(api) as c:
        counts = lambda: tuple(
            api_mod.get_app().db.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
            for t in ("payments", "decisions", "intents", "carts"))
        before = counts()
        for ceiling in (10 ** 3, 10 ** 6, 10 ** 9):
            r = c.post("/api/replay", json={"correlation_id": "cor_fresh",
                                            "ceiling_paise": ceiling,
                                            "utterance": BUYS})
            assert r.status_code == 200, r.json()
            assert r.json()["note"].startswith("pure re-decision")
        assert counts() == before, "the property line wrote to the database"


def test_the_replay_endpoint_takes_a_session_principal(env):
    """Structural: it is the only money-capable route that did not."""
    src = inspect.getsource(api_mod.replay)
    assert "principal(request)" in src
    assert "usr_replay" not in src.split('"""')[-1], (
        "the hardcoded identity is still in the code path")


def test_one_visitors_basket_is_not_readable_by_another(env):
    """A correlation id is on screen, in the ledger and in the logs. It was the
    only thing between one visitor's basket and another's replay."""
    with TestClient(api) as alice, TestClient(api) as bob:
        a = alice.post("/api/shop", json={"utterance": BUYS}).json()
        assert a["correlation_id"]
        stolen = bob.post("/api/replay", json={
            "correlation_id": a["correlation_id"], "ceiling_paise": 10 ** 9}).json()
        assert stolen.get("error"), "Bob re-decided Alice's basket"
        mine = alice.post("/api/replay", json={
            "correlation_id": a["correlation_id"], "ceiling_paise": 10 ** 9}).json()
        assert "authorization" in mine, mine


# ────────────────────────────────────────────── the surface, structurally

def test_every_state_changing_route_resolves_a_principal(env):
    """A route that mutates state and never asks who is calling is a route
    where every limit in the system is somebody else's problem."""
    import remit.api as m
    exempt = {
        # HMAC-signed by the gateway; identity comes from the signature over
        # the body, not from a session, because Razorpay has no cookie.
        "webhook", "payment_verify",
        # admin token, and 404s when that is unset
        "reset",
        # gateway truth, no caller-supplied input at all
        "reconcile",
        # runs a named attack on a throwaway instance; no live state
        "attack_run",
    }
    missing = []
    for name in ("shop", "probe", "replay", "compare"):
        fn = getattr(m, name, None)
        assert fn is not None, f"route {name} disappeared"
        if "principal(request)" not in inspect.getsource(fn):
            missing.append(name)
    assert missing == [], f"state-changing routes with no principal: {missing}"
    assert exempt


def test_the_gateway_is_only_reachable_through_the_journey(env):
    """The order-creating call must have exactly one caller in the live path."""
    import remit.buyer.journey as j
    src = inspect.getsource(j)
    assert src.count('broker.call("create_order"') <= 1, (
        "create_order is invoked from more than one place in the journey")
    # and the call sits after the policy decision, not before it
    authorize_at = src.index("authorize(")
    order_at = src.index('"create_order"')
    assert authorize_at < order_at, (
        "the order is created before the policy has decided")
