"""One ceiling, spent more than once.

CEIL-001 compares one basket against the sentence that authorised it. That was
the entire meaning of a ceiling in this system, and it left an obvious move
available to an agent that could not fit inside the limit: use more baskets.

    "buy chips under 200"      -> 190 rupees, AUTO
    "buy biscuits under 200"   -> 180 rupees, AUTO
    "buy soap under 200"       -> 195 rupees, AUTO
                                  ---
                                  565 rupees, against a person who said 200

Every one of those passes on its own merits. Nothing in the policy engine could
see it, because nothing in the policy engine looked at more than one basket.

SPLIT-001 is soft on purpose. Buying twice under the same instruction is
something people do -- the first was the wrong size, the delivery split, they
just want another packet. What it is not is something an agent decides alone.
The clause asks. A hard version would turn an ordinary second purchase into a
dead end, and being wrong in that direction costs somebody their socks.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from remit.assembly import build
from remit.domain.risk import Exposure
from remit.exec.razorpay import FakeGateway

NOW = datetime(2026, 8, 22, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def app():
    return build(now=NOW, gateway=FakeGateway())


def run(app, utterance, user="usr_split", now=NOW, **kw):
    return app.journey.run(utterance=utterance, user_id=user, now=now,
                           exposure=Exposure(), **kw)


def failed(r):
    return [c.clause_id for c in r.authorization.clauses if not c.passed]


# ────────────────────────────────────────────────────────── the attack

def test_a_second_basket_under_the_same_instruction_asks(app):
    first = run(app, "buy chips under 200", human_confirms=True)
    assert first.payment_state == "CREATED"
    assert "SPLIT-001" not in failed(first), "fired with nothing to aggregate"

    second = run(app, "buy biscuits under 200")
    assert "SPLIT-001" in failed(second), failed(second)
    assert second.payment_state == "AWAITING_HUMAN", second.payment_state


def test_the_aggregate_is_named_in_rupees_not_hinted_at(app):
    run(app, "buy chips under 200", human_confirms=True)
    r = run(app, "buy biscuits under 200")
    clause = next(c for c in r.authorization.clauses if c.clause_id == "SPLIT-001")
    assert "baskets" in clause.detail and "instruction" in clause.detail
    assert "₹" in clause.detail, clause.detail


def test_a_different_instruction_is_a_different_authority(app):
    """The obvious wrong implementation is to sum everything against the
    smallest ceiling anyone mentioned. That refuses a person who bought socks
    and then a laptop, which is arithmetic rather than consent."""
    run(app, "buy chips under 200", human_confirms=True)
    other = run(app, "buy running shoes under 5000")
    assert "SPLIT-001" not in failed(other), failed(other)


def test_a_different_ceiling_is_a_different_authority(app):
    run(app, "buy chips under 200", human_confirms=True)
    other = run(app, "buy biscuits under 900")
    assert "SPLIT-001" not in failed(other), failed(other)


def test_an_unbounded_request_has_nothing_to_aggregate_against(app):
    """No stated ceiling, no mandate to split. Those requests are governed by
    the policy caps and by drift, which is a different argument."""
    run(app, "buy chips under 200", human_confirms=True)
    r = run(app, "buy biscuits")
    assert "SPLIT-001" not in failed(r), failed(r)


def test_it_is_per_person(app):
    run(app, "buy chips under 200", user="usr_one", human_confirms=True)
    r = run(app, "buy biscuits under 200", user="usr_two")
    assert "SPLIT-001" not in failed(r), (
        "one person's spending counted against another's instruction")


def test_it_expires_with_the_window(app):
    run(app, "buy chips under 200", human_confirms=True)
    later = run(app, "buy biscuits under 200", now=NOW + timedelta(hours=3))
    assert "SPLIT-001" not in failed(later), (
        "buying the same thing tomorrow is a new decision, not a suspicion")


def test_it_is_soft(app):
    """The verdict must be STEP_UP, never DENY. A person who says yes gets
    their second packet."""
    run(app, "buy chips under 200", human_confirms=True)
    asked = run(app, "buy biscuits under 200")
    assert asked.authorization.verdict.value == "STEP_UP", asked.authorization.verdict
    approved = run(app, "buy biscuits under 200", human_confirms=True)
    assert approved.payment_state == "CREATED", approved.payment_state


def test_the_same_sentence_sent_twice_is_not_a_split(app):
    """The regression that this clause caused on its first day.

    A resend -- a double-tapped button, a chat UI that resends, an agent with a
    retry policy -- is ONE basket, and idempotency already returns the one
    payment it made. Counting it as a split stepped up on the most ordinary
    event in the system: three suites went red at once, all of them saying the
    same thing, which is that a person repeating themselves is not an attack.
    """
    first = run(app, "buy chips under 200", human_confirms=True)
    assert first.payment_state == "CREATED"
    again = run(app, "buy chips under 200", human_confirms=True)
    assert "SPLIT-001" not in failed(again), failed(again)
    assert again.replayed is True
    assert again.payment_id == first.payment_id


def test_a_failed_payment_does_not_count_against_the_mandate(app):
    """Money that never moved is not money that was spent."""
    app.db.execute("UPDATE payments SET state='FAILED'")
    run(app, "buy chips under 200", human_confirms=True)
    app.db.execute("UPDATE payments SET state='FAILED'")
    r = run(app, "buy biscuits under 200")
    assert "SPLIT-001" not in failed(r), failed(r)


def test_the_policy_engine_still_does_no_io(app):
    """SPLIT-001's input arrives as an argument like every other input. If the
    clause had reached for the database, replay, the frontier sweep and the
    arena would all have stopped being possible."""
    import inspect

    import remit.policy.authorize as mod
    src = inspect.getsource(mod)
    for forbidden in ("db.execute", "sqlite3", "requests.", "httpx.",
                      "datetime.now", "utcnow()"):
        assert forbidden not in src, f"{forbidden} appeared in the policy engine"
