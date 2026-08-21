"""Cart and pricing. Deterministic arithmetic, no model involvement.

Every rupee in a total is derived here from catalog ids x quantity plus
merchant shipping and discount rules. The AI selects line items; it never
computes a total. That separation is what makes CONF-002 (model/catalog
amount agreement) a meaningful check rather than a tautology.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from ..money import Paise
from .catalog import Catalog, Product


class CartLine(BaseModel):
    product_id: str
    name: str
    merchant_id: str
    category: str
    unit_price_paise: Paise          # what we are about to charge
    shown_price_paise: Paise = 0     # what was on screen when it was selected
    qty: int
    margin_bps: int
    origin: str            # 'primary' | 'upsell' | 'cross_sell'
    accepted_by: str       # 'intent' | 'human' | 'agent'
    reason: str = ""

    @property
    def line_paise(self) -> Paise:
        return self.unit_price_paise * self.qty

    @property
    def line_margin_paise(self) -> Paise:
        return self.line_paise * self.margin_bps // 10_000


class Totals(BaseModel):
    subtotal_paise: Paise
    shipping_paise: Paise
    discount_paise: Paise
    total_paise: Paise
    merchant_margin_paise: Paise


class Cart(BaseModel):
    cart_id: str
    intent_id: str
    intent_version: int
    catalog_version: int
    created_at: datetime
    lines: list[CartLine] = []
    state: str = "open"

    def add(self, line: CartLine) -> None:
        for l in self.lines:
            if l.product_id == line.product_id and l.origin == line.origin:
                l.qty += line.qty
                return
        self.lines.append(line)

    def remove(self, product_id: str) -> None:
        self.lines = [l for l in self.lines if l.product_id != product_id]

    @property
    def primary_line(self) -> CartLine | None:
        for l in self.lines:
            if l.origin == "primary":
                return l
        return self.lines[0] if self.lines else None


def new_cart(intent_id: str, intent_version: int, catalog_version: int,
             now: datetime) -> Cart:
    return Cart(cart_id="crt_" + uuid.uuid4().hex[:18], intent_id=intent_id,
                intent_version=intent_version, catalog_version=catalog_version,
                created_at=now)


def line_from(p: Product, qty: int, origin: str, accepted_by: str,
              reason: str = "") -> CartLine:
    return CartLine(product_id=p.product_id, name=p.name, merchant_id=p.merchant_id,
                    category=p.category, unit_price_paise=p.price_paise,
                    shown_price_paise=p.price_paise, qty=qty,
                    margin_bps=p.margin_bps, origin=origin,
                    accepted_by=accepted_by, reason=reason)


def price_cart(cart: Cart, catalog: Catalog) -> Totals:
    """Shipping is charged per distinct merchant, waived above that
    merchant's free-shipping threshold. This is why an upsell can *reduce*
    the total: crossing the threshold removes the shipping line. The revenue
    engine knows this; so does the drift engine."""
    subtotal = sum(l.line_paise for l in cart.lines)
    margin = sum(l.line_margin_paise for l in cart.lines)
    shipping = 0
    by_merchant: dict[str, int] = {}
    for l in cart.lines:
        by_merchant[l.merchant_id] = by_merchant.get(l.merchant_id, 0) + l.line_paise
    for mid, amt in by_merchant.items():
        m = catalog.merchant(mid)
        if not m:
            continue
        if m.free_ship_over_paise and amt >= m.free_ship_over_paise:
            continue
        shipping += m.base_ship_paise
    discount = 0
    return Totals(subtotal_paise=subtotal, shipping_paise=shipping,
                  discount_paise=discount,
                  total_paise=subtotal + shipping - discount,
                  merchant_margin_paise=margin)
