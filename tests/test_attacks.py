"""The attack suite has to be able to fail.

An attack list where everything holds is a marketing asset. These tests check
the harness itself: that each attack targets a stated invariant, that the one
attack aimed at a gap REMIT genuinely has does succeed, and that all the others
hold.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from remit.assembly import build
from remit.exec.razorpay import FakeGateway
from remit.lab.attacks import ATTACKS, BY_KEY, run_attack

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)

# Empty, and it did not used to be.
#
# `identity_forgery` succeeded until FAILURES #32: user_id arrived in the
# request body and nothing verified it. The expectation was updated when the
# fix landed rather than the test deleted, which is the only honest way to
# retire a failing security test.
#
# The cost of an empty set is real: this suite can no longer demonstrate, from
# its own results, that it is capable of detecting a failure. That job now
# belongs to `test_an_attack_that_raises_counts_as_a_break` below, which is
# weaker evidence and worth saying out loud.
EXPECTED_TO_BREAK: set[str] = set()


def fresh():
    return build(now=NOW, gateway=FakeGateway())


@pytest.mark.parametrize("key", [a.key for a in ATTACKS])
def test_each_attack_reaches_its_verdict(key):
    r = run_attack(BY_KEY[key], fresh(), NOW)
    assert r["what_happened"], "an attack that says nothing is not a result"
    if key in EXPECTED_TO_BREAK:
        assert r["broke"] is True, (
            "the attack aimed at a gap REMIT actually has now reports success. "
            "Either it was fixed -- update EXPECTED_TO_BREAK -- or the harness "
            "has stopped being able to detect a failure.")
    else:
        assert r["broke"] is False, f"{key}: {r['what_happened']}"
        assert r["stopped_by"], "held, but cannot name what stopped it"


def test_every_attack_declares_the_invariant_it_targets():
    for a in ATTACKS:
        assert a.invariant and " " in a.invariant, a.key
        assert a.surface in ("intent", "catalog", "payment"), a.key


def test_the_suite_covers_all_three_surfaces():
    surfaces = {a.surface for a in ATTACKS}
    assert surfaces == {"intent", "catalog", "payment"}
    for s in surfaces:
        assert sum(1 for a in ATTACKS if a.surface == s) >= 5, s


def test_an_attack_that_raises_counts_as_a_break():
    """A crash is not a defence."""
    from remit.lab.attacks import Attack

    def boom(app, now):
        raise RuntimeError("kaboom")

    r = run_attack(Attack("x", "intent", "n", "i", boom), fresh(), NOW)
    assert r["broke"] is True
    assert "kaboom" in r["what_happened"]
