"""The policy engine.

THREE PROPERTIES, and the demo depends on all of them:

  1. Pure. No I/O, no clock reads, no randomness. `now` is an argument.
  2. Total. Every path returns a Decision carrying the clauses it evaluated.
  3. Explaining. An ALLOW is only ever produced with a full set of passed
     clauses; there is no early-return that skips the record.

Purity is why 400 recorded decisions can be replayed against a modified
policy in milliseconds, which is the whole counterfactual-replay demo.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import yaml

from ..models import ClauseHit, Decision, Intent, Remit, SpendState, Verdict
from ..money import Paise, rupees


class Policy:
    def __init__(self, doc: dict):
        self.doc = doc
        self.version = doc["version"]
        self.risk = doc["risk"]
        self.limits = doc["limits"]
        self.clauses = doc["clauses"]

    @classmethod
    def load(cls, path: str) -> "Policy":
        with open(path) as fh:
            return cls(yaml.safe_load(fh))

    def with_overrides(self, **kw) -> "Policy":
        """Used by the frontier sweep. Returns a NEW policy; never mutates."""
        import copy
        d = copy.deepcopy(self.doc)
        for k, v in kw.items():
            section, _, key = k.partition(".")
            if key:
                d[section][key] = v
            else:
                d[k] = v
        d["version"] = d["version"] + "+" + ",".join(f"{k}={v}" for k, v in kw.items())
        return Policy(d)

    def on(self, clause_id: str) -> bool:
        c = self.clauses.get(clause_id)
        return bool(c and c.get("enabled", True))

    def irreversibility(self, category: str) -> float:
        return float(self.risk["irreversibility"].get(
            category, self.risk["irreversibility"]["default"]))


def expected_loss(intent: Intent, p_correct: float, policy: Policy) -> Paise:
    """What it costs, in paise, to be wrong about this intent."""
    return int(
        (1.0 - p_correct)
        * intent.computed_amount_paise
        * policy.irreversibility(intent.category)
    )


def evaluate(
    *,
    intent: Intent,
    remit: Remit | None,
    spend: SpendState,
    p_correct: float,
    policy: Policy,
    now: datetime,
) -> Decision:
    hits: list[ClauseHit] = []
    amount = intent.computed_amount_paise
    counterfactual: str | None = None

    def hit(cid: str, passed: bool, detail: str) -> bool:
        if policy.on(cid):
            hits.append(ClauseHit(clause_id=cid, passed=passed, detail=detail))
            return passed
        return True

    el = expected_loss(intent, p_correct, policy)
    friction = int(policy.risk["friction_cost_paise"])

    if remit is None:
        hits.append(ClauseHit(clause_id="GRANT-000", passed=False,
                              detail="no remit presented"))
        return Decision(
            verdict=Verdict.STEP_UP, clause_hits=hits, expected_loss_paise=el,
            friction_cost_paise=friction, policy_version=policy.version,
            reason="No grant exists for this merchant yet. A human must issue one.",
            counterfactual="Would be ALLOW if a live remit covered "
                           f"{intent.merchant_id}/{intent.category}.",
        )

    hard_fail = False
    hard_fail |= not hit("SCOPE-001", intent.merchant_id in remit.merchant_ids,
                         f"{intent.merchant_id} in {remit.merchant_ids}")
    hard_fail |= not hit("SCOPE-002", intent.category in remit.categories,
                         f"{intent.category} in {remit.categories}")
    hard_fail |= not hit("LIFE-001", now <= remit.valid_until,
                         f"now={now.isoformat()} until={remit.valid_until.isoformat()}")
    hard_fail |= not hit("LIFE-002", remit.revoked_at is None,
                         f"revoked_at={remit.revoked_at}")

    ok_txn = amount <= remit.per_txn_ceiling_paise
    if not hit("CEIL-001", ok_txn,
               f"{rupees(amount)} <= {rupees(remit.per_txn_ceiling_paise)}"):
        hard_fail = True
        counterfactual = (f"Would pass CEIL-001 if the per-transaction ceiling "
                          f"were >= {rupees(amount)} (currently "
                          f"{rupees(remit.per_txn_ceiling_paise)}).")

    spent = spend.spent(remit.remit_id)
    ok_agg = spent + amount <= remit.aggregate_ceiling_paise
    if not hit("CEIL-002", ok_agg,
               f"{rupees(spent)}+{rupees(amount)} <= "
               f"{rupees(remit.aggregate_ceiling_paise)}"):
        hard_fail = True
        counterfactual = counterfactual or (
            f"Would pass CEIL-002 if the aggregate ceiling were >= "
            f"{rupees(spent + amount)}.")

    cnt = spend.count(remit.remit_id)
    if not hit("CEIL-003", cnt + 1 <= remit.count_ceiling,
               f"{cnt + 1} <= {remit.count_ceiling}"):
        hard_fail = True

    # UPI Circle mirror: a REDUCED CAP for 24h after the grant, not a freeze.
    in_cooloff = now < remit.cooloff_until
    cooloff_ok = (not in_cooloff) or (spent + amount <= remit.cooloff_ceiling_paise)
    if not hit("COOL-001", cooloff_ok,
               f"in_cooloff={in_cooloff} cap={rupees(remit.cooloff_ceiling_paise)}"):
        hard_fail = True

    # RBI e-mandate 2026: notice must precede the debit by 24h.
    notified = remit.envelope_notified_at + timedelta(hours=24) <= now
    env_ok = notified and (spent + amount <= remit.envelope_ceiling_paise)
    if not hit("ENV-001", env_ok,
               f"notified_24h_ahead={notified} "
               f"envelope_ceiling={rupees(remit.envelope_ceiling_paise)}"):
        hard_fail = True
        counterfactual = counterfactual or (
            "Would pass ENV-001 24h after envelope notification; the debit is "
            "inside the window but the notice has not aged.")

    # The hole nothing in the real ecosystem closes today.
    subject_cap = int(policy.limits["subject_aggregate_exposure_paise"])
    agg_ok = spend.subject_live_exposure_paise + amount <= subject_cap
    if not hit("AGG-001", agg_ok,
               f"subject exposure {rupees(spend.subject_live_exposure_paise)}"
               f"+{rupees(amount)} <= {rupees(subject_cap)}"):
        hard_fail = True
        counterfactual = counterfactual or (
            f"Blocked by total exposure across ALL live grants "
            f"({rupees(spend.subject_live_exposure_paise)}), not by this grant.")

    # Consent decays on two axes.
    age_days = (now - remit.granted_at).days
    if not hit("FRESH-001", age_days <= remit.reaffirm_after_days,
               f"age={age_days}d limit={remit.reaffirm_after_days}d"):
        hard_fail = True
        counterfactual = counterfactual or (
            f"Consent is {age_days} days old; re-affirmation is required after "
            f"{remit.reaffirm_after_days}.")
    if not hit("FRESH-002", spent <= remit.reaffirm_after_paise,
               f"spent={rupees(spent)} limit={rupees(remit.reaffirm_after_paise)}"):
        hard_fail = True

    if intent.user_ceiling_paise is not None:
        if not hit("USER-001", amount <= intent.user_ceiling_paise,
                   f"{rupees(amount)} <= user said {rupees(intent.user_ceiling_paise)}"):
            hard_fail = True
            counterfactual = counterfactual or (
                f"The user said {rupees(intent.user_ceiling_paise)}; the cart is "
                f"{rupees(amount)}.")

    # --- confidence gates: these escalate, they do not deny -----------
    soft_fail = False
    floor = float(policy.risk["hard_confidence_floor"])
    soft_fail |= not hit("CONF-001", p_correct >= floor,
                         f"p={p_correct:.3f} floor={floor}")
    max_dis = int(policy.risk["max_amount_disagreement_paise"])
    soft_fail |= not hit("CONF-002", intent.amount_disagreement <= max_dis,
                         f"disagreement={rupees(intent.amount_disagreement)}")

    if hard_fail:
        failed = [c.clause_id for c in hits if not c.passed]
        return Decision(
            verdict=Verdict.DENY, clause_hits=hits, expected_loss_paise=el,
            friction_cost_paise=friction, policy_version=policy.version,
            reason=f"Refused by {', '.join(failed)}.",
            counterfactual=counterfactual,
        )

    # The threshold is not a magic number: ask when being wrong costs more
    # than asking does.
    if soft_fail or el > friction:
        return Decision(
            verdict=Verdict.STEP_UP, clause_hits=hits, expected_loss_paise=el,
            friction_cost_paise=friction, policy_version=policy.version,
            reason=(f"Expected loss {rupees(el)} exceeds the cost of asking "
                    f"({rupees(friction)}); confirming with the human."),
            counterfactual=(f"Would auto-execute at p_correct >= "
                            f"{1 - friction / max(1, int(amount * policy.irreversibility(intent.category))):.3f}."),
        )

    return Decision(
        verdict=Verdict.ALLOW, clause_hits=hits, expected_loss_paise=el,
        friction_cost_paise=friction, policy_version=policy.version,
        reason=(f"Within grant {remit.remit_id}; expected loss {rupees(el)} "
                f"below the cost of asking."),
    )
