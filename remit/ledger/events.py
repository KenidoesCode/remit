"""The event vocabulary, in one place, with a version on it.

Event kinds were string literals scattered across `journey.run` -- twenty-one
of them, plus six more in the legacy gateway, invented at the call site. That
is fine until somebody needs to answer *why did this payment happen* from the
record alone, at which point the questions are: which kinds exist, which are
mandatory, and what does each one carry.

This module answers those three questions and nothing else. It deliberately
does not wrap `Ledger.append` in a validation layer that could refuse to record
an event -- an audit log that can reject a write is an audit log that can be
made to forget, and the failure mode of a strict schema on this path is a
missing record of the exact thing somebody wanted to look at.

So: the vocabulary is declared, the required fields are declared, and a test
asserts the code and this file agree. The enforcement is at test time, where a
mistake costs a red build, rather than at run time, where it costs evidence.
"""
from __future__ import annotations

SCHEMA_VERSION = "1"

# Every kind the live path emits, grouped by the question it answers.
KINDS: dict[str, str] = {
    # what was asked
    "UTTERANCE":            "the raw sentence, as typed",
    "INTENT_CREATED":       "the envelope it compiled into",
    "INTENT_AMENDED":       "a new version, with the reason it exists",
    # what was found
    "PRODUCT_SEARCH":       "how many candidates each requested item found",
    "PRODUCT_SELECTED":     "the pick, its score and why",
    "OFFER_PROPOSED":       "what the merchant wanted to add",
    "OFFER_ACCEPTED":       "what fitted inside the envelope",
    "CART_PRICED":          "lines, subtotal, shipping, total",
    # what was judged
    "DRIFT_MEASURED":       "distance from what was said, per dimension",
    "RISK_EVALUATED":       "expected loss against the cost of asking",
    "POLICY_DECIDED":       "the verdict and every clause behind it",
    # what happened to the authority
    "STEP_UP_REQUIRED":     "policy stopped and asked a person",
    "APPROVAL_REQUESTED":   "a token was issued, bound to this basket",
    "APPROVAL_REJECTED":    "a token was refused, and precisely why",
    "USER_CONFIRMED":       "a person said yes",
    "AUTHORIZATION_REVOKED": "a person cancelled the authority",
    # what happened to the money
    "PAYMENT_REQUESTED":    "an order was asked for",
    "PAYMENT_CREATED":      "an order exists at the gateway",
    "PAYMENT_REPLAYED":     "idempotency returned an existing payment",
    "PAYMENT_BLOCKED":      "no order was created, and why",
    "PAYMENT_FAILED":       "the gateway refused",
    "EXCEPTION":            "something the system could not do",
}

# Fields that must be present for the record to be worth having. Kept short
# on purpose: a required field nobody reads is a field somebody will pad.
REQUIRED: dict[str, tuple[str, ...]] = {
    "POLICY_DECIDED":     ("verdict", "clauses"),
    "PAYMENT_CREATED":    ("payment_id", "order_id"),
    "PAYMENT_BLOCKED":    ("amount_paise",),
    "APPROVAL_REJECTED":  ("reason", "detail"),
    "AUTHORIZATION_REVOKED": ("revocation_id", "scope", "revoked_at"),
    "CART_PRICED":        ("total_paise",),
    "UTTERANCE":          ("utterance",),
    "PRODUCT_SELECTED":   ("product_id", "name", "why"),
}

# The events without which "why did this payment happen" cannot be answered.
# A journey that reaches a decision must have emitted all of these.
MANDATORY_FOR_A_DECISION = (
    "UTTERANCE", "INTENT_CREATED", "PRODUCT_SEARCH", "CART_PRICED",
    "DRIFT_MEASURED", "RISK_EVALUATED", "POLICY_DECIDED",
)


def unknown(kinds) -> list[str]:
    """Kinds emitted that this vocabulary does not declare."""
    return sorted({k for k in kinds if k not in KINDS})


def missing_fields(kind: str, payload: dict) -> list[str]:
    return [f for f in REQUIRED.get(kind, ()) if f not in (payload or {})]
