"""Merchant revenue engine.

The optimisation the product actually performs:

    maximise   merchant margin
    subject to final transaction in the authorised intent envelope

Two design decisions worth defending:

1. **Nothing is added silently.** Every proposal carries a reason, the exact
   marginal cost, whether it changes the intent, and whether it needs a
   human. An offer the agent accepts on its own is recorded with
   accepted_by='agent' so drift can attribute it later.

2. **The engine proposes inside the envelope first.** It computes headroom
   (ceiling - current total) and only offers items that fit. Offers that do
   not fit are still returned, flagged `needs_human=True`, because hiding
   them would be optimising the metric rather than the merchant -- and a
   human may well say yes.

A genuinely nice case falls out of the shipping rule: an add-on can cross a
merchant's free-shipping threshold and REDUCE the total. `net_delta_paise`
captures that, and it is the honest way to show that revenue optimisation and
buyer interest are not always opposed.
"""
from __future__ import annotations

from pydantic import BaseModel

from ..money import Paise, rupees
from .cart import Cart, CartLine, line_from, price_cart
from .catalog import Catalog
from .intent import IntentEnvelope


class Offer(BaseModel):
    product_id: str
    name: str
    kind: str                 # 'upsell' | 'cross_sell'
    price_paise: Paise
    net_delta_paise: Paise    # what the TOTAL actually changes by
    margin_gain_paise: Paise
    reason: str
    relevance: float
    changes_intent: bool
    needs_human: bool
    headroom_after_paise: Paise | None


class RevenueEngine:
    def __init__(self, catalog: Catalog, aggressiveness: float = 1.0,
                 max_offers: int = 3, min_relevance: float = 0.35):
        self.catalog = catalog
        self.aggr = max(0.0, min(1.0, aggressiveness))
        self.max_offers = max_offers
        self.min_relevance = min_relevance

    def headroom(self, env: IntentEnvelope, cart: Cart) -> Paise | None:
        ceiling = env.ceiling_paise()
        if ceiling is None:
            return None
        return ceiling - price_cart(cart, self.catalog).total_paise

    def propose(self, env: IntentEnvelope, cart: Cart) -> list[Offer]:
        if self.aggr <= 0 or not cart.lines:
            return []
        anchor = cart.primary_line
        if anchor is None:
            return []
        current = price_cart(cart, self.catalog).total_paise
        ceiling = env.ceiling_paise()
        have = {l.product_id for l in cart.lines}

        candidates: list[Offer] = []
        for kind in ("cross_sell", "upsell"):
            for p, reason, strength in self.catalog.relations(anchor.product_id, kind):
                if p.product_id in have:
                    continue
                if strength < self.min_relevance:
                    continue
                trial = cart.model_copy(deep=True)
                trial.add(line_from(p, 1, kind, "agent", reason))
                new_total = price_cart(trial, self.catalog).total_paise
                delta = new_total - current
                headroom_after = None if ceiling is None else ceiling - new_total
                needs_human = ceiling is not None and new_total > ceiling
                candidates.append(Offer(
                    product_id=p.product_id, name=p.name, kind=kind,
                    price_paise=p.price_paise, net_delta_paise=delta,
                    margin_gain_paise=p.margin_paise, reason=reason,
                    relevance=round(strength, 3),
                    changes_intent=needs_human, needs_human=needs_human,
                    headroom_after_paise=headroom_after))

        # Rank by margin the merchant actually gains, weighted by relevance.
        candidates.sort(key=lambda o: -(o.margin_gain_paise * o.relevance))
        n = max(1, round(self.max_offers * self.aggr)) if candidates else 0
        return candidates[:n]

    def explain(self, offer: Offer) -> str:
        if offer.net_delta_paise < 0:
            return (f"{offer.name}: {offer.reason}. Adding it crosses the free-delivery "
                    f"threshold, so your total drops by {rupees(-offer.net_delta_paise)}.")
        if offer.needs_human:
            return (f"{offer.name}: {offer.reason}. Adds {rupees(offer.net_delta_paise)} "
                    f"and would take the order past what you authorised - needs your "
                    f"confirmation.")
        return (f"{offer.name}: {offer.reason}. Adds {rupees(offer.net_delta_paise)}, "
                f"still inside your limit.")
