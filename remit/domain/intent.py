"""The Intent Envelope: the immutable, versioned record of what the human
actually authorised.

Two rules that the rest of the system depends on:
  1. An envelope is NEVER mutated. A change creates version n+1 with a
     reason. History is how a dispute gets adjudicated.
  2. Everything the drift engine compares against comes from here. If a
     constraint is not in the envelope, the human did not state it, and the
     agent is not bound by it -- which is itself a finding worth surfacing.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from ..models import canonical, sha
from ..money import Paise

Objective = Literal["best_value", "cheapest", "best_rated", "fastest_delivery"]


class IntentEnvelope(BaseModel):
    intent_id: str
    user_id: str
    version: int = 1
    utterance: str
    category: str | None = None
    product_terms: list[str] = []      # the noun the human actually said
    max_price_paise: Paise | None = None        # per-unit ceiling if stated
    max_total_paise: Paise | None = None        # all-in ceiling if stated
    currency: str = "INR"
    quantity: int = 1
    objective: Objective = "best_value"
    required_attributes: list[str] = []
    excluded_attributes: list[str] = []
    merchant_constraints: list[str] = []
    purchase_authority: bool = False
    created_at: datetime
    expires_at: datetime
    parse_confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @property
    def semantic_hash(self) -> str:
        """What the human ASKED FOR, independent of when they asked.

        Deliberately excludes intent_id, created_at, expires_at, version and
        parse_confidence. Two identical utterances from the same user are the
        same purchase intent and must produce the same idempotency key --
        otherwise a chat UI that resends, or an agent that retries, buys twice.
        Found by the eval's retry-storm bucket; see FAILURES.md 2026-08-21 15:00.
        """
        d = json.loads(self.model_dump_json())
        for k in ("intent_id", "created_at", "expires_at", "version",
                  "parse_confidence"):
            d.pop(k, None)
        return sha(canonical(d))

    @property
    def envelope_hash(self) -> str:
        return sha(canonical(json.loads(self.model_dump_json())))

    def ceiling_paise(self) -> Paise | None:
        """The all-in ceiling the transaction must respect.

        If the human said 'under 5000' about a product, that is a per-unit
        price ceiling -- NOT a licence for the total to exceed it. We treat
        an unqualified ceiling as binding on the total, because that is what
        a person means, and we say so out loud in the UI. Getting this wrong
        in the permissive direction is exactly the failure the project is
        about.
        """
        if self.max_total_paise is not None:
            return self.max_total_paise
        if self.max_price_paise is not None:
            return self.max_price_paise * self.quantity
        return None

    def expired(self, now: datetime) -> bool:
        return now > self.expires_at


def new_intent(*, user_id: str, utterance: str, now: datetime,
               ttl_minutes: int = 30, **fields) -> IntentEnvelope:
    return IntentEnvelope(
        intent_id="int_" + uuid.uuid4().hex[:18], user_id=user_id,
        utterance=utterance, created_at=now,
        expires_at=now + timedelta(minutes=ttl_minutes), **fields)


def amend(env: IntentEnvelope, *, now: datetime, reason: str, **changes
          ) -> tuple[IntentEnvelope, str]:
    """Never mutate. Produce version n+1 and the reason it exists."""
    data = env.model_dump()
    data.update(changes)
    data["version"] = env.version + 1
    data["created_at"] = now
    return IntentEnvelope(**data), reason
