"""The drift engine.

DEFINITION (ours, not an industry standard -- say so):
  Intent-to-transaction drift is the degree to which the transaction about
  to be executed deviates from the intent envelope the human authorised.

It is deliberately NOT one number that an LLM produces. It is a vector of
named dimensions, each computed by a small pure function with a documented
formula, then combined with published weights. Anyone can recompute it, and
a reviewer can argue with the weights rather than with a black box.

FORMULA
  For each dimension d we compute a bounded severity s_d in [0, 1].
    s_d = 0  -> the transaction is exactly what was authorised on d
    s_d = 1  -> maximal violation on d
  Ratio-style dimensions (price, total, shipping, upsell, cross_sell) use
    s = clamp((actual - authorised) / max(authorised, 1), 0, 1)
  i.e. exceeding the ceiling by 100% or more saturates at 1.0.
  Categorical dimensions (category, product, merchant, currency, authority,
  catalog_version) are 0 or 1.
  Quantity uses  s = clamp(|actual - authorised| / max(authorised, 1), 0, 1).

  score = sum(w_d * s_d) / sum(w_d over dimensions that were evaluable)

Renormalising over *evaluable* dimensions matters: if the human never stated
a category, category drift is not measurable and must not silently count as
zero drift. Unstated constraints are reported as `not_evaluable`, never as
compliance.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from ..money import Paise, rupees
from .catalog import term_answers
from .cart import Cart, Totals
from .intent import IntentEnvelope

# Published weights. Tunable in policy; printed in the UI and the docs.
DEFAULT_WEIGHTS: dict[str, float] = {
    "total": 3.0,        # the all-in number the human will actually be charged
    "price": 2.0,        # per-unit price against a stated ceiling
    "category": 2.5,     # bought something from the wrong category
    "merchant": 1.5,
    "quantity": 2.0,
    "currency": 3.0,
    "shipping": 1.0,     # shipping specifically, so it can be named as a cause
    "upsell": 1.0,
    "cross_sell": 1.0,
    "authority": 3.0,    # no purchase authority was granted at all
    "catalog_version": 0.5,
    "attributes": 1.0,
    "price_change": 2.0,   # charged more than was shown at selection
    "product_match": 2.5,  # bought a different THING inside the right category
}


def _ratio(actual: int, ceiling: int) -> float:
    if ceiling <= 0:
        return 0.0
    return max(0.0, min(1.0, (actual - ceiling) / ceiling))


class DriftResult(BaseModel):
    drift_detected: bool
    score: float
    dimensions: dict[str, float]
    not_evaluable: list[str]
    reasons: list[str]
    weights: dict[str, float]
    over_ceiling_paise: Paise = 0

    @property
    def worst(self) -> tuple[str, float]:
        if not self.dimensions:
            return ("none", 0.0)
        k = max(self.dimensions, key=lambda d: self.dimensions[d])
        return (k, self.dimensions[k])


def compute_drift(*, env: IntentEnvelope, cart: Cart, totals: Totals,
                  catalog_version: int, weights: dict[str, float] | None = None,
                  stale_pricing: bool | None = None) -> DriftResult:
    w = dict(weights or DEFAULT_WEIGHTS)
    dims: dict[str, float] = {}
    skipped: list[str] = []
    reasons: list[str] = []
    over = 0

    # --- authority ---
    if env.purchase_authority:
        dims["authority"] = 0.0
    else:
        dims["authority"] = 1.0
        reasons.append("the human did not grant purchase authority for this intent")

    # --- total vs the all-in ceiling ---
    ceiling = env.ceiling_paise()
    if ceiling is None:
        skipped.append("total")
    else:
        dims["total"] = _ratio(totals.total_paise, ceiling)
        if totals.total_paise > ceiling:
            over = totals.total_paise - ceiling
            reasons.append(
                f"final total {rupees(totals.total_paise)} exceeds the authorised "
                f"{rupees(ceiling)} by {rupees(over)}")

    # --- per-unit price ---
    primary = cart.primary_line
    if env.max_price_paise is None or primary is None:
        skipped.append("price")
    else:
        dims["price"] = _ratio(primary.unit_price_paise, env.max_price_paise)
        if primary.unit_price_paise > env.max_price_paise:
            reasons.append(
                f"unit price {rupees(primary.unit_price_paise)} exceeds the stated "
                f"maximum {rupees(env.max_price_paise)}")

    # --- shipping as a named cause ---
    if ceiling is None:
        skipped.append("shipping")
    else:
        head = totals.total_paise - totals.shipping_paise
        if totals.shipping_paise > 0 and head <= ceiling < totals.total_paise:
            dims["shipping"] = 1.0
            reasons.append(
                f"shipping of {rupees(totals.shipping_paise)} is what pushes this "
                f"transaction outside the authorised amount")
        else:
            dims["shipping"] = 0.0

    # --- category ---
    if not env.category or primary is None:
        skipped.append("category")
    else:
        same = primary.category.lower() == env.category.lower()
        dims["category"] = 0.0 if same else 1.0
        if not same:
            reasons.append(
                f"selected item is in '{primary.category}', the human asked for "
                f"'{env.category}'")

    # --- did we buy the thing they actually named? ---
    if not env.product_terms or primary is None:
        skipped.append("product_match")
    else:
        # Same predicate the catalog used to allow this product to be
        # selected at all. A stricter test here does not make the system
        # safer -- it makes two components disagree about one question, and
        # the human pays for the disagreement with an interruption.
        hit = term_answers(name=primary.name, category=primary.category,
                           attributes=list(primary.attributes),
                           terms=env.product_terms)
        dims["product_match"] = 0.0 if hit else 1.0
        if not hit:
            reasons.append(
                f"the human asked for {env.product_terms[0]!r}; the cart contains "
                f"{primary.name!r}")

    # --- merchant ---
    if not env.merchant_constraints:
        skipped.append("merchant")
    else:
        bad = sorted({l.merchant_id for l in cart.lines
                      if l.merchant_id not in env.merchant_constraints})
        dims["merchant"] = 1.0 if bad else 0.0
        if bad:
            reasons.append(f"merchant(s) {bad} outside the authorised list")

    # --- quantity ---
    qty = sum(l.qty for l in cart.lines if l.origin == "primary")
    dims["quantity"] = max(0.0, min(1.0, abs(qty - env.quantity) / max(env.quantity, 1)))
    if qty != env.quantity:
        reasons.append(f"quantity {qty} differs from the authorised {env.quantity}")

    # --- currency (single-currency system today; dimension kept explicit) ---
    dims["currency"] = 0.0 if env.currency == "INR" else 1.0
    if env.currency != "INR":
        reasons.append(f"currency {env.currency} is not supported by this rail")

    # --- upsell / cross-sell added value beyond the authorised head ---
    up = sum(l.line_paise for l in cart.lines if l.origin == "upsell")
    cross = sum(l.line_paise for l in cart.lines if l.origin == "cross_sell")
    if ceiling is None:
        skipped += ["upsell", "cross_sell"]
    else:
        dims["upsell"] = _ratio(totals.total_paise, ceiling) if up else 0.0
        dims["cross_sell"] = _ratio(totals.total_paise, ceiling) if cross else 0.0
        agent_added = [l for l in cart.lines
                       if l.origin in ("upsell", "cross_sell")
                       and l.accepted_by == "agent"]
        if agent_added and totals.total_paise > ceiling:
            reasons.append(
                "agent-added items are part of what takes this transaction "
                "outside the authorised amount")

    # --- required / excluded attributes ---
    if not (env.required_attributes or env.excluded_attributes) or primary is None:
        skipped.append("attributes")
    else:
        dims["attributes"] = 0.0   # enforced at search time; 0 unless violated

    # --- price changed between what was shown and what will be charged ---
    shown_total = sum((l.shown_price_paise or l.unit_price_paise) * l.qty
                      for l in cart.lines)
    charged_head = totals.subtotal_paise
    if shown_total <= 0:
        skipped.append("price_change")
    else:
        dims["price_change"] = _ratio(charged_head, shown_total)
        if charged_head > shown_total:
            reasons.append(
                f"price rose after selection: shown {rupees(shown_total)}, "
                f"about to charge {rupees(charged_head)}")

    # --- catalog staleness ---
    # A version counter moving is not, by itself, drift. A catalog edit that
    # does not touch anything in this cart changes the integer and nothing else.
    # What matters is whether the prices we are about to charge are the prices
    # we showed. The caller computes that materially; the version is kept in the
    # record for provenance. (Found while chasing 84 false-positive step-ups in
    # the first full eval run -- FAILURES.md 2026-08-21 15:40.)
    stale = (catalog_version != cart.catalog_version
             if stale_pricing is None else stale_pricing)
    dims["catalog_version"] = 1.0 if stale else 0.0
    if stale:
        reasons.append(
            f"prices in this cart no longer match the catalog "
            f"(cart priced at v{cart.catalog_version}, catalog is now "
            f"v{catalog_version})")

    usable = {d: w.get(d, 1.0) for d in dims}
    denom = sum(usable.values()) or 1.0
    score = sum(usable[d] * dims[d] for d in dims) / denom
    return DriftResult(
        drift_detected=any(v > 0 for v in dims.values()),
        score=round(score, 4), dimensions={k: round(v, 4) for k, v in dims.items()},
        not_evaluable=sorted(set(skipped)), reasons=reasons,
        weights={k: usable[k] for k in dims}, over_ceiling_paise=over)
