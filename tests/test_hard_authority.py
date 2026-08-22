"""Hard constraints are hard.

A number the human said is not a preference the ranker gets to weigh. It is a
limit, and there are exactly two acceptable outcomes for it: the envelope
carries it and the system respects it, or the system refuses and says why.

"The envelope quietly did not record it" is not on that list, and it is what
happened for every budget below Rs 50 until FAILURES #28.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from remit.assembly import build
from remit.intent.shopping import RuleCompiler

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)

# Amount, and the surface forms a person might use for it. Deliberately spans
# the old Rs 50 floor in both directions.
AMOUNTS = [1, 5, 20, 45, 49, 50, 99, 200, 999, 2000, 25000]
FORMS = ["under {n}", "under Rs {n}", "under ₹{n}", "below {n}",
         "{n} se kam", "{n} tak", "upto {n}"]
NOUNS = ["chips", "soap", "rice", "notebook", "running shoes", "toothpaste"]


@pytest.fixture(scope="module")
def app():
    return build(db_path=":memory:", now=NOW, live=False)


@pytest.mark.parametrize("amount", AMOUNTS)
@pytest.mark.parametrize("form", FORMS)
def test_a_stated_ceiling_is_always_recorded(form, amount):
    """Every explicit budget reaches the envelope, at every magnitude."""
    utterance = f"buy chips {form.format(n=amount)}"
    env, tel = RuleCompiler().compile(utterance, "u", NOW)
    assert env is not None, utterance
    assert env.ceiling_paise() == amount * 100, (
        f"{utterance!r} recorded {env.ceiling_paise()} for a Rs {amount} budget")


@pytest.mark.parametrize("noun", NOUNS)
@pytest.mark.parametrize("amount", [1, 20, 45, 200, 5000])
def test_nothing_above_the_ceiling_is_ever_bought(app, noun, amount):
    """The property, stated once: if money moved, it moved inside the line.

    An abstention is a pass. Buying something dearer than the human allowed is
    the only failure -- and buying it because the ceiling was never recorded is
    the same failure wearing a different hat.
    """
    utterance = f"buy {noun} under {amount}"
    r = app.journey.run(utterance=utterance,
                        user_id=f"usr_hard{hash(utterance) % 9973}", now=NOW)
    if r.cart is None:
        assert r.note, "refused without saying why"
        return
    ceiling = r.intent.ceiling_paise()
    assert ceiling == amount * 100, f"ceiling lost: {ceiling}"
    assert r.totals.total_paise <= ceiling or (
        r.authorization and r.authorization.verdict.value != "AUTO"), (
        f"{utterance!r} auto-executed {r.totals.total_paise} against {ceiling}")


def test_an_impossible_budget_refuses_with_the_real_price(app):
    r = app.journey.run(utterance="buy chips under 20", user_id="usr_hard_x", now=NOW)
    assert r.cart is None
    assert "cheapest chips" in r.note and "₹20.00" in r.note, r.note
