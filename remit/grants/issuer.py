"""Issue, verify and revoke Remits.

Ed25519 because the grant needs a small, fast, unambiguous signature that a
third party can verify without talking to us. Not because crypto is cool:
if the ledger and the grant were both only ours, neither would be evidence.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

from ..models import Remit
from ..money import Paise


def new_keypair() -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    sk = Ed25519PrivateKey.generate()
    return sk, sk.public_key()


def pubkey_hex(pk: Ed25519PublicKey) -> str:
    return pk.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()


def issue(
    *,
    signing_key: Ed25519PrivateKey,
    subject: str,
    agent_instance: str,
    merchant_ids: list[str],
    categories: list[str],
    per_txn_ceiling_paise: Paise,
    aggregate_ceiling_paise: Paise,
    count_ceiling: int,
    valid_days: int,
    now: datetime,
    policy_version: str,
    cooloff_hours: int = 24,
    cooloff_ceiling_paise: Paise = 500_00,
    envelope_days: int = 7,
    reaffirm_after_days: int = 30,
    reaffirm_after_paise: Paise | None = None,
) -> Remit:
    s = Remit(
        remit_id="rmt_" + uuid.uuid4().hex[:20],
        subject=subject,
        agent_instance=agent_instance,
        merchant_ids=sorted(merchant_ids),
        categories=sorted(categories),
        per_txn_ceiling_paise=per_txn_ceiling_paise,
        aggregate_ceiling_paise=aggregate_ceiling_paise,
        count_ceiling=count_ceiling,
        granted_at=now,
        valid_until=now + timedelta(days=valid_days),
        cooloff_until=now + timedelta(hours=cooloff_hours),
        cooloff_ceiling_paise=cooloff_ceiling_paise,
        envelope_epoch=1,
        # The envelope is notified 24h AHEAD of the window it covers.
        envelope_notified_at=now,
        envelope_ceiling_paise=aggregate_ceiling_paise,
        reaffirm_after_days=reaffirm_after_days,
        reaffirm_after_paise=reaffirm_after_paise or (aggregate_ceiling_paise // 2),
        policy_version=policy_version,
    )
    s.sig = signing_key.sign(s.signing_payload().encode()).hex()
    return s


def verify(remit: Remit, public_key: Ed25519PublicKey) -> bool:
    try:
        public_key.verify(bytes.fromhex(remit.sig), remit.signing_payload().encode())
        return True
    except Exception:
        return False


def revoke(remit: Remit, *, now: datetime, signing_key: Ed25519PrivateKey) -> Remit:
    """Revocation bumps an epoch. That epoch is an input to the idempotency
    key, which is what closes the TOCTOU window between the policy check
    and the debit."""
    s = remit.model_copy(deep=True)
    s.revoked_at = now
    s.revocation_epoch = remit.revocation_epoch + 1
    s.sig = ""
    s.sig = signing_key.sign(s.signing_payload().encode()).hex()
    return s


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
