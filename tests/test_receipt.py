"""The authorization receipt is a projection, and these tests hold it to that.

The receipt in `remit/receipt.py` writes nothing and decides nothing -- it reads
the decision, payment, intent, authority and event records another part of the
system already produced, and joins them into one view. So the thing worth
testing is not "does it compute the right verdict" (the policy engine's tests
own that) but "does it report, faithfully and without inventing anything, what
those records actually say" -- across AUTO, STEP_UP, DENY, revocation, a
duplicate request, and a tampered chain.

The one property that would make the receipt dangerous is if it could claim a
decision or a payment that the underlying tables do not contain. Several of the
assertions below exist only to pin that shut.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from remit.assembly import build
from remit.exec.razorpay import FakeGateway
from remit.policy.authorize import Verdict
from remit.receipt import build_receipt, render_text

NOW = datetime(2026, 8, 22, 19, 0, tzinfo=timezone.utc)


@pytest.fixture
def app():
    return build(now=NOW, gateway=FakeGateway())


def _receipt(app, r, user="u"):
    rec = build_receipt(app, r.correlation_id, user)
    assert rec is not None, "a decision was recorded but no receipt was built"
    return rec


# ─────────────────────────────────────────────────────────────── AUTO

def test_an_auto_purchase_produces_a_receipt_that_shows_the_money_moved(app):
    r = app.journey.run(utterance="buy a yoga mat under 2000", user_id="u",
                        now=NOW, human_confirms=True)
    assert r.authorization.verdict is Verdict.AUTO
    rec = _receipt(app, r)

    assert rec["decision"]["verdict"] == "AUTO"
    assert rec["execution"]["money_moved"] is True
    # The order id in the receipt is the one the executor actually got back,
    # not a placeholder: it matches the payment row.
    assert rec["execution"]["order_id"] == r.order_id
    assert rec["execution"]["mode"] == "razorpay_test"
    assert rec["intent"]["text"] == "buy a yoga mat under 2000"
    assert rec["receipt_id"] == f"rcpt_{r.correlation_id}"
    # Provenance for the reviewer: every field says nothing the record doesn't.
    assert rec["authority"]["ceiling"]["paise"] == 2000_00


# ─────────────────────────────────────────────────────────────── STEP_UP

def test_a_step_up_receipt_says_a_human_must_decide_and_no_money_moved(app):
    r = app.journey.run(utterance="buy running shoes under 5000", user_id="u",
                        now=NOW, human_confirms=None, inject={"shipping": 99900})
    assert r.authorization.verdict is not Verdict.AUTO
    rec = _receipt(app, r)

    # Whatever the exact non-AUTO verdict, the receipt must not claim a payment.
    assert rec["execution"]["money_moved"] is False
    assert rec["execution"]["order_id"] is None
    assert rec["execution"]["state"] == "NOT_EXECUTED"
    if rec["decision"]["verdict"] == "STEP_UP":
        assert rec["decision"]["requires_human"] is True


# ─────────────────────────────────────────────────────────────── DENY

def test_a_denied_purchase_receipt_names_the_clause_and_moves_nothing(app):
    r = app.journey.run(utterance="buy running shoes under 5000", user_id="u",
                        now=NOW, human_confirms=True, inject={"revoked": True})
    assert r.authorization.verdict is Verdict.DENY
    rec = _receipt(app, r)

    assert rec["decision"]["verdict"] == "DENY"
    assert "AUTH-003" in rec["decision"]["failed_clauses"]
    assert rec["execution"]["money_moved"] is False
    assert rec["execution"]["order_id"] is None
    # The reason travels with the receipt, so a denial explains itself.
    assert rec["decision"]["reason"]


def test_no_purchase_authority_is_a_denial_with_an_explained_receipt(app):
    r = app.journey.run(utterance="show me running shoes under 5000",
                        user_id="u", now=NOW, human_confirms=True)
    assert r.authorization.verdict is Verdict.DENY
    rec = _receipt(app, r)
    assert rec["decision"]["verdict"] == "DENY"
    assert rec["execution"]["money_moved"] is False


# ─────────────────────────────────────────────────────── revocation

def test_the_receipt_reflects_a_revoked_authority(app):
    # First a real decision, then a real revocation through the store the
    # receipt reads -- not the policy-time `inject={"revoked": True}` simulation,
    # which triggers AUTH-003 without writing a revocation record. The receipt
    # must report the store's truth, so the revocation has to be real.
    r = app.journey.run(utterance="buy running shoes under 5000", user_id="u",
                        now=NOW, human_confirms=True)
    app.journey.revocations.revoke(user_id="u", now=NOW, scope="principal")
    rec = _receipt(app, r)
    assert rec["revocation"]["revoked"] is True
    assert rec["revocation"]["revoked_at"] is not None


# ─────────────────────────────────────────────────────── idempotency

def test_a_duplicate_request_does_not_create_a_second_financial_effect(app):
    outs = [app.journey.run(utterance="buy running shoes under 5000",
                            user_id="u", now=NOW, human_confirms=True)
            for _ in range(5)]
    orders = [c for c in app.gateway.calls if c[0] == "create_order"]
    assert len(orders) == 1, "the rail was hit more than once for one request"

    # Every retry shares the correlation id, so every receipt points at the one
    # payment. The receipt cannot be used to make five requests look like five
    # purchases.
    rec = _receipt(app, outs[0])
    assert rec["execution"]["money_moved"] is True
    order_ids = {build_receipt(app, o.correlation_id, "u")["execution"]["order_id"]
                 for o in outs}
    assert order_ids == {rec["execution"]["order_id"]}, "retries diverged on order id"


# ─────────────────────────────────────────────────────── verification

def test_the_receipt_reports_the_chain_intact_when_it_is(app):
    r = app.journey.run(utterance="buy a yoga mat under 2000", user_id="u",
                        now=NOW, human_confirms=True)
    rec = _receipt(app, r)
    assert rec["audit"]["chain_intact"] is True
    assert rec["self_reported_chain"] == "intact"


def test_a_tampered_event_makes_the_receipt_report_a_broken_chain(app):
    r = app.journey.run(utterance="buy a yoga mat under 2000", user_id="u",
                        now=NOW, human_confirms=True)
    # Edit an event payload after the fact -- the exact attack the hash chain
    # exists to detect.
    app.ledger.db.execute("UPDATE events SET payload='{}' WHERE seq=3")
    rec = _receipt(app, r)
    assert rec["audit"]["chain_intact"] is False
    assert rec["self_reported_chain"] == "BROKEN"
    assert rec["audit"]["first_bad_seq"] == 3


# ─────────────────────────────────────────────────────── scoping & safety

def test_a_receipt_is_scoped_to_its_principal(app):
    r = app.journey.run(utterance="buy a yoga mat under 2000", user_id="alice",
                        now=NOW, human_confirms=True)
    # Bob may not read Alice's receipt: it carries the sentence she typed.
    assert build_receipt(app, r.correlation_id, "bob") is None
    assert build_receipt(app, r.correlation_id, "alice") is not None


def test_an_unknown_correlation_id_has_no_receipt(app):
    assert build_receipt(app, "cor_does_not_exist", "u") is None


def test_the_receipt_carries_no_secret_or_credential_shaped_field(app):
    r = app.journey.run(utterance="buy a yoga mat under 2000", user_id="u",
                        now=NOW, human_confirms=True)
    rec = _receipt(app, r)
    import json
    blob = json.dumps(rec).lower()
    for forbidden in ("secret", "api_key", "apikey", "private_key",
                      "password", "rzp_test_", "rzp_live_", "authorization:"):
        assert forbidden not in blob, f"receipt leaked a {forbidden!r}-shaped field"


# ─────────────────────────────────────────────────────── human rendering

def test_the_text_receipt_states_the_decision_and_how_to_verify(app):
    r = app.journey.run(utterance="buy a yoga mat under 2000", user_id="u",
                        now=NOW, human_confirms=True)
    text = render_text(_receipt(app, r))
    assert "REMIT AUTHORIZATION RECEIPT" in text
    assert "AUTO" in text
    assert "buy a yoga mat under 2000" in text
    assert f"remit receipt verify {r.correlation_id}" in text
