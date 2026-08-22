"""Retrieval by meaning, and the two things it is not allowed to do.

An embedding may FIND a product. It may never AUTHORISE one, and it may never
resurrect a request this catalog cannot answer.

The two lists below are the measurement that set `SEMANTIC_FLOOR`. They are in
the test rather than in a comment because the floor is a property of the
catalog: add a product called "Housewarming Kit" and "buy a house" starts
scoring differently, and this should fail loudly when it does.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from remit.assembly import build
from remit.intent.grounding import content_query
from remit.intent.shopping import RuleCompiler
from remit.retrieval.embed import HashingEmbedder, cosine, hash_str
from remit.retrieval.index import VectorIndex, hard_filter

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)

# Things this shop cannot sell. None of these may clear the floor.
CANNOT_SELL = [
    "buy a helicopter", "buy a ferrari", "hello", "what's the weather",
    "thanks!", "asdkjhasd", "buy a kalashnikov", "buy bitcoin", "buy a house",
    "buy a car", "buy an aeroplane", "random words here",
    "buy the cheapest thing you have", "buy something nice for my mom",
]

# Real requests with no word a catalog index can look up. The honest claim is
# that SOME of these clear the floor -- not all of them, and the ones that do
# not are refused rather than guessed at.
MEANING_ONLY_THAT_WORK = [
    "something to drink", "stuff for my desk", "fever medicine",
    "a gift for a runner", "things for a baby",
]


@pytest.fixture(scope="module")
def app():
    return build(db_path=":memory:", now=NOW, live=False)


def top(app, q):
    """Score the query the way the compiler scores it -- content words only.

    Retrieval on the raw sentence is dominated by the words every sentence
    contains: "buy a helicopter under 500000" scored 0.21 against a cable tray
    once the amount was masked out, purely because the leftover query was short.
    Strip the grammar of buying and the same query scores 0.15."""
    c = content_query(q)
    hits = app.index.search(c, app.db, k=3) if c else []
    return hits[0].score if hits else 0.0


# ------------------------------------------------------------ the boundary

@pytest.mark.parametrize("q", CANNOT_SELL)
def test_meaning_alone_never_buys_what_we_do_not_sell(app, q):
    """The behavioural invariant, which is the one that matters.

    FAILURES #13 was 'buy a helicopter' returning a yoga mat, and adding vector
    retrieval is the single most likely way to bring that back. Note what is
    asserted: not that retrieval finds nothing, but that nothing is BOUGHT
    without a person. Those are different promises and only the second one is
    keepable."""
    r = app.journey.run(utterance=q, user_id=f"usr_ns{hash(q) % 997}", now=NOW)
    if r.cart is None:
        return
    assert r.authorization.verdict.value != "AUTO", (
        f"{q!r} auto-executed {[l.name for l in r.cart.lines]}")
    assert "MATCH-002" in r.authorization.failed


@pytest.mark.parametrize("q", [q for q in CANNOT_SELL if "house" not in q])
def test_nonsense_does_not_even_reach_the_floor(app, q):
    assert top(app, q) < RuleCompiler.SEMANTIC_FLOOR, (
        f"{q!r} scored {top(app, q):.3f} against a floor of "
        f"{RuleCompiler.SEMANTIC_FLOOR}")


def test_the_known_collision_is_a_known_collision(app):
    """"buy a house" retrieves a Microfibre Mop at 0.24, above the floor.

    This is not a bug in the threshold, it is what a LEXICAL-semantic embedder
    is: "house" and "household" share four character n-grams, and this shop
    does sell household goods. The right response is not to tune the number
    until this one example goes away -- it is to make sure the mop is OFFERED
    and never BOUGHT, which MATCH-002 does, and to write the example down.

    A dense neural embedder would separate these. This instance says which one
    it is running, on /health, rather than implying the better answer."""
    assert top(app, "buy a house") >= RuleCompiler.SEMANTIC_FLOOR
    r = app.journey.run(utterance="buy a house under 5000000",
                        user_id="usr_house", now=NOW)
    assert r.authorization.verdict.value != "AUTO"
    assert "MATCH-002" in r.authorization.failed


@pytest.mark.parametrize("q", MEANING_ONLY_THAT_WORK)
def test_a_real_request_with_no_matching_word_still_reaches_a_product(app, q):
    assert top(app, q) >= RuleCompiler.SEMANTIC_FLOOR, (
        f"{q!r} scored {top(app, q):.3f}")


def test_the_helicopter_still_abstains_end_to_end(app):
    r = app.journey.run(utterance="buy a helicopter under 500000",
                        user_id="usr_sem1", now=NOW)
    assert r.cart is None, [l.name for l in r.cart.lines]
    assert r.telemetry.get("abstained") is True


# ------------------------------------------------------- find, never authorise

def test_a_semantic_match_is_never_bought_without_a_person(app):
    # "a house" has no word this catalog indexes; "drink" does, so it takes the
    # lexical path and is not a test of this at all.
    r = app.journey.run(utterance="buy a house under 500000",
                        user_id="usr_sem2", now=NOW)
    assert r.cart is not None, r.note
    assert r.authorization.verdict.value != "AUTO"
    assert "MATCH-002" in r.authorization.failed, r.authorization.failed
    assert r.intent.semantic_items, "the envelope did not record how it was found"


def test_a_word_match_does_not_trip_the_semantic_clause(app):
    r = app.journey.run(utterance="buy chips under 200", user_id="usr_sem3", now=NOW)
    assert "MATCH-002" not in r.authorization.failed


# ------------------------------------------------------------- the hard filter

def test_retrieval_cannot_reintroduce_a_product_the_budget_excluded(app):
    """The Rs 139 rule. Similarity is not a currency."""
    hits = [h.product for h in app.index.search("something to drink", app.db, k=20)]
    assert hits
    kept = hard_filter(hits, max_price_paise=2000, required=None,
                       excluded=None, merchants=None)
    assert all(p.price_paise <= 2000 for p in kept)


def test_a_semantic_journey_still_respects_the_ceiling(app):
    r = app.journey.run(utterance="buy a house under 20",
                        user_id="usr_sem4", now=NOW)
    if r.cart is not None:
        assert r.totals.total_paise <= 2000 or \
            r.authorization.verdict.value != "AUTO"


# --------------------------------------------------------------- determinism

def test_the_same_query_embeds_the_same_way_in_a_new_process():
    """Python's hash() is salted per process. Using it here would mean every
    restart produced different vectors and every replayed decision retrieved a
    different shelf."""
    assert hash_str("cooking oil") == hash_str("cooking oil")
    assert hash_str("cooking oil") == 3787067091924354795  # pinned on purpose


def test_vectors_are_normalised(app):
    e = HashingEmbedder()
    v = e.embed("Freshcart Basmati Rice 5kg groceries")
    assert abs(cosine(v, v) - 1.0) < 1e-9


def test_the_api_says_which_embedder_actually_ran(app):
    assert app.embedder.kind in ("lexical-semantic", "dense-neural")
    assert app.embedder.name
