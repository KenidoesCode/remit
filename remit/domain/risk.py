"""Risk engine.

Separate from drift on purpose. Drift asks *is this what they asked for?*
Risk asks *how much does it cost us to be wrong, and is this account
behaving normally?* A transaction can be perfectly on-intent and still be
risky (a large amount, a session that has already spent a lot, a merchant we
do not trust), and it can be off-intent but trivially cheap.

Expected loss is the bridge to the escalation decision:
    E[loss] = P(the transaction is not what was authorised)
              x amount at stake
              x irreversibility of the category

P(wrong) is estimated from two independent signals -- the calibrated parse
confidence, and the measured drift -- combined so that either one being bad
is enough:
    P(wrong) = 1 - (parse_confidence x (1 - drift_score))
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel

from ..money import Paise, rupees
from .drift import DriftResult
from .intent import IntentEnvelope

RiskLevel = Literal["low", "medium", "high", "critical"]

IRREVERSIBILITY = {
    "running shoes": 0.35, "fitness accessories": 0.35,
    "electronics accessories": 0.55, "travel accessories": 0.40,
    "home office": 0.45, "personal care": 0.85,   # hygiene: rarely returnable
    "default": 0.50,
}


class Exposure(BaseModel):
    session_paise: Paise = 0
    daily_paise: Paise = 0
    txn_count_1h: int = 0


class RiskDecision(BaseModel):
    level: RiskLevel
    expected_loss_paise: Paise
    p_wrong: float
    irreversibility: float
    friction_cost_paise: Paise
    recommended: str            # 'auto' | 'step_up' | 'deny'
    reasons: list[str]
    exposure: Exposure


def friction_cost(total_paise: Paise, floor_paise: Paise = 1500,
                  bps: int = 500) -> Paise:
    """What it costs to ask the human one unnecessary question.

    It is NOT a constant. A flat rupee figure says that interrupting someone
    over a Rs 50 purchase is exactly as costly as interrupting them over a
    Rs 50,000 one, which is false in both directions: over-asking on small
    baskets destroys the point of an agent, and under-asking on large ones is
    how money moves that nobody authorised.

        friction = max(floor, bps x total / 10_000)

    Defaults: a Rs 15 floor (the annoyance of any interruption) and 500 bps
    (5%) of the transaction. Both are POLICY KNOBS, not truths -- sweeping
    `friction_bps` is what draws the autonomy/revenue frontier, and the honest
    claim is "here is the exchange rate", not "here is the right number".

    The first version of this file used a flat Rs 15 and produced 296
    unnecessary escalations out of 540 journeys. See FAILURES.md 2026-08-21 16:10.
    """
    return max(floor_paise, total_paise * bps // 10_000)


def assess(*, env: IntentEnvelope, total_paise: Paise, drift: DriftResult,
           exposure: Exposure, now: datetime, merchant_risk: str = "low",
           parse_confidence: float | None = None,
           friction_cost_paise: Paise | None = None,
           friction_floor_paise: Paise = 1500, friction_bps: int = 500,
           session_cap_paise: Paise = 2_500_000,
           daily_cap_paise: Paise = 5_000_000,
           velocity_cap_1h: int = 12) -> RiskDecision:
    cat = (env.category or "default").lower()
    irr = IRREVERSIBILITY.get(cat, IRREVERSIBILITY["default"])
    # A raw model/parser confidence is not a probability. Expected-loss
    # arithmetic on an uncalibrated number is arithmetic on a lie, so the
    # caller passes in a CALIBRATED probability where one has been fitted.
    conf = env.parse_confidence if parse_confidence is None else parse_confidence
    if friction_cost_paise is None:
        friction_cost_paise = friction_cost(total_paise, friction_floor_paise,
                                            friction_bps)
    p_ok = max(0.0, min(1.0, conf)) * (1.0 - drift.score)
    p_wrong = round(1.0 - p_ok, 4)
    el = int(p_wrong * total_paise * irr)

    reasons: list[str] = []
    level: RiskLevel = "low"
    rec = "auto"

    if el > friction_cost_paise:
        level, rec = "medium", "step_up"
        reasons.append(
            f"expected loss {rupees(el)} exceeds the cost of asking "
            f"({rupees(friction_cost_paise)})")
    if drift.score >= 0.25:
        level, rec = "high", "step_up"
        reasons.append(f"measured drift {drift.score:.2f} on "
                       f"{drift.worst[0]}")
    if exposure.session_paise + total_paise > session_cap_paise:
        level, rec = "high", "deny"
        reasons.append(
            f"session exposure {rupees(exposure.session_paise + total_paise)} "
            f"exceeds the cap {rupees(session_cap_paise)}")
    if exposure.daily_paise + total_paise > daily_cap_paise:
        level, rec = "critical", "deny"
        reasons.append(
            f"daily exposure {rupees(exposure.daily_paise + total_paise)} "
            f"exceeds the cap {rupees(daily_cap_paise)}")
    if exposure.txn_count_1h >= velocity_cap_1h:
        level, rec = "high", "deny"
        reasons.append(f"velocity {exposure.txn_count_1h} transactions in the last hour")
    if merchant_risk in ("high", "blocked"):
        level, rec = "high", "step_up" if merchant_risk == "high" else "deny"
        reasons.append(f"merchant risk tier is {merchant_risk}")
    if env.expired(now):
        level, rec = "critical", "deny"
        reasons.append("intent has expired")
    if not reasons:
        reasons.append("within all limits and expected loss below the cost of asking")
    return RiskDecision(level=level, expected_loss_paise=el, p_wrong=p_wrong,
                        irreversibility=irr, friction_cost_paise=friction_cost_paise,
                        recommended=rec, reasons=reasons, exposure=exposure)
