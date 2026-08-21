"""Amount extraction from code-mixed Indian English.

This is small, ugly, and the most important 150 lines in the repo.

Published Indic ASR/NLU work reports numeric error rates on READ speech
(SCRIBE reports ~0.31% ER_num on Hindi FLEURS), while the flagship noisy
conversational Indic benchmark does not isolate numerals at all. Vendors
document real order-of-magnitude failures -- "teen hazaar rupay" (Rs 3,000)
transcribed as "teen rupay" (Rs 3), a 1,000x error. Sarvam publishes a
failure class for exactly this: three numeric forms ('paanch sau', '500',
'\u096b\u0966\u0966') treated as unrelated tokens.

So we parse amounts deterministically, return every candidate we found with a
confidence, and let the caller decide. We NEVER let a model do the
arithmetic, and we surface the magnitude of disagreement rather than
silently picking one.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from ..money import Paise

WORD_UNITS = {
    "hazaar": 1_000, "hazar": 1_000, "hazzar": 1_000, "thousand": 1_000, "k": 1_000,
    "sau": 100, "hundred": 100,
    "lakh": 100_000, "lac": 100_000, "lakhs": 100_000,
    "crore": 10_000_000, "cr": 10_000_000,
    "million": 1_000_000,
}
WORD_NUMS = {
    "ek": 1, "one": 1, "do": 2, "two": 2, "teen": 3, "theen": 3, "three": 3,
    "char": 4, "chaar": 4, "four": 4, "paanch": 5, "panch": 5, "five": 5,
    "chhe": 6, "che": 6, "six": 6, "saat": 7, "seven": 7, "aath": 8, "eight": 8,
    "nau": 9, "nine": 9, "das": 10, "dus": 10, "ten": 10,
    "bees": 20, "twenty": 20, "pachaas": 50, "pachas": 50, "fifty": 50,
}


@dataclass
class AmountCandidate:
    paise: Paise
    surface: str
    confidence: float
    form: str          # 'digits' | 'digits+unit' | 'words' | 'devanagari'

    def rupees(self) -> int:
        return self.paise // 100


def _fold_devanagari(text: str) -> str:
    out = []
    for ch in text:
        if unicodedata.category(ch) == "Nd" and not ch.isascii():
            out.append(str(unicodedata.digit(ch)))
        else:
            out.append(ch)
    return "".join(out)


_DIGIT_UNIT = re.compile(
    r"(?:\u20b9|rs\.?|inr)?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*"
    r"(k|hazaar|hazar|thousand|lakh|lac|lakhs|crore|cr|sau|hundred)?\b",
    re.I)


def extract(text: str) -> list[AmountCandidate]:
    """Every plausible amount, most confident first. Never one guess."""
    t = _fold_devanagari(text.lower())
    had_devanagari = t != text.lower()
    out: list[AmountCandidate] = []

    for m in _DIGIT_UNIT.finditer(t):
        raw, unit = m.group(1), (m.group(2) or "").lower()
        try:
            val = float(raw.replace(",", ""))
        except ValueError:
            continue
        if val <= 0:
            continue
        mult = WORD_UNITS.get(unit, 1)
        rupees = val * mult
        # A bare small integer next to no currency marker is usually a
        # quantity, not a price. Lower confidence rather than discard.
        has_marker = bool(re.search(r"(\u20b9|rs\.?|inr)\s*" + re.escape(raw), t))
        if unit:
            conf, form = 0.93, "digits+unit"
        elif has_marker:
            conf, form = 0.96, "digits"
        elif rupees >= 100:
            conf, form = 0.80, "digits"
        else:
            conf, form = 0.30, "digits"
        if had_devanagari:
            form = "devanagari"
            conf = min(conf, 0.85)
        out.append(AmountCandidate(int(round(rupees * 100)), m.group(0).strip(),
                                   conf, form))

    # word forms: "das hazaar", "paanch sau"
    toks = re.findall(r"[a-z]+", t)
    for i, tok in enumerate(toks):
        if tok in WORD_NUMS:
            n = WORD_NUMS[tok]
            if i + 1 < len(toks) and toks[i + 1] in WORD_UNITS:
                rupees = n * WORD_UNITS[toks[i + 1]]
                out.append(AmountCandidate(
                    int(rupees * 100), f"{tok} {toks[i+1]}", 0.72, "words"))
            elif i + 1 < len(toks) and toks[i + 1] in ("rupay", "rupaye", "rupees", "rs"):
                # "teen rupay" -- the 1000x failure mode. Low confidence on
                # purpose: a bare small number next to 'rupay' in a shopping
                # utterance is far more often a truncated 'teen hazaar rupay'.
                out.append(AmountCandidate(int(n * 100), f"{tok} {toks[i+1]}",
                                           0.35, "words"))

    # dedupe by value, keep the most confident surface
    best: dict[int, AmountCandidate] = {}
    for c in out:
        if c.paise not in best or c.confidence > best[c.paise].confidence:
            best[c.paise] = c
    res = sorted(best.values(), key=lambda c: (-c.confidence, -c.paise))
    return res


def best_ceiling(text: str) -> tuple[AmountCandidate | None, list[AmountCandidate]]:
    """Pick the amount that reads as a spending ceiling, and return the
    alternatives that were rejected. The rejected list is not decoration:
    when a transaction is disputed, it is the evidence that the ambiguity
    was seen and adjudicated rather than missed."""
    cands = [c for c in extract(text) if c.paise >= 5000]   # >= Rs 50
    if not cands:
        return None, []
    ceiling_words = ("under", "below", "less than", "upto", "up to", "within",
                     "max", "budget", "se kam", "tak")
    low = text.lower()
    if any(wd in low for wd in ceiling_words):
        top = max(cands, key=lambda c: (c.confidence, c.paise))
    else:
        top = cands[0]
    return top, [c for c in cands if c is not top]
