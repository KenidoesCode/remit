"""Can I stop it?

That is the question a person asks before handing an agent money, and until
this file existed REMIT answered it on the page while not implementing it in
the code. `intents.revoked_at` had been in the schema since the first
migration. Nothing wrote to it. Nothing read it. `AUTH-003` -- a hard DENY
clause whose whole purpose is to refuse a revoked mandate -- took its input
from `inject.get("revoked")`, a boolean the caller passed in on the request.

So revocation was a demo lever: it worked when you asked it to work.

The tests below are the nine situations the brief names, plus the two
properties that make revocation mean anything -- it wins over pending
authority, and it does not reach backwards into money that already moved.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import remit.api as api_mod
from remit.api import api
from remit.assembly import build
from remit.domain.risk import Exposure
from remit.exec.razorpay import FakeGateway
from remit.grants.revocation import NoSuchIntent, NotYours

NOW = datetime(2026, 8, 22, 11, 0, tzinfo=timezone.utc)
BUYS = "buy a yoga mat under 2500"
ASKS = "buy whisky under 2000"


@pytest.fixture
def app():
    return build(now=NOW, gateway=FakeGateway())


@pytest.fixture
def http(tmp_path, monkeypatch):
    monkeypatch.setenv("REMIT_DB", str(tmp_path / "rev.sqlite"))
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_forthetests")
    api_mod.STATE.pop("app", None)
    with TestClient(api) as boot:
        boot.get("/health")
        yield
    api_mod.STATE.pop("app", None)


def run(app, utterance=BUYS, user="usr_rev", now=NOW, **kw):
    return app.journey.run(utterance=utterance, user_id=user, now=now,
                           exposure=Exposure(), **kw)


def failed(r):
    return [c.clause_id for c in r.authorization.clauses if not c.passed] \
        if r.authorization else []


# ────────────────────────────────────────────────────────── it actually works

def test_revoke_before_execution_blocks(app):
    app.revocations.revoke(user_id="usr_rev", now=NOW, reason="changed my mind")
    r = run(app, human_confirms=True)
    assert r.payment_state == "BLOCKED", r.payment_state
    assert "AUTH-003" in failed(r)
    assert r.payment_id is None or not app.payments.get(r.payment_id)


def test_revoking_one_intent_leaves_the_others_alone(app):
    first = run(app, human_confirms=True)
    assert first.payment_state == "CREATED"
    app.revocations.revoke(user_id="usr_rev", now=NOW, scope="intent",
                           target=first.intent.intent_id)
    # a different mandate is a different authority
    other = run(app, "buy running shoes under 5000", human_confirms=True)
    assert other.payment_state == "CREATED", other.note
    # and the revoked one stays revoked
    assert app.revocations.is_revoked(user_id="usr_rev",
                                      intent_id=first.intent.intent_id)


def test_the_principal_scope_is_a_kill_switch(app):
    app.revocations.revoke(user_id="usr_rev", now=NOW)
    for u in (BUYS, "buy running shoes under 5000", "buy chips under 200"):
        r = run(app, u, human_confirms=True)
        assert r.payment_state == "BLOCKED", (u, r.payment_state)


def test_revoke_during_a_step_up_blocks_the_approval(app):
    """The window that matters: a person asked to approve, thought better of
    it, and pressed stop instead."""
    step = run(app, ASKS)
    assert step.payment_state == "AWAITING_HUMAN"
    token = step.approval["token"]
    app.revocations.revoke(user_id="usr_rev", now=NOW, reason="no")
    after = run(app, ASKS, human_confirms=True, approval_token=token)
    assert after.payment_state == "BLOCKED", after.payment_state
    assert "AUTH-003" in failed(after)


def test_a_revocation_does_not_burn_the_approval_token(app):
    """Refusing before redemption, not by redeeming and discarding. If the
    revocation consumed the token, un-revoking would leave the person holding
    a dead approval for a basket they never bought."""
    step = run(app, ASKS)
    token = step.approval["token"]
    app.revocations.revoke(user_id="usr_rev", now=NOW)
    run(app, ASKS, human_confirms=True, approval_token=token)
    row = app.journey.approvals.get(token)
    assert row["used_at"] is None, "the token was spent by a blocked journey"


def test_revoke_after_execution_changes_nothing_that_happened(app):
    """Forward only. Reversing settled money is a refund -- a different
    operation with a different authority -- and a control plane that quietly
    unwinds completed payments is one nobody can reason about."""
    done = run(app, human_confirms=True)
    assert done.payment_state == "CREATED"
    before = app.payments.get(done.payment_id)
    rv = app.revocations.revoke(user_id="usr_rev", now=NOW)
    after = app.payments.get(done.payment_id)
    assert dict(after) == dict(before), "revocation reached backwards"
    assert rv.revocation_id


def test_revoking_twice_is_the_same_revocation(app):
    """Somebody pressing stop a second time wants the same outcome, not an
    error. One revocation, one timestamp, `already_revoked` set."""
    a = app.revocations.revoke(user_id="usr_rev", now=NOW, reason="first")
    b = app.revocations.revoke(user_id="usr_rev", now=NOW, reason="second")
    assert b.already is True
    assert b.revocation_id == a.revocation_id
    assert b.reason == "first", "the second press rewrote the record"


def test_revoking_something_that_never_existed_is_refused(app):
    with pytest.raises(NoSuchIntent):
        app.revocations.revoke(user_id="usr_rev", now=NOW, scope="intent",
                               target="int_imaginary")


def test_one_persons_authority_is_not_anothers_to_cancel(app):
    mine = run(app, user="usr_owner", human_confirms=True)
    with pytest.raises(NotYours):
        app.revocations.revoke(user_id="usr_thief", now=NOW, scope="intent",
                               target=mine.intent.intent_id)
    with pytest.raises(NotYours):
        app.revocations.revoke(user_id="usr_thief", now=NOW,
                               scope="principal", target="usr_owner")
    assert not app.revocations.is_revoked(user_id="usr_owner")


def test_an_unknown_scope_is_refused(app):
    with pytest.raises(ValueError):
        app.revocations.revoke(user_id="usr_rev", now=NOW, scope="everything")


# ───────────────────────────────────────────────── revocation wins the race

def test_revocation_wins_against_concurrent_execution(app):
    """Genuinely concurrent, not a loop.

    The invariant the brief names: revocation must win over pending authority.
    With one process-wide lock the two operations serialise, so the outcome is
    "either it paid or it was blocked" -- never both, and never a payment
    dated after the revocation.
    """
    barrier_reached = []

    def spend(_):
        r = run(app, "buy running shoes under 5000", human_confirms=True)
        barrier_reached.append(r.payment_state)
        return r

    def stop(_):
        return app.revocations.revoke(user_id="usr_rev", now=NOW)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(spend if i % 2 == 0 else stop, i)
                   for i in range(8)]
        [f.result() for f in futures]

    assert app.revocations.is_revoked(user_id="usr_rev")
    revoked_at = app.revocations.check(user_id="usr_rev").revoked_at
    rows = app.db.execute(
        "SELECT created_at, state FROM payments WHERE user_id='usr_rev'"
    ).fetchall()
    for row in rows:
        assert row["created_at"] <= revoked_at, (
            f"a payment was created at {row['created_at']}, after the "
            f"authority was revoked at {revoked_at}")
    # and nothing at all executes afterwards
    assert run(app, "buy chips under 200",
               human_confirms=True).payment_state == "BLOCKED"


def test_only_one_revocation_row_survives_a_storm(app):
    with ThreadPoolExecutor(max_workers=12) as pool:
        [f.result() for f in [
            pool.submit(lambda _: app.revocations.revoke(
                user_id="usr_rev", now=NOW, reason="stop"), i)
            for i in range(12)]]
    n = app.db.execute(
        "SELECT COUNT(*) c FROM revocations WHERE user_id='usr_rev'"
    ).fetchone()["c"]
    assert n == 1, f"{n} revocation rows for one kill switch"


# ────────────────────────────────────────────────────────── over HTTP

def test_the_endpoint_is_bound_to_the_caller(http):
    with TestClient(api) as alice, TestClient(api) as bob:
        a = alice.post("/api/shop", json={"utterance": BUYS}).json()
        assert a["payment_state"] == "CREATED", a

        stolen = bob.post("/api/revoke", json={
            "scope": "intent", "intent_id": a["intent"]["intent_id"]})
        assert stolen.status_code == 404, stolen.json()

        # Alice is untouched by Bob's attempt
        again = alice.post("/api/shop",
                           json={"utterance": "buy chips under 200"}).json()
        assert again["payment_state"] in ("CREATED", "AWAITING_HUMAN"), again

        mine = alice.post("/api/revoke", json={"reason": "done for today"})
        assert mine.status_code == 200, mine.json()
        assert mine.json()["scope"] == "principal"
        blocked = alice.post("/api/shop",
                             json={"utterance": "buy a notebook under 300"}).json()
        assert blocked["payment_state"] == "BLOCKED", blocked

        # and Bob, who revoked nothing, still works
        ok = bob.post("/api/shop", json={"utterance": BUYS}).json()
        assert ok["payment_state"] == "CREATED", ok


def test_the_listing_is_scoped_and_says_what_was_revoked(http):
    with TestClient(api) as alice, TestClient(api) as bob:
        alice.post("/api/shop", json={"utterance": BUYS})
        alice.post("/api/revoke", json={"reason": "handing the laptop back"})
        mine = alice.get("/api/revocations").json()
        assert mine["revoked"] is True
        assert len(mine["revocations"]) == 1
        assert mine["revocations"][0]["reason"] == "handing the laptop back"
        assert bob.get("/api/revocations").json()["revocations"] == []


def test_the_revocation_is_in_the_ledger(http):
    with TestClient(api) as c:
        c.post("/api/shop", json={"utterance": BUYS})
        c.post("/api/revoke", json={"reason": "audit me"})
        kinds = [e["kind"] for e in c.get("/api/ledger").json()["events"]]
        assert "AUTHORIZATION_REVOKED" in kinds, kinds[:8]
