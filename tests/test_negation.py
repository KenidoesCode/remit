"""What the human said they did NOT want.

`excluded_attributes` has been on the envelope since the beginning. The catalog
filter honoured it. The vector index honoured it. The tool schema advertised
it. Nothing on the default path ever wrote to it, because `not`, `no` and
`without` were stop words -- discarded before anything looked at them.

The failure that produced is worse than a missing feature. The word after the
discarded marker still grounded, and joined the CURRENT requested item, whose
terms are a conjunction -- every term required. So:

    "buy shoes but not white"        asked for WHITE shoes
    "buy a laptop but not refurbished"   asked for a REFURBISHED laptop

The constraint was not lost. It was inverted, silently, in the permissive
direction, and the system reported ordinary confidence while doing it.

These tests are the shape of the four sentences section 15 of the hardening
brief names, plus the ways a negation parser goes wrong when nobody bounds it.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from remit.assembly import build
from remit.domain.risk import Exposure
from remit.exec.razorpay import FakeGateway
from remit.intent.grounding import _strip_negations, _tokens

NOW = datetime(2026, 8, 22, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def app():
    return build(now=NOW, gateway=FakeGateway())


def run(app, utterance, user="usr_neg"):
    return app.journey.run(utterance=utterance, user_id=user, now=NOW,
                           exposure=Exposure())


def strip(u):
    kept, excluded = _strip_negations(_tokens(u))
    return " ".join(kept), excluded


# ─────────────────────────────────────────── the four the brief names

@pytest.mark.parametrize("utterance,want", [
    ("buy a laptop but not refurbished", ["refurbished"]),
    ("buy shoes but not white", ["white"]),
    ("buy rice but not basmati", ["basmati"]),
    ("buy a phone but not second hand", ["second", "hand"]),
])
def test_the_constraint_is_parsed(utterance, want):
    """All four of the brief's sentences, at the parser. This layer does not
    care whether the shop stocks the thing -- "phone" grounds to nothing in
    this catalog and the journey correctly abstains, but the constraint in the
    sentence was still a constraint and had to be read as one."""
    _, excluded = strip(utterance)
    assert excluded == want, excluded


@pytest.mark.parametrize("utterance,want", [
    ("buy a laptop but not refurbished", ["refurbished"]),
    ("buy shoes but not white", ["white"]),
    ("buy rice but not basmati", ["basmati"]),
])
def test_the_constraint_reaches_the_envelope(app, utterance, want):
    """And for the ones this catalog can answer, it survives compilation --
    which is the part that was missing, not the field."""
    r = run(app, utterance)
    assert r.intent is not None, r.note
    assert r.intent.excluded_attributes == want, r.intent.excluded_attributes


@pytest.mark.parametrize("utterance,forbidden", [
    ("buy a laptop but not refurbished", "refurbished"),
    ("buy shoes but not white", "white"),
    ("buy rice under 2000 but not basmati", "basmati"),
])
def test_the_excluded_thing_is_never_the_thing_it_buys(app, utterance, forbidden):
    """The inversion, stated as an invariant. Whatever else happens -- bought,
    stepped up, refused -- the product on the screen may not be the one the
    sentence ruled out."""
    r = run(app, utterance)
    if r.selected is not None:
        assert forbidden not in r.selected.name.lower(), r.selected.name
    for line in (r.cart.lines if r.cart else []):
        assert forbidden not in line.name.lower(), line.name


# ──────────────────────────────────────────────── the ways this goes wrong

def test_a_negation_does_not_swallow_the_rest_of_the_sentence(app):
    """"not white and buy socks" is a constraint and then a request."""
    kept, excluded = strip("buy shoes not white and buy socks")
    assert excluded == ["white"], excluded
    assert "socks" in kept and "shoes" in kept


def test_two_negations_are_two_spans(app):
    _, excluded = strip("buy shoes not white and not black")
    assert excluded == ["white", "black"], excluded


def test_a_marker_with_nothing_after_it_excludes_nothing(app):
    """"buy shoes, not" is somebody who stopped typing."""
    kept, excluded = strip("buy shoes, not")
    assert excluded == []
    assert "shoes" in kept


def test_stop_words_inside_the_span_are_not_constraints(app):
    _, excluded = strip("buy a laptop but not the refurbished one")
    assert excluded == ["refurbished"], excluded


def test_ordinary_sentences_are_untouched(app):
    """The markers were stop words for a reason -- they had to stop being
    stop words without changing anything that did not contain them."""
    for u in ("buy running shoes under 5000",
              "order 3 kg rice and cooking oil under 2000",
              "buy earbuds under 3000, best rated"):
        kept, excluded = strip(u)
        assert excluded == [], (u, excluded)


def test_the_negation_words_left_the_stop_list(app):
    from remit.intent.grounding import NEGATE, STOP
    for w in ("not", "no", "without"):
        assert w not in STOP, f"{w} is still discarded before anything sees it"
        assert w in NEGATE


# ────────────────────────────────────────────────── saying so out loud

def test_excluding_the_only_stock_says_which_and_why(app):
    """A shop whose only rice is basmati, asked for rice but not basmati, does
    not have "no rice". Telling the human it does is the same class of lie as
    FAILURES #19 -- true-sounding, wrong, and it sends them away."""
    r = run(app, "buy rice under 2000 but not basmati")
    assert r.selected is None
    assert "excluded" in (r.note or ""), r.note
    assert "basmati" in (r.note or "").lower(), r.note
    assert "Rice" in (r.note or ""), r.note
    assert r.telemetry.get("excluded_out"), r.telemetry


def test_it_does_not_quietly_substitute(app):
    """The whole point. Excluding everything that answers the request produces
    a refusal, not the nearest other thing."""
    r = run(app, "buy rice under 2000 but not basmati")
    assert r.cart is None or not r.cart.lines
    assert r.payment_state in ("NONE", "BLOCKED"), r.payment_state


def test_an_exclusion_matches_the_label_not_only_the_tags(app):
    """The first version of this fix compared exclusions against `attributes`
    alone. "basmati" is on the label, not in a tag list, so the filter passed
    the excluded product straight through and then asked the human to confirm
    the one thing they had ruled out."""
    from remit.domain.catalog import Catalog
    cat = Catalog(app.db)
    with_it = cat.search(terms=["rice"], excluded=[])
    without = cat.search(terms=["rice"], excluded=["basmati"])
    assert with_it, "no rice in the catalog; this test proves nothing"
    assert len(without) < len(with_it)
    assert all("basmati" not in p.name.lower() for p in without)


def test_an_exclusion_respects_word_boundaries(app):
    """"white" must not strike "whitening", and "non" must not strike
    "nonstick". A substring match here silently removes stock."""
    from remit.domain.catalog import Catalog
    cat = Catalog(app.db)
    every = cat.search(limit=500)
    assert every
    for word, longer in (("white", "whitening"), ("non", "nonstick"),
                         ("air", "airtight")):
        kept = cat.search(limit=500, excluded=[word])
        for p in kept:
            assert word not in p.name.lower().split(), p.name
        struck = [p.name for p in every if p not in kept]
        for name in struck:
            assert word in name.lower().split() or word in [
                a.lower() for a in next(
                    q for q in every if q.name == name).attributes], name
