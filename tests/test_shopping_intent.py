from datetime import datetime, timezone

import pytest

from remit.intent.amounts import best_ceiling, extract
from remit.intent.shopping import RuleCompiler

NOW = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def c():
    return RuleCompiler()


def test_ambiguous_quantity_takes_the_conservative_reading(c):
    """FAILURES.md 2026-08-21 14:20 -- the parser doubled the budget."""
    bare, _ = c.compile("buy 2x earbuds under 3000", "u", NOW)
    assert bare.ceiling_paise() == 300000, "must read as a TOTAL ceiling"
    each, _ = c.compile("buy 2x earbuds under 3000 each", "u", NOW)
    assert each.ceiling_paise() == 600000, "explicit per-unit must be honoured"
    assert bare.parse_confidence < each.parse_confidence


def test_abstains_rather_than_guessing(c):
    env, tel = c.compile("hello there", "u", NOW)
    assert env is None and tel["abstained"] is True


def test_no_purchase_authority_without_a_buy_verb(c):
    env, _ = c.compile("show me running shoes under 5000", "u", NOW)
    assert env.purchase_authority is False


@pytest.mark.parametrize("utterance,rupees", [
    ("running shoes under \u20b95000", 5000),
    ("running shoes under 5k", 5000),
    ("shoes under Rs 4,999", 4999),
    ("\u096b\u0966\u0966\u0966 se kam ke shoes", 5000),
    ("das hazaar ka budget", 10000),
])
def test_amount_forms(utterance, rupees):
    top, _ = best_ceiling(utterance)
    assert top is not None and top.rupees() == rupees


def test_low_confidence_on_the_1000x_failure_shape():
    """'teen rupay' is the documented truncation of 'teen hazaar rupay'.
    We must never treat a bare Rs 3 as a confident ceiling."""
    cands = [c for c in extract("teen rupay ka payment")]
    assert all(c.confidence < 0.5 for c in cands if c.paise <= 500)


def test_rejected_amounts_are_recorded_not_discarded(c):
    env, tel = c.compile("running shoes under 5000, budget is 8000 max", "u", NOW)
    assert tel["rejected_amounts"], "the alternative must survive into the audit trail"
