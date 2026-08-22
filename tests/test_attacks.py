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

# The one that is supposed to succeed. REMIT has no authentication, so anyone
# can claim any user id -- and a suite that cannot demonstrate a real failure
# cannot be trusted when it reports success.
EXPECTED_TO_BREAK = {"identity_forgery"}


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
