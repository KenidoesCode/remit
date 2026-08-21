from datetime import timedelta

from remit.exec.razorpay import FakeGateway
from remit.gateway import Gateway
from remit.models import Verdict
from tests.consts import T0


def _gw(ledger, policy, catalog, compiler, fake):
    return Gateway(ledger=ledger, policy=policy, catalog=catalog,
                   compiler=compiler, gw=fake)


def test_happy_path_creates_one_order(ledger, policy, catalog, compiler,
                                      remit, spend):
    fake = FakeGateway()
    g = _gw(ledger, policy, catalog, compiler, fake)
    out = g.handle(utterance="usual groceries", merchant_id="mch_grocer",
                   remit=remit, spend=spend, now=T0)
    assert out.decision.verdict is Verdict.ALLOW
    assert out.order is not None
    assert len([c for c in fake.calls if c[0] == "create_order"]) == 1


def test_retry_storm_does_not_double_debit(ledger, policy, catalog, compiler,
                                           remit, spend):
    """Agents retry. Chat UIs repeat. MRTR adds explicit client retries."""
    fake = FakeGateway()
    g = _gw(ledger, policy, catalog, compiler, fake)
    outs = [g.handle(utterance="usual groceries", merchant_id="mch_grocer",
                     remit=remit, spend=spend, now=T0) for _ in range(5)]
    created = [c for c in fake.calls if c[0] == "create_order"]
    assert len(created) == 1, "double debit"
    assert sum(1 for o in outs if o.replayed) == 4


def test_timeout_after_create_enters_ambiguous_not_retry(
        ledger, policy, catalog, compiler, remit, spend):
    """RBI allows T+5 for 'debited but merchant confirmation not received'.
    A system with no AMBIGUOUS state either double-charges or wrongly refunds."""
    from remit.exec.idempotency import idempotency_key, receipt_for
    from remit.intent.compiler import build_intent
    from remit.models import IntentItem
    i = build_intent(utterance="usual groceries", merchant_id="mch_grocer",
                     catalog=catalog,
                     items=[IntentItem(item_id="atta_5kg", qty=1),
                            IntentItem(item_id="milk_1l", qty=2)],
                     category="grocery", raw_confidence=0.94)
    key = idempotency_key(remit_id=remit.remit_id, intent_hash=i.intent_hash,
                          envelope_epoch=remit.envelope_epoch,
                          revocation_epoch=remit.revocation_epoch)
    fake = FakeGateway(timeout_on={receipt_for(key)})
    g = _gw(ledger, policy, catalog, compiler, fake)
    out = g.handle(utterance="usual groceries", merchant_id="mch_grocer",
                   remit=remit, spend=spend, now=T0)
    assert out.order is None
    assert "AMBIGUOUS" in out.note
    kinds = [k for _, _, k, _, _ in ledger.trace(out.trace_id)]
    assert "EXCEPTION" in kinds

    # And the retry must NOT create a second order.
    out2 = g.handle(utterance="usual groceries", merchant_id="mch_grocer",
                    remit=remit, spend=spend, now=T0 + timedelta(seconds=5))
    assert out2.replayed is True
    assert len([c for c in fake.calls if c[0] == "create_order"]) == 1


def test_abstention_is_not_an_error(ledger, policy, catalog, compiler,
                                    remit, spend):
    fake = FakeGateway()
    g = _gw(ledger, policy, catalog, compiler, fake)
    out = g.handle(utterance="something not in the script",
                   merchant_id="mch_grocer", remit=remit, spend=spend, now=T0)
    assert out.intent is None
    assert "abstained" in out.note
    assert fake.calls == []


def test_step_up_blocks_until_human_confirms(ledger, policy, catalog, compiler,
                                             remit, spend):
    fake = FakeGateway()
    g = _gw(ledger, policy, catalog, compiler, fake)
    out = g.handle(utterance="das hazaar ka recharge", merchant_id="mch_grocer",
                   remit=remit, spend=spend, now=T0)
    assert out.order is None
    assert fake.calls == []
