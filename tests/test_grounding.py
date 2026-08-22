"""The grounder has one job: turn what a person said into what a merchant
sells, without inventing anything.

These tests are written against BEHAVIOUR a reviewer can check by reading the
assertion, not against internals. Every one of them is a sentence somebody
actually typed at the deployed site.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from remit.assembly import build
from remit.intent.grounding import Lexicon, SYNONYM, ground
from remit.intent.shopping import RuleCompiler

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def app():
    return build(db_path=":memory:", now=NOW, live=False)


@pytest.fixture(scope="module")
def lex(app):
    return Lexicon.from_db(app.db, 1)


def names(result):
    return [l.name for l in result.cart.lines] if result.cart else []


# ---------------------------------------------------------------- vocabulary

def test_the_vocabulary_comes_from_the_catalog(lex, app):
    """Not from a list I maintain by hand. Every category the merchant stocks
    must be a word the grounder knows, automatically."""
    cats = {r["category"] for r in app.db.execute(
        "SELECT DISTINCT category FROM products WHERE active=1")}
    missing = [c for c in cats if c not in lex.phrases]
    assert not missing, f"catalog sells {missing} and the grounder cannot hear it"


def test_no_synonym_points_at_something_the_merchant_does_not_sell(lex):
    """A synonym map is the one hand-written thing left. It is allowed to
    translate a word; it is not allowed to promise a product. If a synonym's
    target leaves the catalog, this fails rather than silently mis-shopping."""
    dead = {k: v for k, v in SYNONYM.items() if v not in lex.phrases}
    assert not dead, f"synonyms resolve to nothing buyable: {dead}"


# ------------------------------------------------------------- what was said

def test_two_things_asked_for_are_two_things_bought(app):
    """The bug that started this: the parser kept one noun and discarded the
    rest, so the human paid for oil and never saw the rice again."""
    r = app.journey.run(utterance="order 3 kg rice and cooking oil under 2000",
                        user_id="usr_t1", now=NOW)
    got = " | ".join(names(r)).lower()
    assert "rice" in got and "oil" in got, got


def test_a_comma_separates_requests(app):
    r = app.journey.run(utterance="buy notebook, gel pen and highlighter under 900",
                        user_id="usr_t2", now=NOW)
    assert len(r.intent.requested_items) == 3


def test_adjectives_qualify_one_thing_rather_than_becoming_three(lex):
    """"waterproof trail shoes" is one purchase described three ways. Reading
    it as three purchases is how an agent buys you a lot of shoes."""
    g = ground("buy premium waterproof trail shoes", lex)
    assert len(g.items) == 1
    assert set(g.items[0].terms) >= {"trail", "shoes"}


def test_the_head_noun_beats_the_accessory(app):
    """Someone who says "earbuds" wants earbuds, not a case for earbuds. The
    case is cheaper, so price-and-rating scoring picked it and REMIT then
    refused the whole purchase for drift. FAILURES #17."""
    r = app.journey.run(utterance="buy earbuds under 3000", user_id="usr_t3", now=NOW)
    assert names(r), "nothing selected"
    assert "case" not in names(r)[0].lower(), names(r)


# ----------------------------------------------------------- what was not said

def test_an_unstocked_noun_is_refused_not_substituted(app):
    """The whole thesis in one test. A catalog that cannot answer 'helicopter'
    says so; it does not hand over a yoga mat and hope. FAILURES #13."""
    r = app.journey.run(utterance="buy a helicopter under 500000",
                        user_id="usr_t4", now=NOW)
    assert r.cart is None
    assert r.payment_state == "NONE"
    assert r.telemetry.get("abstained") is True


def test_too_expensive_is_not_the_same_sentence_as_not_stocked(app):
    """"We do not stock sunscreen" is false when we stock it at Rs 699 and the
    human allowed Rs 500. The human is owed the real number. FAILURES #19."""
    r = app.journey.run(utterance="buy sunscreen under 500", user_id="usr_t5", now=NOW)
    assert "cheapest" in r.note and "699" in r.note, r.note
    assert "does not stock" not in r.note


def test_filler_next_to_a_real_request_is_not_a_second_request(lex):
    """Counting every unknown word as something the human wanted and did not
    get turned 87 correct purchases into interruptions. FAILURES #18."""
    g = ground("yaar ek yoga mat order kar do teen hazaar tak", lex)
    assert len(g.items) == 1
    assert g.ungrounded == []
    assert "yaar" in g.noise


# ------------------------------------------------------------------- spelling

def test_a_typo_is_forgiven(app):
    r = app.journey.run(utterance="buy toothpast under 300", user_id="usr_t6", now=NOW)
    assert any("toothpaste" in n.lower() for n in names(r)), names(r)


def test_a_different_word_is_not_forgiven(lex):
    """The forgiving half is the dangerous half. A bounded edit distance must
    not turn a product this catalog has never heard of into a sale."""
    for word in ("helicopter", "ferrari", "kalashnikov", "bitcoin"):
        g = ground(f"buy a {word}", lex)
        assert g.items == [], f"{word} grounded to {g.items}"


def test_a_forgiven_typo_is_less_certain_than_a_correct_spelling():
    c = RuleCompiler()
    clean, _ = c.compile("buy toothpaste under 300", "u", NOW)
    typo, _ = c.compile("buy toothpast under 300", "u", NOW)
    assert typo.parse_confidence < clean.parse_confidence


# -------------------------------------------------------------- one consumer

def test_the_budget_phrase_is_not_also_read_as_a_product():
    """One span, one consumer. "under 900, fastest delivery option" was being
    read by the amount parser AND the grounder, which turned 'delivery' and
    'option' into two things the human wanted and could not have."""
    c = RuleCompiler()
    env, tel = c.compile("purchase foot cream under 900, fastest delivery option",
                         "u", NOW)
    assert env is not None
    assert tel["ungrounded"] == [], tel["ungrounded"]
    assert len(env.requested_items) == 1
    assert env.objective == "fastest_delivery"


def test_a_named_merchant_becomes_a_constraint_not_a_product():
    c = RuleCompiler()
    env, _ = c.compile("buy running shoes from Strideworks under 6000", "u", NOW)
    assert env.merchant_constraints, "merchant not recognised"
    assert all("strideworks" not in t for t in env.product_terms)
