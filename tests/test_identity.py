"""Alice cannot be Bob.

Every control in REMIT is downstream of identity. Exposure, velocity, the
idempotency namespace, approval ownership and order lookup are all keyed on a
principal — and until FAILURES #32 that principal arrived in the request body,
so the honest description of the system was "these limits apply to whoever
agrees to be limited".

Each test below is one of the seven things the fix has to make impossible. They
are written against the HTTP boundary on purpose: the domain layer takes a
`user_id` argument and always will, because it is a pure function of its
inputs. What matters is that nothing a caller controls can choose that value.

Two TestClients are two browsers: separate cookie jars, therefore separate
principals. That is now the only way to be a different person.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import remit.api as api_mod
from remit.api import api

ASKS = "buy whisky under 2000"          # RESTRICT-001: always a step-up
BUYS = "buy a yoga mat under 2500"      # AUTO: reaches an order


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("REMIT_DB", str(tmp_path / "id.sqlite"))
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_forthetests")
    api_mod.STATE.pop("app", None)
    with TestClient(api) as boot:
        boot.get("/health")
        yield
    api_mod.STATE.pop("app", None)


def who(c) -> str:
    """The principal the server assigned this browser."""
    c.post("/api/shop", json={"utterance": "hello"})
    from remit.auth import COOKIE, verify
    return verify(c.cookies.get(COOKIE), api_mod._SESSION["secret"])


def shop(c, utterance, **kw):
    return c.post("/api/shop", json={"utterance": utterance, **kw}).json()


# ------------------------------------------------------------------ the seven

def test_1_alice_cannot_spend_as_bob(env):
    with TestClient(api) as alice, TestClient(api) as bob:
        a, b = who(alice), who(bob)
        assert a and b and a != b, "two browsers got the same principal"

        # Alice tries every spelling of "be Bob" a caller has access to.
        for payload in ({"user_id": b}, {"userId": b}, {"principal": b},
                        {"user_id": b, "human_confirms": True}):
            d = alice.post("/api/shop", json={"utterance": BUYS, **payload}).json()
            assert d.get("intent") is None or d["intent"]["user_id"] == a, (
                f"{payload} moved the journey onto {d['intent']['user_id']}")

        rows = api_mod.get_app().db.execute(
            "SELECT DISTINCT user_id FROM payments").fetchall()
        assert b not in {r["user_id"] for r in rows}, "a payment landed on Bob"


def test_2_alice_cannot_redeem_bobs_approval(env):
    with TestClient(api) as alice, TestClient(api) as bob:
        step = shop(bob, ASKS)
        assert step["payment_state"] == "AWAITING_HUMAN", step["payment_state"]
        token = step["approval"]["token"]

        stolen = shop(alice, ASKS, approval_token=token)
        assert stolen["payment_state"] == "APPROVAL_REJECTED", stolen
        assert "wrong_actor" in stolen["note"]

        # and Bob's token is still his to use
        ok = shop(bob, ASKS, approval_token=token)
        assert ok["payment_state"] == "CREATED", ok


def test_3_alice_cannot_use_bobs_mandate(env):
    """The envelope is bound to a principal by its semantic hash, which
    includes the user. Alice replaying Bob's exact sentence gets her OWN
    envelope and her own idempotency namespace -- not a ride on his."""
    with TestClient(api) as alice, TestClient(api) as bob:
        b = shop(bob, BUYS)
        a = shop(alice, BUYS)
        assert b["intent"]["intent_id"] != a["intent"]["intent_id"]
        assert a["replayed"] is False, "Alice inherited Bob's payment"
        assert a["order_id"] != b["order_id"]


def test_4_alice_cannot_spend_bobs_limits(env):
    """Bob burns his hourly velocity. Alice's first journey must not care."""
    with TestClient(api) as bob:
        for i in range(14):
            shop(bob, f"buy a yoga mat under {1500 + i}")
        tired = shop(bob, "buy a yoga mat under 1600")
    with TestClient(api) as alice:
        fresh = shop(alice, BUYS)
    assert fresh["exposure"]["txn_count_1h"] == 0, fresh["exposure"]
    assert (fresh.get("authorization") or {}).get("verdict") != "DENY", fresh
    assert tired["exposure"]["txn_count_1h"] > 0, "Bob's own exposure never grew"


def test_5_alice_cannot_reuse_bobs_idempotency_namespace(env):
    """Same sentence, same cart, same price -- two people, two payments.

    The key is H(user | semantic hash | cart | total | catalog version). If
    identity were forgeable the second buyer would silently 'replay' the first
    buyer's payment and never receive anything."""
    with TestClient(api) as alice, TestClient(api) as bob:
        shop(bob, BUYS)
        a = shop(alice, BUYS)
    rows = api_mod.get_app().db.execute(
        "SELECT user_id, idem_key FROM payments").fetchall()
    keys = {r["idem_key"] for r in rows}
    users = {r["user_id"] for r in rows}
    assert len(users) == 2, users
    assert len(keys) == 2, "two principals collided in one idempotency namespace"
    assert a["replayed"] is False


def test_6_alice_cannot_open_bobs_order(env):
    """A correlation id is not a secret: it is on screen, in the ledger and in
    the logs. Looking one up without asking who wants it hands any visitor
    another visitor's live Razorpay order."""
    with TestClient(api) as alice, TestClient(api) as bob:
        b = shop(bob, BUYS)
        assert b["order_id"], b

        stolen = alice.get(f"/api/checkout/{b['correlation_id']}")
        assert stolen.status_code == 404, stolen.json()

        mine = bob.get(f"/api/checkout/{b['correlation_id']}")
        assert mine.status_code == 200, mine.json()
        assert mine.json()["order_id"] == b["order_id"]


def test_7_alice_cannot_wipe_the_instance(env, monkeypatch):
    """Not a spending lever, which is why it survived the first identity pass.
    'You cannot spend as Bob' is thin next to 'you can delete Bob'."""
    with TestClient(api) as alice:
        off = alice.post("/api/reset")
        assert off.status_code == 404, off.json()

        monkeypatch.setenv("REMIT_ADMIN_TOKEN", "operator-only")
        assert alice.post("/api/reset").status_code == 403
        assert alice.post("/api/reset",
                          headers={"x-remit-admin": "guess"}).status_code == 403
        assert alice.post("/api/reset",
                          headers={"x-remit-admin": "operator-only"}
                          ).status_code == 200


# --------------------------------------------------------------- the mechanism

def test_the_request_schema_has_nowhere_to_put_an_identity(env):
    """The strongest version of this guarantee is structural: there is no field
    to set. A rejected field is a field somebody will find a second spelling
    for."""
    from remit.api import CompareRequest, ShopRequest
    for model in (ShopRequest, CompareRequest):
        assert "user_id" not in model.model_fields, model.__name__
        assert not any("user" in f for f in model.model_fields), model.__name__


def test_a_forged_session_cookie_is_refused(env):
    from remit.auth import mint, verify
    secret = api_mod._SESSION["secret"]
    good = mint(secret)
    pid = verify(good, secret)
    assert pid and pid.startswith("usr_")
    assert verify(f"{pid}.deadbeef", secret) is None
    assert verify("usr_imadethisup.0" * 2, secret) is None
    assert verify(None, secret) is None
    assert verify("", secret) is None
    assert verify(good, "a-different-secret") is None


def test_a_session_survives_across_requests(env):
    with TestClient(api) as c:
        first = who(c)
        second = who(c)
        assert first == second, "the principal changed under the same cookie jar"


def test_the_secret_fails_closed_when_live(env, monkeypatch):
    from remit.auth import session_secret
    monkeypatch.delenv("REMIT_SESSION_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="REMIT_SESSION_SECRET"):
        session_secret(live=True)
    assert session_secret(live=False).startswith("dev-")
