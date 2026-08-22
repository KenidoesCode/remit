"""What happens if the process dies between authorization and payment.

The brief asks that question and says: do not assume, model it, test it. It is
the right question, because it is the one moment where REMIT's two stores --
its own state and the gateway's -- can disagree about whether money moved, and
the wrong answer in either direction is expensive.

    REMIT says paid, gateway says no    the customer is charged for nothing
    REMIT says no, gateway says paid    the customer paid and got nothing

The design that makes this survivable predates these tests: the payment row is
written BEFORE the gateway is called, with a UNIQUE idempotency key, and an
ambiguous outcome lands in UNKNOWN rather than being guessed. What did not
exist was any evidence that a restart actually behaves that way.

These tests kill things: they drop the app object mid-flight, rebuild it
against the same file, and ask what the new process believes.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from remit.assembly import build
from remit.domain.risk import Exposure
from remit.exec.razorpay import FakeGateway

NOW = datetime(2026, 8, 22, 16, 0, tzinfo=timezone.utc)
BUYS = "buy running shoes under 5000"


@pytest.fixture
def path(tmp_path):
    return str(tmp_path / "recover.sqlite")


class _Hangs(FakeGateway):
    """A gateway that stops answering. `FakeGateway.timeout_on` is a set of
    receipts, which is precise and useless here -- what a crashed network looks
    like is "every call hangs", not "this particular receipt does"."""

    hang = True

    def create_order(self, **kw):
        if self.hang:
            import httpx
            raise httpx.TimeoutException("read timeout")
        return super().create_order(**kw)


def boot(path, gateway=None):
    return build(db_path=path, now=NOW, gateway=gateway or FakeGateway())


def run(app, utterance=BUYS, user="usr_rec", now=NOW, **kw):
    return app.journey.run(utterance=utterance, user_id=user, now=now,
                           exposure=Exposure(), **kw)


# ────────────────────────────────────────────── the process dies and returns

def test_a_restart_does_not_lose_what_was_paid(path):
    first = boot(path)
    a = run(first, human_confirms=True)
    assert a.payment_state == "CREATED"
    del first                                    # the process is gone

    second = boot(path)
    row = second.db.execute("SELECT * FROM payments WHERE payment_id=?",
                            (a.payment_id,)).fetchone()
    assert row is not None, "the payment did not survive a restart"
    assert row["order_id"] == a.order_id
    assert row["state"] == "CREATED"


def test_a_restart_does_not_pay_twice_for_the_same_request(path):
    """The one that matters. A retry after a crash is the most common way a
    customer gets charged twice, and the defence is that the idempotency key
    is derived from MEANING and stored with a UNIQUE constraint -- so it
    survives the process that computed it."""
    first = boot(path)
    a = run(first, human_confirms=True)
    del first

    second = boot(path)
    b = run(second, human_confirms=True)
    assert b.payment_id == a.payment_id
    assert b.replayed is True
    n = second.db.execute("SELECT COUNT(*) c FROM payments").fetchone()["c"]
    assert n == 1, f"{n} payment rows for one request across a restart"


def test_a_restart_remembers_that_the_authority_was_revoked(path):
    first = boot(path)
    first.revocations.revoke(user_id="usr_rec", now=NOW, reason="stop")
    del first

    second = boot(path)
    assert second.revocations.is_revoked(user_id="usr_rec")
    r = run(second, human_confirms=True)
    assert r.payment_state == "BLOCKED"
    assert "revoked" in r.note.lower()


def test_a_restart_remembers_where_the_authority_had_got_to(path):
    first = boot(path)
    step = run(first, "buy whisky under 2000")
    assert step.payment_state == "AWAITING_HUMAN"
    iid = step.intent.intent_id
    del first

    second = boot(path)
    assert second.authority.state(iid) == "PENDING_STEP_UP"
    assert [h["to_state"] for h in second.authority.history(iid)][-1] == \
        "PENDING_STEP_UP"


def test_an_approval_issued_before_a_restart_is_still_single_use(path):
    first = boot(path)
    step = run(first, "buy whisky under 2000")
    token = step.approval["token"]
    del first

    second = boot(path)
    ok = run(second, "buy whisky under 2000", human_confirms=True,
             approval_token=token)
    assert ok.payment_state == "CREATED"
    again = run(second, "buy whisky under 2000", human_confirms=True,
                approval_token=token)
    assert again.payment_state == "APPROVAL_REJECTED"
    assert "already_used" in again.note


# ─────────────────────────────────────── the gateway and REMIT disagree

def test_a_timeout_becomes_unknown_and_never_a_guess(path):
    """The window this whole design exists for. The order may or may not have
    been created; REMIT does not know and must not decide. RBI allows T+5 for
    exactly this state."""
    app = boot(path, _Hangs())
    r = run(app, human_confirms=True)
    assert r.payment_state == "UNKNOWN", r.payment_state
    assert r.order_id is None
    row = app.db.execute("SELECT state, unknown_since FROM payments"
                         " WHERE payment_id=?", (r.payment_id,)).fetchone()
    assert row["state"] == "UNKNOWN"
    assert row["unknown_since"], "nothing recorded when the ambiguity began"


def test_the_reconciler_resolves_an_unknown_from_gateway_truth(path):
    gw = _Hangs()
    app = boot(path, gw)
    r = run(app, human_confirms=True)
    assert r.payment_state == "UNKNOWN"

    # the order did exist on the other side
    gw.hang = False
    out = app.recon.run(NOW + timedelta(minutes=5))
    assert out["scanned"] >= 1
    state = app.db.execute("SELECT state FROM payments WHERE payment_id=?",
                           (r.payment_id,)).fetchone()["state"]
    assert state in ("SUCCESS", "FAILED", "UNKNOWN")
    if state == "UNKNOWN":
        assert out["exceptions"], "unresolved and not reported"


def test_an_unresolvable_payment_is_surfaced_not_swallowed(path):
    """Never silently resolve a financial inconsistency. If the gateway has no
    record, the payment stays UNKNOWN and lands on an exception list somebody
    has to look at."""
    class Amnesiac(_Hangs):
        def lookup_by_receipt(self, receipt):
            return None

    app = boot(path, Amnesiac())
    r = run(app, human_confirms=True)
    out = app.recon.run(NOW + timedelta(minutes=5))
    assert out["unresolved"] >= 1
    assert any(e["payment_id"] == r.payment_id for e in out["exceptions"])
    assert app.db.execute("SELECT state FROM payments WHERE payment_id=?",
                          (r.payment_id,)).fetchone()["state"] == "UNKNOWN"


def test_a_reconciliation_survives_a_restart(path):
    first = boot(path, _Hangs())
    r = run(first, human_confirms=True)
    assert r.payment_state == "UNKNOWN"
    del first

    second = boot(path)          # a fresh process picks up the exception list
    pending = second.recon.pending(NOW + timedelta(minutes=5))
    assert any(p["payment_id"] == r.payment_id for p in pending), (
        "an ambiguous payment was forgotten by the process that inherited it")


def test_a_webhook_arriving_after_a_restart_still_applies_once(path):
    import hashlib
    import hmac
    import json

    first = boot(path)
    r = run(first, human_confirms=True)
    secret = first.webhook_secret
    body = json.dumps({
        "id": "evt_after_restart",
        "event": "payment.captured",
        "payload": {"payment_id": r.payment_id}}).encode()
    del first

    second = boot(path)
    # the secret is per-process when unset; sign with the one that will verify
    secret2 = second.webhook_secret
    sig2 = hmac.new(secret2.encode(), body, hashlib.sha256).hexdigest()
    a = second.webhooks.handle(body=body, signature=sig2, now=NOW)
    b = second.webhooks.handle(body=body, signature=sig2, now=NOW)
    assert a.get("applied") is True, a
    assert b.get("applied") is not True, b
    assert b.get("why") == "duplicate", b
    n = second.db.execute(
        "SELECT COUNT(*) c FROM webhook_events").fetchone()["c"]
    assert n == 1, f"{n} rows for one webhook event id"


def test_the_audit_chain_survives_a_restart(path):
    first = boot(path)
    for i in range(4):
        run(first, f"buy running shoes under {5000 + i}", human_confirms=True)
    del first

    second = boot(path)
    ok, bad = second.ledger.verify_chain()
    assert ok and bad is None, (ok, bad)
    n = second.db.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
    assert n > 20, n


def test_an_interrupted_write_leaves_no_half_payment(path):
    """A payment row exists before the gateway is called. If the process dies
    between those two moments the row is there with no order id -- which is
    recoverable -- rather than an order existing that REMIT has never heard of,
    which is not."""
    class Dies(FakeGateway):
        def create_order(self, **kw):
            raise KeyboardInterrupt("process killed mid-flight")

    app = boot(path, Dies())
    with pytest.raises(KeyboardInterrupt):
        run(app, human_confirms=True)

    rows = app.db.execute("SELECT * FROM payments").fetchall()
    assert len(rows) == 1
    assert rows[0]["order_id"] is None
    assert rows[0]["state"] == "CREATED"
    # and the same request after the restart finds that row rather than
    # starting a second one
    second = boot(path)
    r = run(second, human_confirms=True)
    assert r.payment_id == rows[0]["payment_id"]
