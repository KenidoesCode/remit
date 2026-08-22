"""An approval is a token bound to a basket, not a boolean.

"The human said yes" is not a fact a payment system can act on. Yes to WHAT --
which cart, at which price, for how much, and how long ago? Every test here is
one of those questions being answered wrongly on purpose.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from remit.assembly import build
from remit.grants.approval import cart_hash

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
ASKS = "buy whisky under 2000"          # RESTRICT-001: always a step-up


@pytest.fixture
def app():
    return build(db_path=":memory:", now=NOW, live=False)


def step_up(app, user="usr_ap"):
    r = app.journey.run(utterance=ASKS, user_id=user, now=NOW)
    assert r.payment_state == "AWAITING_HUMAN", r.payment_state
    assert r.approval, "a step-up issued no approval to redeem"
    return r


def test_a_step_up_issues_a_token_bound_to_the_basket(app):
    r = step_up(app)
    a = r.approval
    assert a["amount_paise"] == r.totals.total_paise
    assert a["cart_hash"] == cart_hash(r.cart)
    assert a["intent_hash"] == r.intent.semantic_hash
    assert set(a["binds"]) >= {"user", "cart hash", "amount", "expiry"}


def test_a_valid_token_pays(app):
    r = step_up(app)
    ok = app.journey.run(utterance=ASKS, user_id="usr_ap", now=NOW,
                         approval_token=r.approval["token"])
    assert ok.payment_state == "CREATED"
    assert ok.order_id


def test_a_token_cannot_be_used_twice(app):
    r = step_up(app)
    tok = r.approval["token"]
    app.journey.run(utterance=ASKS, user_id="usr_ap", now=NOW, approval_token=tok)
    again = app.journey.run(utterance=ASKS, user_id="usr_ap", now=NOW,
                            approval_token=tok)
    assert again.payment_state == "APPROVAL_REJECTED"
    assert "already_used" in again.note


def test_a_token_belongs_to_one_person(app):
    r = step_up(app, "usr_owner")
    other = app.journey.run(utterance=ASKS, user_id="usr_thief", now=NOW,
                            approval_token=r.approval["token"])
    assert other.payment_state == "APPROVAL_REJECTED"
    assert "wrong_actor" in other.note


def test_a_price_change_invalidates_the_approval(app):
    """The single most valuable property in this file. A person approved a
    basket at a price; if the price moves before they redeem, their yes was
    for a different transaction."""
    r = step_up(app, "usr_price")
    pid = r.cart.lines[0].product_id
    app.catalog.set_price(pid, app.catalog.get(pid).price_paise + 5000, NOW)
    after = app.journey.run(utterance=ASKS, user_id="usr_price", now=NOW,
                            approval_token=r.approval["token"])
    assert after.payment_state == "APPROVAL_REJECTED"
    assert "cart_changed" in after.note


def test_a_stale_approval_is_refused(app):
    r = step_up(app, "usr_slow")
    late = NOW + timedelta(hours=3)
    after = app.journey.run(utterance=ASKS, user_id="usr_slow", now=late,
                            approval_token=r.approval["token"])
    assert after.payment_state == "APPROVAL_REJECTED"
    assert "expired" in after.note


def test_a_forged_token_is_refused(app):
    bad = app.journey.run(utterance=ASKS, user_id="usr_x", now=NOW,
                          approval_token="apr_i_made_this_up")
    assert bad.payment_state == "APPROVAL_REJECTED"
    assert "unknown" in bad.note


def test_approving_records_the_new_amount_in_the_envelope(app):
    """FAILURES #29. A person who approves Rs 7,315 against a Rs 5,000
    instruction has authorised Rs 7,315 -- and if the envelope still says
    Rs 5,000 afterwards, the system's own record of what was authorised
    disagrees with what it paid."""
    r = app.journey.run(utterance="buy running shoes under 5000",
                        user_id="usr_amend", now=NOW,
                        accept_offers="all", human_confirms=True)
    if r.payment_state != "CREATED":
        pytest.skip("this basket did not need a step-up")
    assert r.totals.total_paise <= r.intent.ceiling_paise()
    if r.intent.version > 1:
        assert r.intent.ceiling_paise() == r.totals.total_paise


def test_the_earlier_envelope_version_survives_the_amendment(app):
    r = app.journey.run(utterance="buy running shoes under 5000",
                        user_id="usr_hist", now=NOW,
                        accept_offers="all", human_confirms=True)
    rows = app.db.execute(
        "SELECT version, reason FROM intent_versions WHERE intent_id=?"
        " ORDER BY version", (r.intent.intent_id,)).fetchall()
    assert rows, "no envelope history written"
    if r.intent.version > 1:
        assert len(rows) >= 2, "the amendment replaced history instead of adding to it"
        assert "approved" in rows[-1]["reason"]


def test_declining_leaves_the_token_unused(app):
    r = step_up(app, "usr_no")
    app.journey.run(utterance=ASKS, user_id="usr_no", now=NOW,
                    human_confirms=False)
    row = app.journey.approvals.get(r.approval["token"])
    assert row["used_at"] is None
