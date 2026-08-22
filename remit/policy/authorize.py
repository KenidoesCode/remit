"""The authorization decision. Deterministic, pure, and final.

(env, cart, totals, drift, risk, exposure, policy, now) -> Authorization

Three properties, all load-bearing:
  1. PURE. No I/O, no clock reads, no randomness. `now` is an argument.
     This is what makes the frontier sweep and the counterfactual replay
     possible -- 500 recorded journeys replay against a modified policy in
     milliseconds because nothing needs to be re-derived.
  2. TOTAL. Every path returns an Authorization carrying every clause it
     evaluated, passed or failed.
  3. THE MODEL IS NOT AN INPUT. Nothing here consults an LLM. The LLM's
     output reaches this function only as data that has already been
     validated and measured (parse confidence, drift).
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

import yaml

from ..domain.cart import Cart, Totals
from ..domain.drift import DriftResult
from ..domain.intent import IntentEnvelope
from ..domain.risk import Exposure, RiskDecision
from ..money import rupees


class Verdict(str, Enum):
    AUTO = "AUTO"
    STEP_UP = "STEP_UP"
    DENY = "DENY"


class Clause:
    __slots__ = ("clause_id", "passed", "detail")

    def __init__(self, clause_id: str, passed: bool, detail: str):
        self.clause_id, self.passed, self.detail = clause_id, passed, detail

    def dict(self):
        return {"clause_id": self.clause_id, "passed": self.passed,
                "detail": self.detail}


class Authorization:
    def __init__(self, verdict: Verdict, clauses: list[Clause], reason: str,
                 policy_version: str, counterfactual: str | None = None,
                 blocked_value_paise: int = 0):
        self.verdict = verdict
        self.clauses = clauses
        self.reason = reason
        self.policy_version = policy_version
        self.counterfactual = counterfactual
        self.blocked_value_paise = blocked_value_paise

    @property
    def failed(self) -> list[str]:
        return [c.clause_id for c in self.clauses if not c.passed]

    def dict(self):
        return {"verdict": self.verdict.value, "reason": self.reason,
                "failed": self.failed, "policy_version": self.policy_version,
                "counterfactual": self.counterfactual,
                "blocked_value_paise": self.blocked_value_paise,
                "clauses": [c.dict() for c in self.clauses]}


class Policy:
    def __init__(self, doc: dict):
        self.doc = doc
        self.version = doc["version"]
        self.name = doc.get("name", "policy")
        self.limits = doc["limits"]
        self.revenue = doc.get("revenue", {})
        # The clause map is data, and anything that wants to say how many
        # clauses this policy has reads it from here rather than typing a
        # number that goes stale the next time one is added.
        self.clauses = doc.get("clauses", {})

    @classmethod
    def load(cls, path: str = "policy/authorize.yaml") -> "Policy":
        with open(path) as fh:
            return cls(yaml.safe_load(fh))

    def with_overrides(self, **kw) -> "Policy":
        import copy
        d = copy.deepcopy(self.doc)
        for k, v in kw.items():
            section, _, key = k.partition(".")
            if key:
                d.setdefault(section, {})[key] = v
            else:
                d["limits"][k] = v
        d["version"] = d["version"] + "+" + ",".join(f"{k}={v}" for k, v in sorted(kw.items()))
        return Policy(d)


def authorize(*, env: IntentEnvelope, cart: Cart, totals: Totals,
              drift: DriftResult, risk: RiskDecision, exposure: Exposure,
              policy: Policy, now: datetime, catalog_version: int,
              out_of_stock: list[str] | None = None,
              intent_revoked: bool = False,
              stale_pricing: bool | None = None) -> Authorization:
    L = policy.limits
    # The integrity layer as ONE switch, so "without REMIT" is a single honest
    # data change rather than a scattering of knobs a reader has to reassemble.
    # When it is off, the clauses that exist to hold the agent to what the human
    # said are recorded as passing, with the reason stated.
    integrity = bool(L.get("integrity_layer", True))
    cl: list[Clause] = []
    deny = False
    step = False
    cf: str | None = None

    def check(cid: str, ok: bool, detail: str, hard: bool) -> None:
        nonlocal deny, step
        cl.append(Clause(cid, ok, detail))
        if not ok:
            if hard:
                deny = True
            else:
                step = True

    ceiling = env.ceiling_paise()

    check("AUTH-001", env.purchase_authority or not L["require_purchase_authority"],
          f"purchase_authority={env.purchase_authority}", hard=True)
    check("AUTH-002", not env.expired(now),
          f"expires_at={env.expires_at.isoformat()}", hard=True)
    check("AUTH-003", not intent_revoked, f"revoked={intent_revoked}", hard=True)
    check("CUR-001", env.currency in L["allowed_currencies"],
          f"currency={env.currency}", hard=True)

    if not integrity:
        cl.append(Clause("CEIL-001", True,
                         "stated ceiling NOT enforced -- integrity layer is off"))
    elif ceiling is None:
        cl.append(Clause("CEIL-001", True, "no ceiling was stated by the human"))
    else:
        ok = totals.total_paise <= ceiling
        check("CEIL-001", ok,
              f"{rupees(totals.total_paise)} <= {rupees(ceiling)}", hard=False)
        if not ok:
            cf = (f"would pass CEIL-001 if the authorised amount were "
                  f"{rupees(totals.total_paise)} "
                  f"(currently {rupees(ceiling)}, short by "
                  f"{rupees(totals.total_paise - ceiling)})")

    check("CEIL-002", totals.total_paise <= L["max_transaction_paise"],
          f"{rupees(totals.total_paise)} <= "
          f"{rupees(L['max_transaction_paise'])}", hard=True)
    check("EXPO-001",
          exposure.session_paise + totals.total_paise <= L["session_exposure_paise"],
          f"session {rupees(exposure.session_paise + totals.total_paise)}", hard=True)
    check("EXPO-002",
          exposure.daily_paise + totals.total_paise <= L["daily_exposure_paise"],
          f"daily {rupees(exposure.daily_paise + totals.total_paise)}", hard=True)
    check("VEL-001", exposure.txn_count_1h < L["velocity_1h"],
          f"{exposure.txn_count_1h} in the last hour", hard=True)

    check("DRIFT-002", drift.score <= L["max_drift_stepup"],
          f"drift {drift.score:.3f} <= {L['max_drift_stepup']}", hard=True)
    check("DRIFT-001", (not integrity) or drift.score <= L["max_drift_auto"],
          f"drift {drift.score:.3f} <= {L['max_drift_auto']}"
          if integrity else "drift not enforced -- integrity layer is off", hard=False)
    check("CONF-001", env.parse_confidence >= L["min_parse_confidence"],
          f"parse confidence {env.parse_confidence:.2f}", hard=False)
    # A version counter moving is not drift. What matters is whether the price
    # we are about to charge is the price we showed.
    stale = (catalog_version != cart.catalog_version
             if stale_pricing is None else stale_pricing)
    check("CAT-001", (not integrity) or not stale,
          f"cart priced at v{cart.catalog_version}, catalog now v{catalog_version}"
          f"{'; prices moved' if stale else '; nothing in this cart changed'}",
          hard=False)

    oos = out_of_stock or []
    check("STOCK-001", not oos, f"out of stock: {oos}" if oos else "all in stock",
          hard=True)

    # Regulated goods. Age-restricted items and pharmacy lines are never an
    # autonomous purchase, at any price, under any ceiling. This is not a risk
    # judgement that trades off against the cost of asking -- it is a category
    # of thing an agent may not decide alone, so it is a soft failure that
    # forces the human into the loop rather than a number that can be tuned
    # until it stops firing.
    restricted = [(l.name, l.restricted) for l in cart.lines
                  if getattr(l, "restricted", None)]
    check("RESTRICT-001", (not integrity) or not restricted,
          ("requires a person: " + ", ".join(f"{pid} ({kind})"
                                             for pid, kind in restricted))
          if restricted else "nothing age-restricted or pharmacy in this cart",
          hard=False)

    # RESEMBLANCE IS NOT A MATCH.
    #
    # "buy a laptop" reaches a Laptop Stand, because "laptop" is a word in its
    # name. The stand is a real product, it is in budget, drift scores zero --
    # every gate agrees, and the agent buys a Rs 4,446 stand for someone who
    # asked for a laptop, on AUTO. No amount of risk tuning catches that,
    # because nothing about it is risky; it is simply not what was asked for.
    #
    # So the grounder marks a term that only ever appears as a MODIFIER inside
    # some product's name, and that mark becomes a clause. Soft, not hard: the
    # stand may well be what they wanted. A person decides. FAILURES #24.
    approx = list(getattr(env, "approximate_items", []) or [])
    check("MATCH-001", (not integrity) or not approx,
          ("named but not stocked: " + ", ".join(repr(a) for a in approx)
           + " -- the cart holds the nearest thing, not the thing")
          if approx else "every item was matched by name",
          hard=False)

    if not L.get("allow_agent_added_over_ceiling", False) and ceiling is not None:
        agent_added = [l for l in cart.lines
                       if l.origin in ("upsell", "cross_sell") and l.accepted_by == "agent"]
        ok = (not integrity) or not (agent_added and totals.total_paise > ceiling)
        check("AGENT-001", ok,
              f"agent-added lines: {[l.product_id for l in agent_added]}", hard=False)

    check("RISK-001", risk.recommended != "deny",
          f"risk={risk.level}, recommends {risk.recommended}",
          hard=(risk.recommended == "deny"))
    # A risk-driven escalation must appear in `failed` too, otherwise the UI
    # shows a STEP_UP with an empty reason list and nobody can explain it.
    check("RISK-002", (not integrity) or risk.recommended != "step_up",
          f"expected loss {rupees(risk.expected_loss_paise)} vs friction "
          f"{rupees(risk.friction_cost_paise)}; {risk.reasons[0] if risk.reasons else ''}",
          hard=False)

    blocked = totals.total_paise if (deny or step) else 0
    if deny:
        return Authorization(Verdict.DENY, cl,
                             f"refused by {', '.join(c.clause_id for c in cl if not c.passed)}",
                             policy.version, cf, blocked)
    if step:
        # Explain with the clause that actually escalated, not a concatenation
        # of everything the engines had to say. A STEP_UP a human cannot read is
        # the same as no explanation at all.
        failed = [c for c in cl if not c.passed]
        if failed:
            why = failed[0].detail
            head = ("this is more than you authorised" if failed[0].clause_id == "CEIL-001"
                    else "the price moved after you were shown it"
                    if failed[0].clause_id == "CAT-001"
                    else "the order drifted from what you asked for"
                    if failed[0].clause_id.startswith("DRIFT")
                    else "we are not confident enough to do this unasked"
                    if failed[0].clause_id.startswith(("CONF", "RISK"))
                    else "this is not something an agent buys on its own"
                    if failed[0].clause_id == "RESTRICT-001"
                    else "the agent added something that goes past your limit"
                    if failed[0].clause_id == "AGENT-001"
                    else "this needs your confirmation")
            reason = f"{head} ({failed[0].clause_id}: {why})"
        else:
            reason = (risk.reasons[0] if risk.reasons
                      else "this needs your confirmation")
        return Authorization(Verdict.STEP_UP, cl, reason, policy.version, cf, blocked)
    return Authorization(
        Verdict.AUTO, cl,
        f"inside the authorised envelope; expected loss "
        f"{rupees(risk.expected_loss_paise)} below the cost of asking",
        policy.version, None, 0)
