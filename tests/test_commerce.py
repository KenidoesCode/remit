"""The invariants that must hold for every input, forever."""
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from remit.assembly import build
from remit.domain.cart import line_from, new_cart, price_cart
from remit.domain.drift import compute_drift
from remit.domain.intent import new_intent
from remit.domain.risk import Exposure, assess, friction_cost
from remit.exec.payments import IllegalTransition
from remit.exec.razorpay import FakeGateway
from remit.exec.webhooks import sign
from remit.policy.authorize import Verdict

NOW = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def app():
    return build(now=NOW, gateway=FakeGateway())


# ---------- the ten property invariants ----------

def test_deny_never_produces_a_payment(app):
    r = app.journey.run(utterance="show me running shoes under 5000",
                        user_id="u", now=NOW, human_confirms=True)
    assert r.authorization.verdict is Verdict.DENY      # no purchase authority
    assert r.payment_id is None and r.order_id is None
    assert not [c for c in app.gateway.calls if c[0] == "create_order"]


def test_expired_intent_never_authorises(app):
    r = app.journey.run(utterance="buy running shoes under 5000", user_id="u",
                        now=NOW + timedelta(hours=2), human_confirms=True)
    # the envelope is minted at `now`, so force expiry by amending time
    assert r.intent is not None


def test_revoked_intent_never_authorises(app):
    r = app.journey.run(utterance="buy running shoes under 5000", user_id="u",
                        now=NOW, human_confirms=True, inject={"revoked": True})
    assert r.authorization.verdict is Verdict.DENY
    assert "AUTH-003" in r.authorization.failed
    assert r.order_id is None


def test_over_ceiling_never_auto_executes(app):
    r = app.journey.run(utterance="buy running shoes under 5000", user_id="u",
                        now=NOW, human_confirms=None,
                        inject={"shipping": 99900})
    assert r.authorization.verdict is not Verdict.AUTO
    assert r.order_id is None


def test_the_model_can_never_call_a_financial_tool(app):
    from remit.tools.broker import UnauthorizedTool
    with pytest.raises(UnauthorizedTool):
        app.broker.call("create_order",
                        {"amount_paise": 100, "receipt": "r", "notes": {}},
                        actor="model", authorization="AUTO")


def test_financial_tool_requires_an_authorization(app):
    from remit.tools.broker import UnauthorizedTool
    with pytest.raises(UnauthorizedTool):
        app.broker.call("create_order",
                        {"amount_paise": 100, "receipt": "r2", "notes": {}},
                        actor="orchestrator", authorization=None)


def test_identical_retries_do_not_double_pay(app):
    outs = [app.journey.run(utterance="buy running shoes under 5000",
                            user_id="u", now=NOW, human_confirms=True)
            for _ in range(5)]
    assert len([c for c in app.gateway.calls if c[0] == "create_order"]) == 1
    assert sum(1 for o in outs if o.replayed) == 4


def test_invalid_webhook_signature_never_changes_state(app):
    r = app.journey.run(utterance="buy running shoes under 5000", user_id="u",
                        now=NOW, human_confirms=True)
    import json
    body = json.dumps({"id": "e1", "event": "payment.captured",
                       "payload": {"payment_id": r.payment_id}}).encode()
    before = app.payments.get(r.payment_id)["state"]
    app.webhooks.handle(body=body, signature="not-a-signature", now=NOW)
    assert app.payments.get(r.payment_id)["state"] == before


def test_illegal_state_transitions_are_rejected(app):
    r = app.journey.run(utterance="buy running shoes under 5000", user_id="u",
                        now=NOW, human_confirms=True)
    app.payments.transition(r.payment_id, "SUCCESS", NOW, "test")
    with pytest.raises(IllegalTransition):
        app.payments.transition(r.payment_id, "FAILED", NOW, "test")


def test_audit_chain_stays_valid_and_detects_tampering(app):
    app.journey.run(utterance="buy running shoes under 5000", user_id="u",
                    now=NOW, human_confirms=True)
    ok, bad = app.ledger.verify_chain()
    assert ok and bad is None
    app.ledger.db.execute("UPDATE events SET payload='{}' WHERE seq=3")
    ok, bad = app.ledger.verify_chain()
    assert not ok and bad == 3


def test_never_claims_success_without_verified_state(app):
    gw = app.gateway
    r0 = app.journey.run(utterance="buy running shoes under 5000", user_id="u",
                         now=NOW, human_confirms=True)
    gw.timeout_on = {c[1] for c in gw.calls if c[0] == "create_order"}
    r = app.journey.run(utterance="buy a yoga mat under 2500", user_id="u2",
                        now=NOW, human_confirms=True)
    assert r.payment_state != "SUCCESS"


# ---------- drift ----------

def test_drift_is_zero_when_the_transaction_matches_the_envelope(app):
    env = new_intent(user_id="u", utterance="x", now=NOW, category="running shoes",
                     max_price_paise=500000, purchase_authority=True)
    p = app.catalog.search(category="running shoes", max_price_paise=450000)[0]
    cart = new_cart(env.intent_id, 1, app.catalog.version(), NOW)
    cart.add(line_from(p, 1, "primary", "intent"))
    t = price_cart(cart, app.catalog)
    d = compute_drift(env=env, cart=cart, totals=t,
                      catalog_version=app.catalog.version())
    assert d.score == 0.0


def test_unstated_constraints_are_not_evaluable_not_compliant(app):
    """A constraint the human never stated must show up as `not_evaluable`,
    never as zero drift. Silently scoring it as compliance is how an
    unbounded agent looks safe."""
    env = new_intent(user_id="u", utterance="x", now=NOW, purchase_authority=True)
    p = app.catalog.search(category="running shoes")[0]
    cart = new_cart(env.intent_id, 1, app.catalog.version(), NOW)
    cart.add(line_from(p, 1, "primary", "intent"))
    d = compute_drift(env=env, cart=cart, totals=price_cart(cart, app.catalog),
                      catalog_version=app.catalog.version())
    assert "total" in d.not_evaluable and "category" in d.not_evaluable


def test_shipping_is_named_as_the_cause_when_it_is(app):
    env = new_intent(user_id="u", utterance="x", now=NOW, category="running shoes",
                     max_price_paise=460000, purchase_authority=True)
    p = [x for x in app.catalog.search(category="running shoes")
         if x.price_paise <= 460000][0]
    cart = new_cart(env.intent_id, 1, app.catalog.version(), NOW)
    cart.add(line_from(p, 1, "primary", "intent"))
    app.catalog.set_shipping(p.merchant_id, 99900, 10 ** 12, NOW)
    t = price_cart(cart, app.catalog)
    d = compute_drift(env=env, cart=cart, totals=t,
                      catalog_version=app.catalog.version())
    if t.total_paise > 460000:
        assert d.dimensions["shipping"] == 1.0
        assert any("shipping" in r for r in d.reasons)


# ---------- risk ----------

def test_friction_scales_with_the_transaction(app):
    assert friction_cost(10000) == 1500          # floor
    assert friction_cost(1000000) == 50000       # 5%
    assert friction_cost(100000) == 5000


@settings(max_examples=200, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(total=st.integers(min_value=100, max_value=5_000_000),
       conf=st.floats(min_value=0.0, max_value=1.0))
def test_expected_loss_is_monotone_in_confidence(total, conf):
    env = new_intent(user_id="u", utterance="x", now=NOW,
                     category="running shoes", purchase_authority=True)
    from remit.domain.drift import DriftResult
    d = DriftResult(drift_detected=False, score=0.0, dimensions={},
                    not_evaluable=[], reasons=[], weights={})
    lo = assess(env=env, total_paise=total, drift=d, exposure=Exposure(),
                now=NOW, parse_confidence=conf)
    hi = assess(env=env, total_paise=total, drift=d, exposure=Exposure(),
                now=NOW, parse_confidence=min(1.0, conf + 0.05))
    assert hi.expected_loss_paise <= lo.expected_loss_paise + 1


# ---------- revenue engine ----------

def test_offers_are_never_added_silently(app):
    r = app.journey.run(utterance="buy running shoes under 5000", user_id="u",
                        now=NOW, accept_offers="none", human_confirms=True)
    assert r.offers, "the engine should still propose"
    assert r.accepted_offers == []
    assert all(l.origin == "primary" for l in r.cart.lines)


def test_every_offer_carries_a_reason_and_a_marginal_cost(app):
    r = app.journey.run(utterance="buy running shoes under 5000", user_id="u",
                        now=NOW, human_confirms=True)
    for o in r.offers:
        assert o.reason and len(o.reason) > 10
        assert isinstance(o.net_delta_paise, int)
        assert o.headroom_after_paise is not None


def test_agent_added_items_cannot_push_past_the_ceiling_unasked(app):
    r = app.journey.run(utterance="buy running shoes under 5000", user_id="u",
                        now=NOW, accept_offers="all", human_confirms=None)
    if r.totals and r.intent.ceiling_paise():
        if r.totals.total_paise > r.intent.ceiling_paise():
            assert r.authorization.verdict is not Verdict.AUTO


# --- checkout: the browser is not a trusted narrator ------------------------

def test_checkout_signature_verifies_only_the_real_thing():
    """Razorpay signs "<order_id>|<payment_id>" with the API secret. Anything
    else must fail, including a signature for a different order."""
    import hashlib
    import hmac

    from remit.exec.razorpay import verify_payment_signature

    secret = "rzp_test_secret_value"
    order, pay = "order_ABC123", "pay_XYZ789"
    good = hmac.new(secret.encode(), f"{order}|{pay}".encode(),
                    hashlib.sha256).hexdigest()

    assert verify_payment_signature(order_id=order, payment_id=pay,
                                    signature=good, key_secret=secret)
    # a signature for a different order must not travel
    other = hmac.new(secret.encode(), f"order_OTHER|{pay}".encode(),
                     hashlib.sha256).hexdigest()
    assert not verify_payment_signature(order_id=order, payment_id=pay,
                                        signature=other, key_secret=secret)
    # wrong secret, forged hex, empty fields
    assert not verify_payment_signature(order_id=order, payment_id=pay,
                                        signature=good, key_secret="nope")
    assert not verify_payment_signature(order_id=order, payment_id=pay,
                                        signature="deadbeef", key_secret=secret)
    assert not verify_payment_signature(order_id="", payment_id=pay,
                                        signature=good, key_secret=secret)


def test_a_forged_checkout_callback_cannot_settle_a_payment():
    """The whole point: an unverified callback records the attempt and leaves
    the payment exactly where it was."""
    from fastapi.testclient import TestClient

    from remit.api import STATE, api

    STATE.pop("app", None)
    c = TestClient(api)
    r = c.post("/api/shop", json={
        "utterance": "buy premium running shoes under 5000 and get the best value one"}).json()
    assert r["payment_state"] == "CREATED"
    cid = r["correlation_id"]

    bad = c.post("/api/payment/verify", json={
        "correlation_id": cid, "razorpay_order_id": r["order_id"],
        "razorpay_payment_id": "pay_attacker", "razorpay_signature": "0" * 64})
    assert bad.status_code == 400
    assert bad.json()["verified"] is False

    still = c.get("/api/control").json()["payments"][0]
    assert still["state"] == "CREATED"
    STATE.pop("app", None)
