from remit.exec.idempotency import RECEIPT_MAX, idempotency_key, receipt_for


def _k(**kw):
    base = dict(remit_id="rmt_1", intent_hash="h", envelope_epoch=1,
                revocation_epoch=0)
    base.update(kw)
    return idempotency_key(**base)


def test_stable_across_retries():
    assert _k() == _k()


def test_revocation_changes_the_key():
    """This is what closes the TOCTOU gap between check and debit."""
    assert _k() != _k(revocation_epoch=1)


def test_new_envelope_is_a_new_authorisation():
    assert _k() != _k(envelope_epoch=2)


def test_different_cart_is_a_different_key():
    assert _k() != _k(intent_hash="other")


def test_receipt_fits_razorpay_limit():
    assert len(receipt_for(_k())) == RECEIPT_MAX <= 40
