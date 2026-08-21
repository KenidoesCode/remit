"""Core types. Everything that moves between components is one of these.

Design rule enforced here: the model proposes, the types constrain.
`Intent.computed_amount_paise` is derived from catalog ids x qty by
deterministic code. `Intent.stated_amount_paise` is whatever the LLM said.
A disagreement between them is a signal, not an error to paper over.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, NonNegativeInt

from .money import Paise


def canonical(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


class CatalogItem(BaseModel):
    item_id: str
    name: str
    category: str
    unit_price_paise: NonNegativeInt


class IntentItem(BaseModel):
    item_id: str
    qty: int = Field(ge=1, le=999)


class Alternative(BaseModel):
    """A parse the compiler considered and rejected. Kept because when a
    transaction is disputed, the rejected alternatives are the evidence
    that the ambiguity was seen and adjudicated, not missed."""
    description: str
    probability: float = Field(ge=0.0, le=1.0)
    amount_paise: Paise | None = None


class Intent(BaseModel):
    utterance_hash: str
    merchant_id: str
    category: str
    items: list[IntentItem]
    computed_amount_paise: Paise          # deterministic: catalog x qty
    stated_amount_paise: Paise | None      # what the model claimed
    user_ceiling_paise: Paise | None       # "under 800" said out loud
    raw_confidence: float = Field(ge=0.0, le=1.0)
    alternatives: list[Alternative] = []

    @property
    def intent_hash(self) -> str:
        return sha(canonical({
            "u": self.utterance_hash,
            "m": self.merchant_id,
            "i": sorted((it.item_id, it.qty) for it in self.items),
            "a": self.computed_amount_paise,
        }))

    @property
    def amount_disagreement(self) -> Paise:
        if self.stated_amount_paise is None:
            return 0
        return abs(self.stated_amount_paise - self.computed_amount_paise)


class Verdict(str, Enum):
    ALLOW = "ALLOW"
    STEP_UP = "STEP_UP"
    DENY = "DENY"


class ClauseHit(BaseModel):
    clause_id: str
    passed: bool
    detail: str


class Decision(BaseModel):
    verdict: Verdict
    clause_hits: list[ClauseHit]
    expected_loss_paise: Paise
    friction_cost_paise: Paise
    reason: str
    counterfactual: str | None = None
    policy_version: str

    @property
    def failed_clauses(self) -> list[str]:
        return [c.clause_id for c in self.clause_hits if not c.passed]


class Remit(BaseModel):
    """A bounded, revocable grant of spending authority.

    Three ceilings, not one: a per-transaction cap alone is trivially
    defeated by repetition, and an aggregate cap alone permits a single
    catastrophic purchase.
    """
    remit_id: str
    subject: str                      # the human
    agent_instance: str               # revocable, per-session agent identity
    merchant_ids: list[str]
    categories: list[str]
    per_txn_ceiling_paise: Paise
    aggregate_ceiling_paise: Paise
    count_ceiling: int
    granted_at: datetime
    valid_until: datetime
    # UPI Circle mirrors a REDUCED CAP for 24h after delegation, not a freeze.
    cooloff_until: datetime
    cooloff_ceiling_paise: Paise
    # RBI e-mandate 2026 wants 24h pre-debit notice; we notify an envelope.
    envelope_epoch: int
    envelope_notified_at: datetime
    envelope_ceiling_paise: Paise
    # Consent decays on two axes: elapsed time and cumulative spend.
    reaffirm_after_days: int
    reaffirm_after_paise: Paise
    revocation_epoch: int = 0
    revoked_at: datetime | None = None
    policy_version: str = ""
    sig: str = ""

    def signing_payload(self) -> str:
        d = self.model_dump(exclude={"sig"})
        return canonical(d)


class SpendState(BaseModel):
    """Observed spend, supplied to the policy engine as an input.
    The engine never queries a database; purity is what makes the
    counterfactual replay in the demo possible."""
    per_remit_spent_paise: dict[str, Paise] = {}
    per_remit_count: dict[str, int] = {}
    subject_live_exposure_paise: Paise = 0   # across ALL live grants

    def spent(self, remit_id: str) -> Paise:
        return self.per_remit_spent_paise.get(remit_id, 0)

    def count(self, remit_id: str) -> int:
        return self.per_remit_count.get(remit_id, 0)


LedgerKind = Literal[
    "UTTERANCE", "INTENT", "CALIBRATION", "POLICY_DECISION", "CHALLENGE",
    "REMIT_ISSUED", "REMIT_REVOKED", "TOOL_CALL", "GATEWAY_RESPONSE",
    "WEBHOOK", "RECONCILED", "EXCEPTION",
]
