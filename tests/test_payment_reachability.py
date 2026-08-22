"""Can a person who lands on the site actually pay?

Every test here is a bug that shipped. The engine was correct and the payment
was unreachable, which from the outside is the same thing as being broken --
and worse, because it looks deliberate.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import remit.api as api_mod
from remit.api import api
from remit.buyer.journey import AMBIGUOUS

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REMIT_DB", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_forthetests")
    api_mod.STATE.pop("app", None)
    with TestClient(api) as c:
        yield c
    api_mod.STATE.pop("app", None)


def visitor():
    """A separate browser. TestClient keeps its own cookie jar, so a new one is
    a new session principal -- which is now the only way to be a different
    person. Passing a `user_id` used to be enough, and that was the bug."""
    return TestClient(api)


def shop(client, utterance, user=None, **kw):
    # `user` is accepted and ignored on purpose: these tests used to pass an
    # identity in the body, and leaving the argument in place makes it obvious
    # at the call site that it no longer does anything. Identity comes from the
    # session cookie the server issued.
    return client.post("/api/shop", json={"utterance": utterance, **kw}).json()


# --------------------------------------------------------------- the step-up

def test_a_step_up_can_actually_be_confirmed(client):
    """The product's entire thesis is that a person gets asked. Until this
    worked, being asked was a dead end: the browser had no way to answer, so
    six of the ten example sentences on the home page could never reach a
    payment at all. FAILURES #23."""
    first = shop(client, "buy whisky under 2000")
    assert first["authorization"]["verdict"] == "STEP_UP"
    assert first["payment_state"] == "AWAITING_HUMAN"
    assert first["order_id"] is None, "no order may exist before the human says yes"

    ok = shop(client, "buy whisky under 2000", human_confirms=True)
    assert ok["order_id"], "confirming a step-up produced no order"
    assert ok["payment_state"] == "CREATED"


def test_declining_a_step_up_moves_no_money(client):
    shop(client, "buy whisky under 2000")
    no = shop(client, "buy whisky under 2000", human_confirms=False)
    assert no["payment_state"] == "DECLINED_BY_HUMAN"
    assert no["order_id"] is None


# --------------------------------------------------------------- the exposure

def test_one_visitor_does_not_spend_another_visitors_limit(client):
    """Exposure was summed over every payment row on the instance, for all
    time, and handed to the policy engine as one person's hourly velocity. The
    thirteenth journey on a deployment refused every utterance from every
    visitor, permanently. FAILURES #21.

    Fifteen separate browsers, then a sixteenth. Each gets its own session
    cookie, which is now the only thing that makes them different people."""
    for _ in range(15):
        with visitor() as c:
            shop(c, "buy a yoga mat under 1500")
    with visitor() as late:
        mine = shop(late, "buy a yoga mat under 1500")
    assert mine["authorization"]["verdict"] != "DENY", mine["authorization"]["reason"]
    assert mine["exposure"]["txn_count_1h"] == 0, "counted other people's payments"


def test_exposure_forgets_yesterday(client, monkeypatch):
    """A daily cap that never resets is not a daily cap."""
    d = shop(client, "buy a yoga mat under 1500")
    who = api_mod.get_app().db.execute(
        "SELECT user_id FROM payments ORDER BY rowid DESC LIMIT 1").fetchone()
    assert who, d
    api_mod.get_app().db.execute(
        "UPDATE payments SET created_at=? WHERE user_id=?",
        ((NOW - timedelta(days=3)).isoformat(), who["user_id"]))
    exp = api_mod._exposure(api_mod.get_app(), who["user_id"])
    assert exp.daily_paise == 0 and exp.txn_count_1h == 0


# ---------------------------------------------------------------- the checkout

def test_repeating_an_utterance_still_leads_to_a_payable_order(client):
    """Idempotency returned the FIRST journey's payment row and left it
    pointing at the first correlation id. The browser then drew a Pay button
    for a journey whose order the checkout endpoint could not find, and
    answered the click with "a STEP_UP or DENY has nothing to pay" -- on a
    journey the engine had just approved. FAILURES #20."""
    first = shop(client, "buy a yoga mat under 1500")
    again = shop(client, "buy a yoga mat under 1500")
    assert again["replayed"] is True
    assert again["order_id"] == first["order_id"], "idempotency broke"
    assert again["correlation_id"] != first["correlation_id"]

    r = client.get(f"/api/checkout/{again['correlation_id']}")
    assert r.status_code == 200, r.json()
    assert r.json()["order_id"] == first["order_id"]


def test_checkout_never_leaks_the_secret(client, monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "sk_this_must_never_travel")
    d = shop(client, "buy a yoga mat under 1500")
    body = client.get(f"/api/checkout/{d['correlation_id']}").json()
    assert body["key_id"].startswith("rzp_test_")
    assert "secret" not in " ".join(map(str, body)).lower()
    assert os.environ["RAZORPAY_KEY_SECRET"] not in str(body)


def test_the_404_tells_you_the_checkout_routes_exist(client):
    routes = client.get("/api/nope").json()["routes"]
    assert "/api/payment/verify" in routes
    assert any("checkout" in r for r in routes)


# ------------------------------------------------------------ the ambiguity

def test_a_real_gateway_timeout_is_ambiguous_not_failed():
    """httpx.TimeoutException is not a subclass of the built-in TimeoutError,
    so the branch that parks an order in UNKNOWN for the reconciler was
    unreachable from the real client. A read-timeout -- the one case where the
    order may well exist -- was recorded as terminally FAILED. FAILURES #22."""
    import httpx
    assert issubclass(httpx.TimeoutException, AMBIGUOUS)
    assert not issubclass(httpx.TimeoutException, TimeoutError)
