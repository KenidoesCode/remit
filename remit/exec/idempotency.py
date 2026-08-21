"""Idempotency key derivation.

key = H(remit_id | intent_hash | envelope_epoch | revocation_epoch)

Each component earns its place:
  remit_id         - two grants may legitimately buy the same thing
  intent_hash      - the same cart twice in one turn is ONE purchase
  envelope_epoch   - a new notified envelope is a new authorisation period,
                     so an intentional repeat next week is a new key
  revocation_epoch - closes the TOCTOU gap: a revocation between the policy
                     check and the debit changes the key, so the in-flight
                     execution cannot claim and cannot land

Razorpay's core Orders/Payments APIs document no idempotency header; the
Order `receipt` field is the dedupe surface, and it is capped at 40 chars.
RazorpayX payouts DO take an idempotency key and have since 15 Mar 2025.
"""
from __future__ import annotations

import hashlib

RECEIPT_MAX = 40


def idempotency_key(*, remit_id: str, intent_hash: str,
                    envelope_epoch: int, revocation_epoch: int) -> str:
    raw = f"{remit_id}|{intent_hash}|{envelope_epoch}|{revocation_epoch}"
    return hashlib.sha256(raw.encode()).hexdigest()


def receipt_for(idem_key: str) -> str:
    """Razorpay Order.receipt is limited to 40 characters."""
    return idem_key[:RECEIPT_MAX]
