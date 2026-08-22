"""The REMIT protocol, as types.

REMIT was a website with an engine behind it. Everything a reviewer could do,
they did through a page this repository also owns -- which makes the claim
"this is infrastructure, not an app" a thing said rather than a thing shown.

This module is the seam. Six nouns, one verb each:

    INTENT      what a human authorised, in their own words
    AUTHORITY   the bounded envelope that intent compiled into
    ACTION      what an agent proposes to do with it
    DECISION    AUTO / STEP_UP / DENY, with every clause behind it
    EVIDENCE    the record that makes the decision reconstructable
    EXECUTION   the money moving, once and only once

An external agent needs exactly these and none of REMIT's internals. It does
not need to know what a drift dimension is, that the policy is YAML, that the
ledger is hash-chained, or that a `RequestedItem`'s terms are a conjunction. It
needs to say what it wants, be told whether it may, and be able to prove
afterwards what happened.

WHAT THIS IS NOT
----------------
It is not a second implementation. Every type here is a view over the objects
the journey already produces -- a projection, not a parallel model. A protocol
that reimplements the engine is a protocol that will disagree with it, and the
first thing anyone will find is the disagreement.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

PROTOCOL_VERSION = "1.0"

Verdict = Literal["AUTO", "STEP_UP", "DENY"]


class Clause(BaseModel):
    """One named rule and what it saw. The clause id is stable across
    versions; the detail is human-readable and is not."""
    clause_id: str
    passed: bool
    detail: str


class Money(BaseModel):
    """Never a bare number.

    An amount without a unit is not an amount -- the mistake that let
    "under $5,000" become a ₹5,000 ceiling lived in exactly the gap this type
    closes. Paise, not rupees, because integers do not round.
    """
    amount_paise: int = Field(ge=0)
    currency: str = "INR"


class Intent(BaseModel):
    """What the human said, and what it was understood to mean.

    `utterance` is the evidence. Everything below it is interpretation, and the
    protocol keeps them separate on purpose: an agent that disagrees with the
    interpretation can say so against the original words.
    """
    intent_id: str
    actor_id: str
    utterance: str
    semantic_hash: str
    category: str | None = None
    requested: list[str] = []          # the nouns the human actually said
    excluded: list[str] = []           # and what they ruled out
    quantity: int = 1
    ceiling: Money | None = None
    objective: str = "best_value"
    merchants: list[str] = []
    created_at: str
    expires_at: str
    policy_version: str
    catalog_version: int
    interpreter: str                   # which intelligence produced this
    confidence: float


class Authority(BaseModel):
    """The bounded envelope, and its lifecycle state.

    `state` is the authority machine's, not the payment's. They are different
    questions: one is what the human permitted, the other is what the gateway
    did.
    """
    intent_id: str
    actor_id: str
    state: str
    ceiling: Money | None = None
    expires_at: str
    revoked: bool = False
    revoked_at: str | None = None
    version: int = 1


class Action(BaseModel):
    """What an agent proposes. Deliberately not "what an agent did".

    Everything an external caller can put here is untrusted input. The
    authority it executes against is looked up server-side from the intent id
    and the session principal -- there is no field in this model that lets a
    caller assert what they are allowed to do.
    """
    intent_id: str
    kind: Literal["purchase"] = "purchase"
    items: list[dict] = []             # {product_id, qty}
    accept_offers: str = "in_envelope"
    approval_token: str | None = None


class Decision(BaseModel):
    verdict: Verdict
    reason: str
    clauses: list[Clause] = []
    failed: list[str] = []
    drift: float | None = None
    total: Money | None = None
    authority_state: str | None = None
    correlation_id: str
    latency_ms: float | None = None
    protocol_version: str = PROTOCOL_VERSION


class Execution(BaseModel):
    """The money. `replayed` is part of the contract, not an implementation
    detail: an integrator retrying a request needs to know that the payment it
    is looking at is the one it already made."""
    correlation_id: str
    payment_id: str | None = None
    order_id: str | None = None
    state: str
    total: Money | None = None
    replayed: bool = False
    checkout_key_id: str | None = None


class Evidence(BaseModel):
    """Enough to answer "why did this happen" without asking the model."""
    correlation_id: str
    intent_id: str | None = None
    events: list[dict] = []
    decision: dict | None = None
    authority_history: list[dict] = []
    chain_intact: bool = True
    first_bad_seq: int | None = None
