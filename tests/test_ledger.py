from datetime import timedelta

from remit.ledger.chain import Ledger
from tests.consts import T0


def test_chain_verifies(ledger):
    for n in range(5):
        ledger.append("INTENT", "trc_1", {"n": n}, T0 + timedelta(seconds=n))
    ok, bad = ledger.verify_chain()
    assert ok and bad is None


def test_tampering_is_detected(ledger):
    for n in range(5):
        ledger.append("INTENT", "trc_1", {"n": n}, T0 + timedelta(seconds=n))
    ledger.db.execute("UPDATE events SET payload='{\"n\":999}' WHERE seq=3")
    ok, bad = ledger.verify_chain()
    assert not ok and bad == 3


def test_claim_is_exactly_once(ledger):
    assert ledger.claim("k1", "trc_1", T0) is True
    assert ledger.claim("k1", "trc_2", T0) is False
    ledger.record_result("k1", "order_abc")
    assert ledger.result_for("k1") == "order_abc"
