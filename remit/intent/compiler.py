"""utterance -> Intent.

The contract, and it is the most important boundary in the system:

    THE MODEL MAY SELECT. THE MODEL MAY NOT COMPUTE.

It chooses catalog item ids and quantities. `computed_amount_paise` is then
derived here, deterministically, from the catalog. The model's own claimed
amount is kept only so the disagreement can be measured (CONF-002).
"""
from __future__ import annotations

from typing import Protocol

from ..models import Alternative, CatalogItem, Intent, IntentItem, sha


class Catalog:
    def __init__(self, items: list[CatalogItem]):
        self.by_id = {i.item_id: i for i in items}

    def price(self, item_id: str) -> int:
        if item_id not in self.by_id:
            raise KeyError(f"{item_id} not in catalog")   # closed world
        return self.by_id[item_id].unit_price_paise

    def total(self, items: list[IntentItem]) -> int:
        return sum(self.price(i.item_id) * i.qty for i in items)


class IntentCompiler(Protocol):
    def compile(self, utterance: str, merchant_id: str,
                catalog: Catalog) -> Intent | None:
        """Returns None to ABSTAIN. Abstention is a first-class outcome,
        never an exception, and never a guess."""
        ...


def build_intent(
    *, utterance: str, merchant_id: str, catalog: Catalog,
    items: list[IntentItem], category: str, raw_confidence: float,
    stated_amount_paise: int | None = None,
    user_ceiling_paise: int | None = None,
    alternatives: list[Alternative] | None = None,
) -> Intent:
    return Intent(
        utterance_hash=sha(utterance.strip().lower()),
        merchant_id=merchant_id,
        category=category,
        items=items,
        computed_amount_paise=catalog.total(items),   # deterministic
        stated_amount_paise=stated_amount_paise,
        user_ceiling_paise=user_ceiling_paise,
        raw_confidence=raw_confidence,
        alternatives=alternatives or [],
    )


class StubCompiler:
    """Deterministic compiler for tests and CI. No API key, no network.

    Keeps the whole test suite runnable offline -- which matters at 2am on
    day 11 when you have no wifi and a bug to find.
    """
    def __init__(self, script: dict[str, dict]):
        self.script = script

    def compile(self, utterance: str, merchant_id: str,
                catalog: Catalog) -> Intent | None:
        spec = self.script.get(utterance.strip().lower())
        if spec is None:
            return None
        return build_intent(
            utterance=utterance, merchant_id=merchant_id, catalog=catalog,
            items=[IntentItem(**i) for i in spec["items"]],
            category=spec["category"],
            raw_confidence=spec["raw_confidence"],
            stated_amount_paise=spec.get("stated_amount_paise"),
            user_ceiling_paise=spec.get("user_ceiling_paise"),
            alternatives=[Alternative(**a) for a in spec.get("alternatives", [])],
        )
