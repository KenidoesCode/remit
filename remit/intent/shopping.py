"""utterance -> IntentEnvelope.

Two implementations behind one Protocol:

  RuleCompiler   deterministic, offline, no API key. Used by every test, the
                 whole 500-case evaluation, and CI. Reproducible forever.
  LLMCompiler    Claude with a strict JSON schema, used when a key is present.
                 It may SELECT a category and objective and flag ambiguity.
                 It may NOT compute an amount -- the amount always comes from
                 `amounts.extract`, and the model's own claim is kept only so
                 the disagreement can be measured.

Confidence is not a vibe. It starts at the amount-extraction confidence and
is reduced by each thing we could not resolve. It is later calibrated against
labelled outcomes (remit/risk/calibration.py), because a raw confidence is
not a probability and expected-loss arithmetic on an uncalibrated number is
arithmetic on a lie.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Protocol

from ..money import Paise
from .amounts import AmountCandidate, best_ceiling
from ..domain.intent import IntentEnvelope, new_intent

CATEGORY_WORDS = {
    "running shoes": ["running shoe", "running shoes", "runners", "jogging shoe",
                      "shoe", "shoes", "sneaker", "sneakers", "trainers"],
    "fitness accessories": ["yoga mat", "dumbbell", "resistance band", "fitness",
                            "gym", "skipping rope", "foam roller", "shaker"],
    "electronics accessories": ["earbuds", "headphone", "headphones", "charger",
                                "power bank", "cable", "mouse", "keyboard",
                                "webcam", "adapter"],
    "travel accessories": ["backpack", "luggage", "duffel", "travel", "neck pillow",
                           "packing cube", "suitcase"],
    "home office": ["desk", "chair", "monitor stand", "lamp", "office", "footrest"],
    "personal care": ["shampoo", "sunscreen", "trimmer", "moisturis", "moisturiz",
                      "deodorant", "face wash", "condom", "contracept",
                      "sanitary pad", "tampon", "razor", "shaving", "toothpaste",
                      "toothbrush", "soap", "handwash", "hand wash"],
    # Everyday shelves. Matching is substring, and later entries win, so the
    # words here are deliberately specific: "oil" is inside "toilet", "rice" is
    # inside "price", "pen" is inside "expensive". Each of those was a real
    # mis-categorisation before the leading spaces and qualifiers went in.
    "groceries": ["chips", "namkeen", "biscuit", "cookie", "noodle", "maggi",
                  "chocolate", "basmati", " rice", "atta", "flour", "toor dal",
                  "cooking oil", "groundnut oil", "sugar", " salt", "pasta",
                  "corn flakes", "cereal", "peanut", "nachos", "snack"],
    "beverages": ["juice", "cola", "soft drink", "soda", "energy drink",
                  "coffee", "green tea", "tea bags", "assam tea", "chai",
                  "mineral water", "drinking water", "coconut water"],
    "household": ["detergent", "dishwash", "dish wash", "floor cleaner",
                  "toilet cleaner", "garbage bag", "trash bag", "tissue",
                  "toilet paper", "mop", "scrub pad"],
    "baby care": ["diaper", "nappy", "nappies", "baby wipe", "baby lotion",
                  "baby shampoo", "baby "],
    "stationery": ["notebook", "journal", "gel pen", "ballpoint", "pens",
                   "highlighter", "marker", "sticky note", "document file"],
    "pet supplies": ["dog food", "puppy food", "cat food", "cat litter",
                     "pet shampoo", "pet food", " pet "],
    "otc medicine": ["paracetamol", "antacid", "cough syrup", "thermometer",
                     "first aid", "bandage", " ors ", "medicine", "tablet strip",
                     "pharmacy", "chemist"],
    "alcohol": ["beer", "whisky", "whiskey", "single malt", "wine", "vodka",
                "dark rum", "liquor", "booze", "daru", "scotch"],
}
OBJECTIVES = {
    "best_value": ["best value", "value for money", "good value", "worth"],
    "cheapest": ["cheapest", "lowest price", "sasta", "budget option", "least expensive"],
    "best_rated": ["best rated", "highest rated", "top rated", "best reviews", "best one"],
    "fastest_delivery": ["fastest", "quickest", "urgent", "today", "asap", "jaldi"],
}
BUY_WORDS = ["buy", "purchase", "order", "checkout", "pay for", "get me", "kharid",
             "book"]
PREMIUM_WORDS = ["premium", "high end", "flagship", "pro ", "top of the line"]
QTY = re.compile(r"\b([1-9][0-9]?)\s*(?:x|pcs?|pieces?|pairs?|units?)\b", re.I)


class IntentCompiler(Protocol):
    def compile(self, utterance: str, user_id: str, now: datetime
                ) -> tuple[IntentEnvelope | None, dict]: ...


class RuleCompiler:
    """Deterministic. Returns (envelope, telemetry). None means ABSTAIN --
    a first-class outcome that lands on the risk-coverage curve, never an
    exception and never a guess."""

    def compile(self, utterance: str, user_id: str, now: datetime
                ) -> tuple[IntentEnvelope | None, dict]:
        u = utterance.lower()
        conf = 1.0
        notes: list[str] = []

        category = None
        terms: list[str] = []
        for cat, words in CATEGORY_WORDS.items():
            hits = [wd for wd in words if wd in u]
            if hits:
                category = cat
                # Two kinds of category. If the category name is ITSELF a product
                # type ("running shoes"), then "runners" and "running shoe" are
                # just synonyms for it and the canonical term is the category.
                # If the category is a bucket ("fitness accessories"), the noun
                # the human said IS the constraint ("yoga mat"), and losing it
                # is how an agent buys a gym towel for someone who asked for a
                # yoga mat.
                if cat in words:
                    terms = [cat]
                else:
                    terms = [max(hits, key=len)]
                break
        if category is None:
            notes.append("no recognisable category")
            conf -= 0.45

        top, alts = best_ceiling(utterance)
        ceiling: Paise | None = top.paise if top else None
        if top is None:
            notes.append("no amount found")
            conf -= 0.25
        else:
            conf = min(conf, top.confidence + 0.04)
            if alts:
                notes.append(
                    f"{len(alts)} competing amount(s): "
                    + ", ".join(f"Rs {a.rupees()}" for a in alts))
                conf -= 0.12 * len(alts)

        objective = "best_value"
        for obj, words in OBJECTIVES.items():
            if any(wd in u for wd in words):
                objective = obj
                break

        qty = 1
        m = QTY.search(utterance)
        if m:
            qty = int(m.group(1))

        # AMBIGUITY THAT MATTERS: "buy 2x earbuds under 3000".
        # Is Rs 3,000 per unit or for the lot? The per-unit reading doubles what
        # the human is charged. We take the CONSERVATIVE reading -- a total
        # ceiling -- unless the utterance says otherwise, and we record the
        # ambiguity and cut confidence rather than choosing silently.
        per_unit_markers = ("each", "per pair", "per unit", "apiece", "a piece",
                            "per piece", "ek ka")
        ceiling_is_per_unit = qty == 1 or any(wd in u for wd in per_unit_markers)
        if ceiling is not None and not ceiling_is_per_unit:
            notes.append(
                f"quantity {qty} with one stated amount: reading Rs {ceiling // 100} "
                f"as a TOTAL ceiling, not per-unit")
            conf -= 0.15

        authority = any(wd in u for wd in BUY_WORDS)
        if not authority:
            notes.append("no explicit purchase authority in the utterance")

        required = ["premium"] if any(wd in u for wd in PREMIUM_WORDS) else []
        conf = max(0.0, min(1.0, conf))

        telemetry = {
            "amount_candidates": [
                {"paise": c.paise, "surface": c.surface, "confidence": c.confidence,
                 "form": c.form} for c in ([top] + alts if top else [])],
            "rejected_amounts": [
                {"paise": a.paise, "surface": a.surface} for a in alts],
            "notes": notes, "raw_confidence": round(conf, 4),
            "compiler": "rule",
        }
        # Abstain when there is nothing to shop FOR.
        #
        # The old condition also required a missing amount, which meant
        # "buy a helicopter under 500000" -- no category, no product term, a
        # perfectly clear budget -- fell through into a catalog-wide search and
        # came back with a yoga mat. An unrecognised noun plus a large number is
        # the single most dangerous input this system can receive, and it was
        # the one input that skipped the boundary entirely. FAILURES.md #13.
        #
        # A stated amount is not a reason to buy something. It is only a limit
        # on what may be spent once there is a thing to buy.
        if category is None and not terms:
            notes.append("nothing in the utterance names something this catalog sells")
            return None, telemetry | {"abstained": True,
                                      "reason": "no groundable product"}
        if category is None and ceiling is None:
            return None, telemetry | {"abstained": True}

        env = new_intent(
            user_id=user_id, utterance=utterance, now=now,
            category=category, product_terms=terms,
            max_price_paise=ceiling if ceiling_is_per_unit else None,
            max_total_paise=None if ceiling_is_per_unit else ceiling,
            currency="INR",
            quantity=qty, objective=objective, required_attributes=required,
            purchase_authority=authority, parse_confidence=round(conf, 4))
        return env, telemetry | {"abstained": False}


class LLMCompiler:
    """Claude-backed. Selects category/objective/attributes and flags
    ambiguity. Amounts still come from the deterministic extractor; the
    model's claimed amount is recorded only to measure disagreement."""

    SCHEMA = {
        "type": "object",
        "properties": {
            "category": {"type": ["string", "null"], "enum": list(CATEGORY_WORDS) + [None]},
            "objective": {"type": "string", "enum": list(OBJECTIVES)},
            "quantity": {"type": "integer", "minimum": 1, "maximum": 99},
            "required_attributes": {"type": "array", "items": {"type": "string"}},
            "excluded_attributes": {"type": "array", "items": {"type": "string"}},
            "purchase_authority": {"type": "boolean"},
            "stated_amount_rupees": {"type": ["number", "null"]},
            "ambiguity": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["category", "objective", "quantity", "purchase_authority",
                     "confidence"],
    }

    def __init__(self, client=None, model: str = "claude-sonnet-4-5",
                 fallback: IntentCompiler | None = None):
        self.client = client
        self.model = model
        self.fallback = fallback or RuleCompiler()

    def compile(self, utterance: str, user_id: str, now: datetime
                ) -> tuple[IntentEnvelope | None, dict]:
        if self.client is None:
            env, tel = self.fallback.compile(utterance, user_id, now)
            return env, tel | {"compiler": "rule(fallback: no client)"}
        try:
            msg = self.client.messages.create(
                model=self.model, max_tokens=600,
                system=("Extract a shopping intent. You MAY choose the category, "
                        "objective, quantity, attributes and whether the human "
                        "granted purchase authority. You MUST NOT invent or "
                        "compute a budget: report stated_amount_rupees only if it "
                        "appears verbatim. Return JSON matching the schema."),
                messages=[{"role": "user", "content": utterance}],
                tools=[{"name": "emit", "description": "Emit the parsed intent.",
                        "input_schema": self.SCHEMA}],
                tool_choice={"type": "tool", "name": "emit"})
            blocks = [b for b in msg.content if getattr(b, "type", "") == "tool_use"]
            if not blocks:
                raise ValueError("model returned no structured output")
            data = blocks[0].input
        except Exception as e:
            # Degradation always moves toward MORE friction, never more autonomy.
            env, tel = self.fallback.compile(utterance, user_id, now)
            if env:
                env.parse_confidence = min(env.parse_confidence, 0.5)
            return env, tel | {"compiler": "rule(fallback)", "llm_error": str(e)}

        top, alts = best_ceiling(utterance)
        ceiling = top.paise if top else None
        model_amt = data.get("stated_amount_rupees")
        disagreement = 0
        if model_amt and ceiling:
            disagreement = abs(int(model_amt * 100) - ceiling)

        conf = float(data.get("confidence", 0.5))
        if top:
            conf = min(conf, top.confidence + 0.04)
        if disagreement > 100:      # more than Re 1 apart -> do not trust either
            conf = min(conf, 0.45)
        if alts:
            conf -= 0.10 * len(alts)
        conf = max(0.0, min(1.0, conf))

        _q = int(data.get("quantity", 1))
        _per_unit = _q == 1 or any(wd in utterance.lower() for wd in
                                   ("each", "per pair", "per unit", "apiece"))
        env = new_intent(
            user_id=user_id, utterance=utterance, now=now,
            category=data.get("category"),
            max_price_paise=ceiling if _per_unit else None,
            max_total_paise=None if _per_unit else ceiling, currency="INR",
            quantity=_q,
            objective=data.get("objective", "best_value"),
            required_attributes=data.get("required_attributes", []),
            excluded_attributes=data.get("excluded_attributes", []),
            purchase_authority=bool(data.get("purchase_authority")),
            parse_confidence=round(conf, 4))
        tel = {"compiler": "llm", "model": self.model,
               "model_stated_amount_paise": int(model_amt * 100) if model_amt else None,
               "amount_disagreement_paise": disagreement,
               "ambiguity": data.get("ambiguity", []),
               "rejected_amounts": [{"paise": a.paise, "surface": a.surface}
                                    for a in alts],
               "raw_confidence": round(conf, 4), "abstained": False}
        return env, tel
