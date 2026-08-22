"""Vector retrieval over the merchant catalog, with the hard filter on both
sides of it.

The order matters and it is the whole point:

    semantic retrieval (top K)
        -> HARD FILTER
            -> rerank
                -> HARD FILTER again

Retrieval is allowed to be fuzzy. Ranking is allowed to be fuzzy. Neither is
allowed to decide what is *eligible*, because eligibility is where the human's
stated limits live. A product costing Rs 139 must never survive a Rs 20
ceiling, no matter how similar its embedding is or how confidently a reranker
recommends it -- and the only way to guarantee that is to filter after every
stage that could reorder or reintroduce a candidate.

The second filter looks redundant. It is not: a reranker is the one component
in this pipeline that could be a model, and a model that returns a product id
is a model that can return a product id nobody offered it.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..domain.catalog import Product, _prod
from ..money import Paise
from .embed import Embedder, cosine


@dataclass(frozen=True)
class Hit:
    product: Product
    score: float


def product_text(p: Product) -> str:
    """What a product 'says' about itself. Name, shelf and attributes -- not
    price, which is a constraint rather than a meaning, and not the merchant,
    which would make every Daily Mart item similar to every other one."""
    attrs = " ".join(a.replace("-", " ") for a in p.attributes
                     if not a.startswith("restricted"))
    return f"{p.name} {p.category} {p.subcategory or ''} {attrs}".strip()


class VectorIndex:
    """A flat index. 186 products is 186 dot products of length 512, which is
    well under a millisecond -- an ANN structure here would be architecture
    cosplay. It becomes a real question at ~10^5 products, and the production
    note in ARCHITECTURE.md says so rather than pre-building it."""

    def __init__(self, embedder: Embedder, vectors: dict[str, list[float]],
                 catalog_version: int):
        self.embedder = embedder
        self.vectors = vectors
        self.catalog_version = catalog_version

    # Vectors are a pure function of (embedder, product text). The evaluation
    # builds a fresh App per case -- 540 of them -- and re-embedding an
    # unchanged catalog 540 times took the suite from 13s to over two minutes.
    # Keyed on the actual text, so a price change does not invalidate it and a
    # renamed product does.
    _CACHE: dict[str, dict[str, list[float]]] = {}

    @classmethod
    def build(cls, db: sqlite3.Connection, embedder: Embedder,
              catalog_version: int) -> "VectorIndex":
        rows = [_prod(r) for r in db.execute(
            "SELECT * FROM products WHERE active=1 ORDER BY product_id")]
        texts = {p.product_id: product_text(p) for p in rows}
        from ..models import canonical, sha
        key = sha(canonical({"e": embedder.name, "t": texts}))
        cached = cls._CACHE.get(key)
        if cached is None:
            cached = {pid: embedder.embed(t) for pid, t in texts.items()}
            if len(cls._CACHE) > 8:          # bounded: this is a cache, not a leak
                cls._CACHE.clear()
            cls._CACHE[key] = cached
        return cls(embedder, cached, catalog_version)

    def search(self, query: str, db: sqlite3.Connection, k: int = 24
               ) -> list[Hit]:
        q = self.embedder.embed(query)
        scored = sorted(((cosine(q, v), pid) for pid, v in self.vectors.items()),
                        reverse=True)[:k]
        out: list[Hit] = []
        for score, pid in scored:
            row = db.execute(
                "SELECT * FROM products WHERE product_id=? AND active=1",
                (pid,)).fetchone()
            if row is not None:
                out.append(Hit(_prod(row), round(score, 4)))
        return out


def hard_filter(products: list[Product], *,
                max_price_paise: Paise | None,
                required: list[str] | None,
                excluded: list[str] | None,
                merchants: list[str] | None,
                in_stock: bool = True) -> list[Product]:
    """The only thing in the retrieval path that is not allowed to be clever.

    Every constraint here came from a human sentence. None of them is a
    preference, none of them is weighted, and none of them can be traded off
    against relevance. If this function drops everything, the correct answer is
    'nothing you asked for is available under that limit' -- which is a real
    answer, and a better one than a product that fits the vibe and breaks the
    budget.
    """
    req = {a.lower() for a in (required or [])}
    exc = {a.lower() for a in (excluded or [])}
    keep: list[Product] = []
    for p in products:
        if max_price_paise is not None and p.price_paise > max_price_paise:
            continue
        if in_stock and p.inventory <= 0:
            continue
        if merchants and p.merchant_id not in merchants:
            continue
        attrs = {a.lower() for a in p.attributes}
        if req and not req.issubset(attrs):
            continue
        if exc and attrs & exc:
            continue
        keep.append(p)
    return keep
