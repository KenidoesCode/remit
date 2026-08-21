"""A deliberate attempt to break the invariants from the outside.

Written after the build, in the posture of a reviewer who wants the system to
fail rather than the author who wants it to pass.
"""
from datetime import datetime, timedelta, timezone

import pytest

from remit.assembly import build
from remit.domain.cart import line_from, new_cart, price_cart
from remit.domain.drift import compute_drift
from remit.domain.intent import amend, new_intent
from remit.domain.risk import Exposure
from remit.exec.razorpay import FakeGateway
from remit.policy.authorize import Verdict, authorize

NOW = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def app():
    return build(now=NOW, gateway=FakeGateway())


def test_an_expired_intent_can_never_authorise(app):
    env = new_intent(user_id="u", utterance="buy shoes under 5000", now=NOW,
                     category="running shoes", product_terms=["running shoes"],
                     max_price_paise=500000, purchase_authority=True,
                     ttl_minutes=30)
    later = NOW + timedelta(hours=2)
    assert env.expired(later)
    p = app.catalog.search(category="running shoes", terms=["running shoes"],
                           max_price_paise=400000)[0]
    cart = new_cart(env.intent_id, 1, app.catalog.version(), NOW)
    cart.add(line_from(p, 1, "primary", "intent"))
    totals = price_cart(cart, app.catalog)
    drift = compute_drift(env=env, cart=cart, totals=totals,
                          catalog_version=app.catalog.version())
    from remit.domain.risk import assess
    risk = assess(env=env, total_paise=totals.total_paise, drift=drift,
                  exposure=Exposure(), now=later)
    auth = authorize(env=env, cart=cart, totals=totals, drift=drift, risk=risk,
                     exposure=Exposure(), policy=app.policy, now=later,
                     catalog_version=app.catalog.version())
    assert auth.verdict is Verdict.DENY
    assert "AUTH-002" in auth.failed


def test_amending_an_intent_creates_a_new_version_and_keeps_the_old(app):
    env = new_intent(user_id="u", utterance="x", now=NOW, category="running shoes",
                     max_price_paise=500000, purchase_authority=True)
    v2, reason = amend(env, now=NOW, reason="human raised the limit",
                       max_price_paise=900000)
    assert env.version == 1 and v2.version == 2
    assert env.max_price_paise == 500000, "history must not be mutated"
    assert v2.envelope_hash != env.envelope_hash


def test_quantity_inflation_is_caught_by_drift(app):
    """If anything upstream inflated the quantity, drift must see it."""
    env = new_intent(user_id="u", utterance="x", now=NOW, category="running shoes",
                     max_price_paise=500000, quantity=1, purchase_authority=True)
    p = app.catalog.search(category="running shoes", max_price_paise=400000)[0]
    cart = new_cart(env.intent_id, 1, app.catalog.version(), NOW)
    cart.add(line_from(p, 9, "primary", "intent"))
    d = compute_drift(env=env, cart=cart, totals=price_cart(cart, app.catalog),
                      catalog_version=app.catalog.version())
    assert d.dimensions["quantity"] == 1.0
    assert any("quantity" in r for r in d.reasons)


def test_drift_cannot_be_gamed_by_omitting_a_constraint(app):
    """Removing a constraint from the envelope must NOT produce zero drift.
    Unstated is `not_evaluable`, never compliant."""
    env = new_intent(user_id="u", utterance="x", now=NOW, purchase_authority=True)
    p = app.catalog.search(category="personal care")[0]
    cart = new_cart(env.intent_id, 1, app.catalog.version(), NOW)
    cart.add(line_from(p, 1, "primary", "intent"))
    d = compute_drift(env=env, cart=cart, totals=price_cart(cart, app.catalog),
                      catalog_version=app.catalog.version())
    assert "total" in d.not_evaluable
    assert "category" in d.not_evaluable
    assert d.score == 0.0, "score is 0 but the reviewer is told WHY it is 0"


def test_no_ceiling_still_cannot_exceed_the_transaction_cap(app):
    """A human who names no amount is not handing over an unbounded cheque."""
    r = app.journey.run(utterance="buy a cabin roller", user_id="u", now=NOW,
                        human_confirms=True)
    if r.totals:
        assert r.totals.total_paise <= app.policy.limits["max_transaction_paise"]


def test_session_exposure_stops_a_run_of_purchases(app):
    made = 0
    for i in range(30):
        r = app.journey.run(utterance=f"buy running shoes under 5000 #{i}",
                            user_id="u", now=NOW,
                            exposure=Exposure(session_paise=made),
                            human_confirms=True)
        if r.totals and r.payment_state in ("CREATED", "SUCCESS"):
            made += r.totals.total_paise
        if r.authorization and "EXPO-001" in r.authorization.failed:
            break
    assert made <= app.policy.limits["session_exposure_paise"]


def test_policy_is_pure_under_repetition(app):
    """Same inputs, same decision, every time -- this is what the frontier
    replay depends on."""
    outs = []
    for _ in range(6):
        a = build(now=NOW, gateway=FakeGateway())
        r = a.journey.run(utterance="buy running shoes under 5000", user_id="u",
                          now=NOW, human_confirms=None)
        outs.append((r.authorization.verdict, r.drift.score,
                     r.totals.total_paise, sorted(r.authorization.failed)))
    assert len(set(map(str, outs))) == 1


def test_a_denied_journey_leaves_no_payment_row(app):
    r = app.journey.run(utterance="show me running shoes under 5000", user_id="u",
                        now=NOW, human_confirms=True)
    assert r.authorization.verdict is Verdict.DENY
    n = app.db.execute("SELECT COUNT(*) c FROM payments").fetchone()["c"]
    assert n == 0


def test_step_up_without_confirmation_leaves_no_payment_row(app):
    r = app.journey.run(utterance="buy running shoes under 5000", user_id="u",
                        now=NOW, human_confirms=None, inject={"shipping": 99900})
    assert r.authorization.verdict is Verdict.STEP_UP
    assert app.db.execute("SELECT COUNT(*) c FROM payments").fetchone()["c"] == 0


def test_every_executed_payment_has_a_full_clause_chain(app):
    """The gate, expressed as a property of the database rather than a run."""
    for i in range(12):
        app.journey.run(utterance=f"buy running shoes under {3000 + i * 200}",
                        user_id=f"u{i}", now=NOW, human_confirms=True)
    import json
    for row in app.db.execute("SELECT policy, verdict FROM decisions"):
        pol = json.loads(row["policy"])
        if row["verdict"] == "AUTO":
            assert pol["failed"] == [], f"AUTO with failed clauses: {pol['failed']}"
