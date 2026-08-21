"""Merchant catalog: read, search, rank, mutate.

The catalog is MUTABLE on purpose. Price changes, stock-outs and shipping
changes between selection and payment are the single most common way a real
agentic purchase drifts out of its authorisation, so they must be first-class
and testable, not an afterthought.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from pydantic import BaseModel

from ..money import Paise


class Product(BaseModel):
    product_id: str
    merchant_id: str
    name: str
    category: str
    subcategory: str | None = None
    price_paise: Paise
    mrp_paise: Paise
    margin_bps: int
    rating: float
    reviews: int
    inventory: int
    attributes: list[str]
    premium: bool
    ship_days: int
    catalog_version: int

    @property
    def discount_pct(self) -> float:
        if self.mrp_paise <= 0:
            return 0.0
        return round(100.0 * (self.mrp_paise - self.price_paise) / self.mrp_paise, 1)

    @property
    def margin_paise(self) -> Paise:
        return self.price_paise * self.margin_bps // 10_000


class Merchant(BaseModel):
    merchant_id: str
    name: str
    rating: float
    free_ship_over_paise: Paise
    base_ship_paise: Paise
    risk_tier: str


def _matches_terms(p: "Product", terms: list[str]) -> bool:
    """Does this product plausibly ANSWER what the human named?

    Three ways to match, in order of how the catalog actually encodes meaning:
      * the product NAME contains the phrase   ("Kinetic Yoga Mat 6mm" / "yoga mat")
      * the CATEGORY is the phrase             ("running shoes")
      * an ATTRIBUTE equals the phrase once hyphens are normalised
        ("earbuds" == "earbuds"), but NOT a longer compound
        ("earbuds-accessory" -> "earbuds accessory", which is a case for the
        buds, not a pair of buds)

    Substring matching on attributes was the first attempt and it bought a
    "Northbeam Buds Case" when the human said "earbuds". FAILURES.md 2026-08-21 18:20.
    """
    name = p.name.lower()
    cat = p.category.lower()
    attrs = {a.lower().replace("-", " ") for a in p.attributes}
    for t in terms:
        t = t.lower().strip()
        if not t:
            continue
        singular = t[:-1] if t.endswith("s") else t
        if t in name or singular in name:
            return True
        if t == cat or t in cat or cat in t:
            return True
        if t in attrs or singular in attrs:
            return True
    return False


def _prod(r: sqlite3.Row) -> Product:
    d = dict(r)
    d["attributes"] = json.loads(d["attributes"])
    d["premium"] = bool(d["premium"])
    d.pop("active", None)
    return Product(**d)


class Catalog:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    # ---------- version ----------
    def version(self) -> int:
        r = self.db.execute(
            "SELECT MAX(version) v FROM catalog_versions").fetchone()
        return int(r["v"] or 0)

    def bump_version(self, note: str, now: datetime) -> int:
        self.db.execute(
            "INSERT INTO catalog_versions (created_at, note) VALUES (?,?)",
            (now.isoformat(), note))
        return self.version()

    # ---------- read ----------
    def get(self, product_id: str) -> Product | None:
        r = self.db.execute("SELECT * FROM products WHERE product_id=? AND active=1",
                            (product_id,)).fetchone()
        return _prod(r) if r else None

    def merchant(self, merchant_id: str) -> Merchant | None:
        r = self.db.execute("SELECT * FROM merchants WHERE merchant_id=?",
                            (merchant_id,)).fetchone()
        return Merchant(**dict(r)) if r else None

    # ---------- search ----------
    def search(self, *, category: str | None = None, max_price_paise: Paise | None = None,
               required: list[str] | None = None, excluded: list[str] | None = None,
               merchants: list[str] | None = None, in_stock: bool = True,
               terms: list[str] | None = None, limit: int = 40) -> list[Product]:
        q = "SELECT * FROM products WHERE active=1"
        args: list = []
        if category:
            q += " AND category=?"; args.append(category)
        if max_price_paise is not None:
            q += " AND price_paise<=?"; args.append(max_price_paise)
        if in_stock:
            q += " AND inventory>0"
        if merchants:
            q += " AND merchant_id IN (%s)" % ",".join("?" * len(merchants))
            args += merchants
        rows = [_prod(r) for r in self.db.execute(q, args)]
        # `terms` is what the human actually asked FOR -- "yoga mat", "earbuds".
        # A category filter alone lets the agent buy a gym towel when the human
        # said yoga mat, and score zero drift for it. See FAILURES.md 2026-08-21 18:20.
        tset = [t.lower() for t in (terms or []) if t]
        req = set(a.lower() for a in (required or []))
        exc = set(a.lower() for a in (excluded or []))
        out = []
        for p in rows:
            attrs = set(a.lower() for a in p.attributes)
            if req and not req.issubset(attrs):
                continue
            if exc and attrs & exc:
                continue
            if tset and not _matches_terms(p, tset):
                continue
            out.append(p)
        return out[:limit]

    def relations(self, product_id: str, kind: str) -> list[tuple[Product, str, float]]:
        rows = self.db.execute(
            "SELECT r.related_id, r.reason, r.strength FROM relations r"
            " WHERE r.product_id=? AND r.kind=?", (product_id, kind)).fetchall()
        out = []
        for r in rows:
            p = self.get(r["related_id"])
            if p and p.inventory > 0:
                out.append((p, r["reason"], float(r["strength"])))
        out.sort(key=lambda t: -t[2])
        return out

    # ---------- mutate (for chaos + scenarios) ----------
    def set_price(self, product_id: str, price_paise: Paise, now: datetime,
                  note: str = "price change") -> int:
        v = self.bump_version(note, now)
        self.db.execute(
            "UPDATE products SET price_paise=?, catalog_version=? WHERE product_id=?",
            (price_paise, v, product_id))
        return v

    def set_inventory(self, product_id: str, inventory: int, now: datetime) -> int:
        v = self.bump_version(f"inventory {product_id}={inventory}", now)
        self.db.execute(
            "UPDATE products SET inventory=?, catalog_version=? WHERE product_id=?",
            (inventory, v, product_id))
        return v

    def deactivate(self, product_id: str, now: datetime) -> int:
        v = self.bump_version(f"delist {product_id}", now)
        self.db.execute(
            "UPDATE products SET active=0, catalog_version=? WHERE product_id=?",
            (v, product_id))
        return v

    def set_shipping(self, merchant_id: str, base_ship_paise: Paise,
                     free_over_paise: Paise, now: datetime) -> int:
        v = self.bump_version(f"shipping {merchant_id}", now)
        self.db.execute(
            "UPDATE merchants SET base_ship_paise=?, free_ship_over_paise=?"
            " WHERE merchant_id=?", (base_ship_paise, free_over_paise, merchant_id))
        return v


# ---------- ranking ----------
def _norm(v: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.5
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))


def rank(products: list[Product], objective: str, budget_paise: Paise | None
         ) -> list[tuple[Product, float, str]]:
    """Deterministic ranking. The LLM does not score products.

    Kept explicit and cheap so the same ranking is reproducible in eval,
    in the demo, and in a dispute six months later.
    """
    if not products:
        return []
    prices = [p.price_paise for p in products]
    lo, hi = min(prices), max(prices)
    rats = [p.rating for p in products]
    rlo, rhi = min(rats), max(rats)
    out = []
    for p in products:
        cheap = 1.0 - _norm(p.price_paise, lo, hi)
        good = _norm(p.rating, rlo, rhi)
        trust = _norm(min(p.reviews, 2000), 0, 2000)
        fast = 1.0 - _norm(p.ship_days, 1, 7)
        if objective == "cheapest":
            score = 0.80 * cheap + 0.15 * good + 0.05 * trust
            why = "lowest price that still meets your requirements"
        elif objective == "best_rated":
            score = 0.60 * good + 0.25 * trust + 0.15 * cheap
            why = "highest rated option within your constraints"
        elif objective == "fastest_delivery":
            score = 0.60 * fast + 0.25 * good + 0.15 * cheap
            why = "quickest delivery within your constraints"
        else:  # best_value
            score = 0.40 * good + 0.30 * cheap + 0.20 * trust + 0.10 * fast
            why = "best balance of rating, price and delivery in your budget"
        if budget_paise and p.price_paise > budget_paise:
            score -= 1.0
        out.append((p, round(score, 4), why))
    out.sort(key=lambda t: -t[1])
    return out
