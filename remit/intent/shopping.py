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
from .amounts import AmountCandidate, best_ceiling, detect_currency
from .grounding import Lexicon, RequestedItem, content_query, ground
from ..domain.intent import IntentEnvelope, new_intent

_DEFAULT_LEXICON: Lexicon | None = None


def default_lexicon() -> Lexicon:
    """The lexicon of a freshly seeded catalog.

    Only for callers that have no database of their own -- unit tests and the
    calibration script. Everything on the live path is handed the lexicon of
    the catalog it is actually shopping, because a grounder that knows words
    for products the merchant does not stock is a grounder that lies.
    """
    global _DEFAULT_LEXICON
    if _DEFAULT_LEXICON is None:
        from datetime import timezone
        from ..db import connect
        from ..domain.catalog import Catalog
        from ..seed.catalog_seed import seed
        db = connect(":memory:")
        seed(db, datetime(2026, 1, 1, tzinfo=timezone.utc))
        _DEFAULT_LEXICON = Lexicon.from_db(db, Catalog(db).version())
    return _DEFAULT_LEXICON

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
# What counts as "the human told the agent to spend money". Kept literal and
# auditable: AUTH-001 is a hard clause, and a person saying "i need shampoo
# under 300" to a shopping agent has authorised a purchase as plainly as one
# who says "buy". Refusing that is not caution, it is a broken product.
BUY_WORDS = ["buy", "purchase", "order", "checkout", "pay for", "get me",
             "kharid", "book", "i need", "we need", "need ", "want ", "grab",
             "pick up", "add to cart", "le lo", "chahiye", "mangwa", "de do"]
PREMIUM_WORDS = ["premium", "high end", "flagship", "pro ", "top of the line"]
QTY = re.compile(r"\b([1-9][0-9]?)\s*(?:x|pcs?|pieces?|pairs?|units?)\b", re.I)


class IntentCompiler(Protocol):
    def compile(self, utterance: str, user_id: str, now: datetime
                ) -> tuple[IntentEnvelope | None, dict]: ...


class RuleCompiler:
    """Deterministic. Returns (envelope, telemetry). None means ABSTAIN --
    a first-class outcome that lands on the risk-coverage curve, never an
    exception and never a guess.

    Grounding is delegated to `intent.grounding`, which derives its vocabulary
    from the catalog rather than from a list I maintain by hand. That is the
    difference between a demo that answers the eight sentences its author
    thought of and a system whose vocabulary is exactly the set of things a
    merchant actually sells.
    """

    # How similar a product has to be, by meaning alone, before REMIT will
    # even OFFER it. Below this the honest answer is "we do not sell that".
    #
    # MEASURED, not guessed, and the measurement is not flattering.
    #
    # Against 14 things this catalog cannot sell and 10 real meaning-only
    # requests, the deterministic embedder's score distributions OVERLAP:
    # "buy a house" scores 0.186 and "snacks for a party" scores 0.174. A
    # character-n-gram embedder is lexical-semantic; it generalises over
    # spelling and word order and it does not know that a party implies
    # snacks. No threshold separates those two sentences, and pretending one
    # does would be the exact failure this project exists to talk about.
    #
    # So the floor is set for PRECISION at 0.20: nothing in the negative set
    # reaches it, and half the positive set does. "something to drink",
    # "stuff for my desk", "fever medicine", "a gift for a runner" and "things
    # for a baby" work; "snacks for a party" and "something for a headache" do
    # not, and REMIT says it does not stock them rather than guessing.
    #
    # Installing the neural embedder raises the ceiling on that recall, and
    # /health reports which one is actually running. `tests/test_semantic.py`
    # asserts both sides of this boundary against the same two lists, so a
    # catalog change cannot quietly move it.
    SEMANTIC_FLOOR = 0.20

    def __init__(self, lexicon: Lexicon | None = None, semantic=None):
        self._lex = lexicon
        # (utterance, k) -> [(product_id, name, category, score)]. Supplied by
        # the composition root, because the compiler has no business holding a
        # database handle.
        self._semantic = semantic

    @property
    def lexicon(self) -> Lexicon:
        return self._lex if self._lex is not None else default_lexicon()

    def compile(self, utterance: str, user_id: str, now: datetime
                ) -> tuple[IntentEnvelope | None, dict]:
        u = utterance.lower()
        conf = 1.0
        grounding_penalty = 0.0
        notes: list[str] = []

        # ONE SPAN, ONE CONSUMER.
        #
        # The amount extractor owns "under Rs 2,500" and the objective parser
        # owns "fastest delivery". Leaving those spans in the text for the
        # grounder to read as well is double-counting, and it is how "purchase
        # foot cream under 900, fastest delivery option" came to mean "and also
        # a delivery, and also an option, neither of which we stock".
        masked = utterance
        for c in ([top_probe] if (top_probe := best_ceiling(utterance)[0]) else []):
            masked = masked.replace(c.surface, " ")
        for words in OBJECTIVES.values():
            for wd in words:
                masked = re.sub(re.escape(wd), " ", masked, flags=re.I)
        g = ground(masked, self.lexicon)
        items = list(g.items)
        semantic_surfaces: list[str] = []

        # NOTHING MATCHED BY WORD. Ask the vector index whether anything
        # matches by MEANING -- "something to drink", "snacks for a party",
        # "stuff for my desk" contain no word a catalog index can look up.
        #
        # What comes back is a candidate, not a decision: the item is tagged so
        # MATCH-002 fails and a person is asked. An embedding may find a
        # product; it may never authorise one.
        if not items and self._semantic is not None:
            hits = [h for h in self._semantic(content_query(utterance), 4)
                    if h[3] >= self.SEMANTIC_FLOOR]
            if hits:
                pid, name, cat, score = hits[0]
                semantic_surfaces = [utterance.strip()[:60]]
                items = [RequestedItem(
                    terms=[name.lower()], category=cat,
                    surface=utterance.strip()[:60], how="semantic",
                    product_ids=[h[0] for h in hits])]
                notes.append(
                    f"nothing here is called that; by meaning, the closest is "
                    f"{name!r} (similarity {score:.2f})")
                conf -= 0.20
        terms = [t for i in items for t in i.terms]
        terms = list(dict.fromkeys(terms))
        cats = {i.category for i in items if i.category}
        category = cats.pop() if len(cats) == 1 else None

        if not items:
            notes.append("no recognisable category")
            conf -= 0.45
        else:
            fuzzy = [i.surface for i in items if i.how == "fuzzy"]
            if fuzzy:
                # A forgiven typo is still a guess about what someone meant.
                # It is allowed to shop; it is not allowed to be as sure as a
                # word that was spelled correctly. Applied AFTER the amount
                # clamp below, because min() would otherwise swallow it and a
                # misspelling would come out exactly as certain as a correct
                # one -- which is the sort of quiet lie a calibration curve
                # cannot recover from.
                notes.append("read " + ", ".join(
                    f"{i.surface!r} as {i.terms[0]!r}" for i in items
                    if i.how == "fuzzy"))
                grounding_penalty += 0.08 * len(fuzzy)
            if g.ungrounded:
                # The half we cannot fill is not a rounding error. Saying so
                # here is what lets the cart be short of what was asked for
                # without the human discovering it at the doorstep.
                notes.append(
                    "nothing in this catalog answers " +
                    ", ".join(repr(w) for w in g.ungrounded[:3]))
                conf -= 0.15
            approx = [i for i in items if i.approximate]
            if approx:
                notes.append(
                    "no product here IS " + " or ".join(
                        repr(i.surface) for i in approx)
                    + " -- only products with that word in the name")
            if len(items) > 1:
                notes.append(f"{len(items)} separate items requested: " +
                             ", ".join(i.surface for i in items))

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

        conf -= grounding_penalty

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
        #
        # The same reasoning covers a multi-item request. "rice and cooking oil
        # under 500" is five hundred rupees of shopping, not five hundred each.
        per_unit_markers = ("each", "per pair", "per unit", "apiece", "a piece",
                            "per piece", "ek ka")
        multi = len(items) > 1
        ceiling_is_per_unit = (qty == 1 and not multi) or any(
            wd in u for wd in per_unit_markers)
        if ceiling is not None and not ceiling_is_per_unit:
            what = f"{len(items)} items" if multi else f"quantity {qty}"
            notes.append(
                f"{what} with one stated amount: reading Rs {ceiling // 100} "
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
            "grounding": [i.dict() for i in items],
            "ungrounded": g.ungrounded, "unrecognised_words": g.noise,
            "lexicon_size": len(self.lexicon.phrases),
            "notes": notes, "raw_confidence": round(conf, 4),
            "compiler": "rule",
        }
        # Abstain when there is nothing to shop FOR.
        #
        # A stated amount is not a reason to buy something. It is only a limit
        # on what may be spent once there is a thing to buy. "buy a helicopter
        # under 500000" -- no groundable noun, a perfectly clear budget -- used
        # to fall through into a catalog-wide search and come back with a yoga
        # mat. FAILURES.md #13.
        if not items:
            notes.append("nothing in the utterance names something this catalog sells")
            return None, telemetry | {
                "abstained": True, "reason": "no groundable product",
                "unstocked": g.ungrounded}

        env = new_intent(
            user_id=user_id, utterance=utterance, now=now,
            category=category, product_terms=terms,
            requested_items=[i.dict() for i in items],
            approximate_items=[i.surface for i in items if i.approximate],
            semantic_items=semantic_surfaces,
            ungrounded=g.ungrounded,
            merchant_constraints=g.merchants,
            # What the human said they did NOT want. Until this line the field
            # existed, the catalog filter honoured it and the vector index
            # honoured it -- and nothing on the default path ever wrote to it,
            # because `not`, `no` and `without` were stopwords. See
            # grounding._strip_negations and FAILURES #42.
            excluded_attributes=g.excluded,
            max_price_paise=ceiling if ceiling_is_per_unit else None,
            max_total_paise=None if ceiling_is_per_unit else ceiling,
            # Read from the sentence, not assumed. A hardcoded "INR" made
            # CUR-001 a clause that could never fire, and turned "under $5,000"
            # into a 5,000-rupee ceiling. See amounts.detect_currency.
            currency=detect_currency(utterance),
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
            max_total_paise=None if _per_unit else ceiling,
            # The model does not get to name the unit either.
            currency=detect_currency(utterance),
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
