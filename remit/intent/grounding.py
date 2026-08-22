"""Ground an utterance in the catalog that actually exists.

The old grounder was a hand-written dictionary of category words. It had two
properties that do not survive contact with real users:

  1. It could only ever know the nouns I had personally thought of. Adding
     eighty-five products to the catalog did not teach it a single new word.
     Every gap was silent -- the utterance simply failed to ground and the
     agent widened to a category, which is how "buy a helicopter" bought a
     yoga mat (FAILURES #13).
  2. It stopped at the FIRST category it matched and kept ONE noun. So
     "order 3 kg rice and cooking oil" grounded as `cooking oil` and the rice
     was silently discarded -- not refused, not flagged, discarded. The human
     asked for two things, authorised two things, and was charged for one of
     them plus whatever the revenue engine attached.

This module replaces both. The lexicon is DERIVED FROM THE CATALOG, so the
vocabulary is exactly the set of things that can actually be bought, and it
grows when the catalog grows. And it returns EVERY item the utterance names,
in the order the human said them.

Three properties worth stating, because the rest of the system leans on them:

  * Deterministic. Same utterance + same catalog version -> same grounding,
    forever. No model call, no clock, no randomness. Replay works.
  * Auditable. Every match records how it matched -- exact phrase, synonym,
    or bounded fuzzy -- and that provenance travels into telemetry. A judge
    can ask "why did it think I said rice" and get an answer.
  * Conservative at the edges. Fuzzy matching is deliberately crippled: it
    only fires on single tokens of five characters or more, only when the
    first two characters already agree, and only above a high similarity
    floor. A typo should be forgiven. A different word should not.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from difflib import SequenceMatcher

# Words that are grammar, not goods. Kept small on purpose: this list only has
# to stop noise from reaching the lexicon, and the lexicon is the real filter.
STOP = {
    "a", "an", "the", "and", "or", "of", "for", "to", "me", "my", "i", "we",
    "please", "pls", "plz", "some", "any", "one", "two", "buy", "purchase",
    "order", "get", "want", "need", "give", "add", "cart", "checkout", "pay",
    "book", "under", "below", "less", "than", "max", "maximum", "budget",
    "around", "about", "upto", "up", "rs", "rupees", "inr", "with", "without",
    "best", "good", "cheap", "cheapest", "top", "rated", "value", "worth",
    "fast", "fastest", "quick", "urgent", "today", "asap", "premium", "high",
    "end", "new", "nice", "also", "then", "plus", "from", "in", "on", "at",
    "it", "is", "are", "that", "this", "not", "no", "do", "can", "you",
    "karo", "kar", "de", "do", "mujhe", "chahiye", "jaldi", "sasta", "acha",
}

# Measurement and packaging noise inside product names. Stripped before the
# name is turned into searchable phrases, so "Freshcart Basmati Rice 5kg"
# contributes "basmati rice", not "rice 5kg".
UNIT = re.compile(
    r"^\(?\d+(\.\d+)?\s*(kg|kgs|g|gm|gms|mg|ml|l|ltr|litre|liters?|"
    r"pcs?|pack|packs|x|cm|mm|inch|in|sheets?|tablets?|units?|count)?\)?$",
    re.I)

# Colloquial and Hinglish surface forms that no product name will ever carry.
# This is the ONE hand-written list left, and it is deliberately tiny: it maps
# a word a person might say onto a word the catalog already contains. It never
# invents a product, only a spelling.
SYNONYM = {
    # alcohol
    "daru": "alcohol", "booze": "alcohol", "liquor": "alcohol",
    "sharab": "alcohol", "scotch": "whisky", "rum": "dark rum",
    # beverages
    "chai": "tea", "cold drink": "cola", "soft drink": "cola",
    "thanda": "cola", "soda": "lemon soda",
    # electronics -- the catalog says "Buds", people say everything else
    "earbuds": "buds", "earphone": "buds", "earphones": "buds",
    "headphones": "buds", "headphone": "buds", "headset": "buds",
    "powerbank": "power bank", "charger cable": "cable",
    # pharmacy
    "painkiller": "paracetamol", "fever tablet": "paracetamol",
    "dawai": "medicine", "chemist": "medicine", "pharmacy": "medicine",
    "bandaid": "bandages", "band aid": "bandages",
    # personal care / household
    "contraceptive": "condoms", "contraceptives": "condoms",
    "sanitizer": "handwash", "hand sanitizer": "handwash",
    "washing powder": "detergent", "kitchen roll": "tissue",
    "toilet roll": "toilet paper", "chappal": "slide",
    # groceries
    "maggi": "noodles", "kurkure": "chips", "wafers": "chips",
    "namkeen": "peanuts", "nappy": "diapers", "nappies": "diapers",
}

# Words that are packaging, currency or quantity talk. They are not products
# and they are not noise worth reporting back to the human as "we do not stock
# a bottle", so they are stopped rather than left to reach `ungrounded`.
STOP |= {
    "bottle", "bottles", "packet", "packets", "box", "boxes", "piece", "pieces",
    "pack", "packs", "kg", "kgs", "gram", "grams", "litre", "litres", "liter",
    "ml", "pcs", "unit", "units", "hazaar", "hazar", "rupaye", "rupaiya", "sau",
    "hundred", "thousand", "lakh", "k", "le", "lo", "aur", "ka", "ki", "ke",
    "bhi", "ek", "do", "teen", "char", "paanch", "each", "per", "apiece",
    # Words about the TRANSACTION rather than the goods. Nobody sells a
    # "delivery" or an "option", and letting those reach the grounder made
    # "fastest delivery option" read as a second thing the human wanted and
    # could not have. One span, one consumer: the objective parser owns these.
    "delivery", "shipping", "option", "options", "price", "prices", "rated",
    "rating", "ratings", "review", "reviews", "service", "quality", "deal",
    "deals", "offer", "offers", "discount", "brand", "seller", "warranty",
    "return", "returns", "exchange", "emi", "coupon", "cashback",
}

# Words that describe the wrapper, not the goods. Stripped off the end of a
# product name before deciding what its head noun is, so "Daily Mart Condoms
# (10 pack)" is a condom and not a pack.
PACKAGING = {"pack", "packs", "pcs", "piece", "pieces", "box", "bags", "bag",
             "pulls", "rolls", "roll", "sachets", "sachet", "strip", "strips",
             "refill", "set", "kit", "combo", "pair", "pairs", "count"}

WORD = re.compile(r"[a-z]+", re.I)


@dataclass(frozen=True)
class Grounded:
    """One thing the human named, and the evidence that it was named."""
    term: str                  # the catalog-side phrase this grounds to
    surface: str               # the words the human actually used
    category: str | None       # the shelf it leads to; None if the phrase
                               # is used on more than one shelf
    how: str                   # 'exact' | 'synonym' | 'fuzzy'
    start: int                 # token index: ordering is the human's ordering

    def dict(self) -> dict:
        return {"term": self.term, "surface": self.surface,
                "category": self.category, "how": self.how}


@dataclass(frozen=True)
class RequestedItem:
    """One thing the human asked for, and every word they used to describe it.

    `terms` is a conjunction, not a list of alternatives: "waterproof trail
    shoes" is ONE item with three terms, all of which a product must answer.
    Whether that conjunction is real is not decided here -- the caller tries it
    against the catalog and splits it if nothing satisfies all of it. Grammar
    proposes; the catalog disposes.
    """
    terms: list[str]
    category: str | None
    surface: str
    how: str
    approximate: bool = False
    product_ids: list[str] | None = None   # set when the item came from vector
                                           # retrieval: the products ARE the
                                           # request, because no word matched   # every word matched only as a MODIFIER inside
                                # some product's name -- "laptop" reaching
                                # "Laptop Stand". Buyable, but not the thing
                                # that was named, so a person decides.

    def dict(self) -> dict:
        d = {"terms": self.terms, "category": self.category,
             "surface": self.surface, "how": self.how,
             "approximate": self.approximate}
        if self.product_ids:
            d["product_ids"] = list(self.product_ids)
        return d


@dataclass(frozen=True)
class Grounding:
    """Everything one utterance turned out to be asking for."""
    items: list[RequestedItem]     # the things to buy, in the order said
    merchants: list[str]           # merchant ids the human named
    ungrounded: list[str]          # words that stood alone as a request this
                                   # catalog cannot answer -- "and a ferrari"
    noise: list[str]               # unknown words sitting next to something we
                                   # DID understand. Reported, never counted.

    @property
    def all_terms(self) -> list[str]:
        out: list[str] = []
        for it in self.items:
            for t in it.terms:
                if t not in out:
                    out.append(t)
        return out


class Lexicon:
    """Everything this catalog can be asked for, and where each phrase leads.

    Built once per catalog version. A phrase maps to the category that uses it
    most -- a phrase that appears across categories (say "cotton") is a weak
    signal and is dropped rather than arbitrated, because a grounder that
    guesses is worse than one that abstains.
    """

    def __init__(self, phrases: dict[str, tuple[str | None, str]],
                 merchants: dict[str, str], version: int):
        # phrase -> (category or None if the phrase spans shelves, kind)
        # kind is 'product' (a thing you can put in a cart) or 'attribute'
        # (a property of a thing). Conflating the two is how "waterproof trail
        # shoes" becomes three separate purchases instead of one qualified one.
        self.phrases = phrases
        self.merchants = merchants      # merchant word -> merchant_id
        self.version = version
        self._single = [p for p in phrases if " " not in p and len(p) >= 5]
        # A typo of a word the catalog does not use ("hedphones") still has to
        # reach the shelf it means. Synonym keys are spellable too, so they get
        # the same forgiveness, resolving through the map afterwards.
        self._syn = [k for k, v in SYNONYM.items()
                     if " " not in k and len(k) >= 5 and v in phrases]

    # ---------- construction ----------
    @classmethod
    def from_db(cls, db: sqlite3.Connection, version: int) -> "Lexicon":
        brand: set[str] = set()
        merchants: dict[str, str] = {}
        for r in db.execute("SELECT merchant_id, name FROM merchants"):
            for w in WORD.findall(r["name"].lower()):
                if len(w) > 2 and w not in ("the", "co", "supply", "goods"):
                    brand.add(w)
                    merchants.setdefault(w, r["merchant_id"])

        counts: dict[str, dict[str, int]] = {}
        kinds: dict[str, set[str]] = {}

        def note(phrase: str, category: str, kind: str = "head") -> None:
            phrase = phrase.strip()
            if len(phrase) < 3 or phrase in STOP:
                return
            counts.setdefault(phrase, {})
            counts[phrase][category] = counts[phrase].get(category, 0) + 1
            kinds.setdefault(phrase, set()).add(kind)

        import json as _json
        for r in db.execute(
                "SELECT name, category, subcategory, attributes FROM products"
                " WHERE active=1"):
            cat = r["category"]
            # The category name itself, and its words. "running shoes" and
            # "shoes" both have to reach the running-shoes shelf.
            note(cat, cat)
            for w in cat.split():
                note(w, cat)
            if r["subcategory"]:
                note(str(r["subcategory"]).lower(), cat)
            # Attributes are how someone asks for the property rather than the
            # product: "waterproof", "carbon-plate", "wide-fit".
            for a in _json.loads(r["attributes"]):
                a = a.lower().replace("-", " ")
                if not a.startswith("restricted"):
                    # A whole attribute names a kind of thing ("backpack",
                    # "waterproof"); a fragment of one does not.
                    note(a, cat, "head")
            # The product name, minus brand and minus packaging.
            toks = [t for t in WORD.findall(r["name"].lower())
                    if t not in brand and t not in STOP]
            toks = [t for t in toks if not UNIT.match(t)]
            while toks and toks[-1] in PACKAGING:
                toks.pop()
            # English puts the head noun last. The final token, and the final
            # pair, NAME the product; everything before them only describes it.
            # "Deskhaus Laptop Stand" is a stand, not a laptop -- and an agent
            # that treats those as the same word buys you a Rs 4,446 stand when
            # you asked for a laptop, and does it on AUTO. FAILURES #24.
            heads = set()
            if toks:
                heads.add(toks[-1])
                if len(toks) > 1:
                    heads.add(" ".join(toks[-2:]))
            for n in (2, 1):
                for i in range(len(toks) - n + 1):
                    phrase = " ".join(toks[i:i + n])
                    note(phrase, cat, "head" if phrase in heads else "modifier")

        phrases: dict[str, tuple[str | None, str]] = {}
        for phrase, by_cat in counts.items():
            ordered = sorted(by_cat.items(), key=lambda kv: (-kv[1], kv[0]))
            # A phrase used on two shelves in equal measure ("gel" is a pen and
            # a face wash) does not identify a shelf. It still identifies a
            # PRODUCT, though, so it is kept with no category: the search then
            # runs on the term alone across the whole catalog, which is both
            # correct and how a human would read it. Dropping the phrase, which
            # is what this used to do, silently lost the word instead.
            tie = len(ordered) > 1 and ordered[0][1] == ordered[1][1]
            cat = None if tie else ordered[0][0]
            # Head anywhere wins: "oil" is a modifier in "Oil Filter" and a head
            # in "Cooking Oil", and the second is enough to make it a thing you
            # can ask for.
            kind = "head" if "head" in kinds[phrase] else "modifier"
            phrases[phrase] = (cat, kind)
        return cls(phrases, merchants, version)

    # ---------- lookup ----------
    def exact(self, phrase: str) -> tuple[str | None, str] | None:
        return self.phrases.get(phrase)

    def fuzzy(self, token: str) -> tuple[str, str] | None:
        """Forgive a typo. Do not accept a different word.

        Three gates, all of them deliberate: five characters or more (so "rce"
        cannot become "rice" and "car" cannot become "cat food"), the first two
        characters must already agree (typos are usually interior), and a
        similarity floor of 0.84. Below that the honest answer is "I do not
        stock that", which the caller knows how to say.
        """
        if len(token) < 5:
            return None
        best, best_r = None, 0.0
        for cand in self._single + self._syn:
            if cand[:2] != token[:2] or abs(len(cand) - len(token)) > 2:
                continue
            r = SequenceMatcher(None, token, cand).ratio()
            if r > best_r:
                best, best_r = cand, r
        if best is None or best_r < 0.84:
            return None
        term = SYNONYM.get(best, best) if best not in self.phrases else best
        return term, self.phrases[term][0]


SPLIT = {"and", "plus", "also", "aur", "then", "with", "&", "+", ",", ";"}

TOKEN = re.compile(r"[a-z]+|[,;&+]", re.I)


def _tokens(utterance: str) -> list[str]:
    """Words, plus the punctuation that separates one request from the next.

    Commas matter. "notebook, gel pen and highlighter" is three things, and the
    only evidence for that is a comma -- throw it away with the rest of the
    punctuation and the three collapse into one unbuyable conjunction."""
    return [t for t in TOKEN.findall(utterance.lower())]


def content_query(utterance: str) -> str:
    """What the sentence is ABOUT, with the grammar of buying removed.

    Vector retrieval on the raw sentence is dominated by the words every
    sentence contains. "buy a helicopter under 500000" retrieved a cable tray
    at 0.21 -- not because a tray is like a helicopter, but because after the
    amount was masked out the query was short and the leftover function words
    carried most of the weight. Strip them and the same query scores 0.15.

    Short queries are where cosine similarity is least trustworthy, so this is
    not cosmetic: it is the difference between the nonsense clearing the floor
    and not.
    """
    low = utterance.lower()
    from .amounts import CEILING_WORDS
    for wd in sorted(CEILING_WORDS, key=len, reverse=True):
        low = low.replace(wd, " ")
    toks = [t for t in TOKEN.findall(low)
            if t.isalpha() and len(t) > 2 and t not in STOP and t not in SPLIT]
    return " ".join(toks)


def ground(utterance: str, lex: Lexicon) -> Grounding:
    """Everything the utterance names, grouped the way the human grouped it.

    Two rules, both cheap and both explainable to a judge in one sentence:

      * Longest match wins, left to right, no overlaps. That is what makes
        "cooking oil" beat "oil" and "dog food" beat "food" with no priority
        table to maintain and no ordering bug to find later.
      * A conjunction or a comma starts a new item; anything else continues the
        current one. "waterproof trail shoes" is one thing with three words on
        it. "rice and cooking oil" is two things.

    A word that looks like a noun and grounds to nothing is not dropped -- it
    comes back in `ungrounded`, so the caller can say "we do not stock a
    helicopter" instead of quietly selling a yoga mat. FAILURES #13.
    """
    toks = _tokens(utterance)
    groups: list[list[Grounded]] = [[]]
    unknown: list[list[str]] = [[]]
    merchants: list[str] = []
    i = 0
    while i < len(toks):
        tok = toks[i]
        if tok in SPLIT:
            if groups[-1] or unknown[-1]:
                groups.append([])
                unknown.append([])
            i += 1
            continue
        hit: Grounded | None = None
        step = 1
        for n in (3, 2, 1):
            if i + n > len(toks):
                continue
            part = toks[i:i + n]
            if any(p in SPLIT for p in part):
                continue
            span = " ".join(part)
            if n == 1 and span in STOP:
                continue
            found, how, term = lex.exact(span), "exact", span
            if found is None:
                syn = SYNONYM.get(span)
                if not syn:
                    continue
                found, how, term = lex.exact(syn), "synonym", syn
                if found is None:
                    continue
            hit = Grounded(term, span, found[0], how, i)
            step = n
            break
        if hit is None:
            if tok in lex.merchants:
                merchants.append(lex.merchants[tok])
            elif tok not in STOP and len(tok) >= 4:
                f = lex.fuzzy(tok)
                if f:
                    hit = Grounded(f[0], tok, f[1], "fuzzy", i)
                else:
                    unknown[-1].append(tok)
        if hit is not None and not any(hit.term == g.term for g in groups[-1]):
            groups[-1].append(hit)
        i += step

    # An unknown word only means "we cannot buy you that" if it stood on its
    # own as a request. Sitting beside something we did understand, it is
    # almost always filler -- "yaar ek yoga mat order kar do" has four words
    # this catalog will never contain and exactly one thing to buy. Counting
    # those against fulfilment turned 83 correct purchases into interruptions
    # in one afternoon. FAILURES #18.
    ungrounded = [w for grp, unk in zip(groups, unknown) if not grp for w in unk]
    noise = [w for grp, unk in zip(groups, unknown) if grp for w in unk]

    items: list[RequestedItem] = []
    seen: set[tuple[str, ...]] = set()
    for g in groups:
        if not g:
            continue
        key = tuple(sorted(x.term for x in g))
        if key in seen:
            continue
        seen.add(key)
        cats = {x.category for x in g if x.category}
        items.append(RequestedItem(
            terms=[x.term for x in g],
            category=cats.pop() if len(cats) == 1 else None,
            surface=" ".join(x.surface for x in g),
            how=("fuzzy" if any(x.how == "fuzzy" for x in g)
                 else "synonym" if any(x.how == "synonym" for x in g)
                 else "exact"),
            approximate=all(lex.phrases.get(x.term, (None, "head"))[1]
                            == "modifier" for x in g)))
    return Grounding(items=items, merchants=sorted(set(merchants)),
                     ungrounded=ungrounded, noise=noise)
