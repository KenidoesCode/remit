from datetime import timedelta

from remit.grants.issuer import issue, new_keypair, revoke, verify
from tests.consts import T0


def test_signature_verifies(remit, keys):
    _, pk = keys
    assert verify(remit, pk)


def test_tampered_ceiling_fails_verification(remit, keys):
    _, pk = keys
    s = remit.model_copy(deep=True)
    s.per_txn_ceiling_paise = 99_999_00
    assert not verify(s, pk)


def test_revocation_bumps_epoch_and_still_verifies(remit, keys):
    sk, pk = keys
    dead = revoke(remit, now=T0, signing_key=sk)
    assert dead.revocation_epoch == remit.revocation_epoch + 1
    assert verify(dead, pk)
