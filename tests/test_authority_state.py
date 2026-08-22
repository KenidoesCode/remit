"""The authority's lifecycle, as a machine that refuses.

REMIT already had one real state machine -- the payment store, with a
transition table, an exception and a persisted history. The authority itself
had none. Its lifecycle was eight free strings assigned to a dataclass field at
eight points in `journey.run`, each written once at the end of the function.
Nothing rejected a move between them because there were no moves.

That worked exactly as long as a journey stayed one synchronous call, and it
left the system unable to answer the question a control plane exists to answer:
*what state is this authority in, and what is it allowed to do next?*

The tests below cover every legal edge, every illegal edge that matters, and --
the part that keeps this from being decorative -- that the real payment path
drives it.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from itertools import product

import pytest

from remit.assembly import build
from remit.domain.authority import (LEGAL, STATES, TERMINAL, AuthorityMachine,
                                    IllegalTransition, legal)
from remit.domain.risk import Exposure
from remit.exec.razorpay import FakeGateway

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def app():
    return build(now=NOW, gateway=FakeGateway())


@pytest.fixture
def m(app):
    return AuthorityMachine(app.db)


def run(app, utterance, user="usr_fsm", **kw):
    return app.journey.run(utterance=utterance, user_id=user, now=NOW,
                           exposure=Exposure(), **kw)


def path(app, intent_id):
    return [h["to_state"] for h in app.authority.history(intent_id)]


# ───────────────────────────────────────────────────────── the table itself

def test_every_state_is_reachable_and_every_terminal_is_terminal():
    reachable = {"DRAFT"} | {t for nxt in LEGAL.values() for t in nxt}
    assert reachable == set(STATES), set(STATES) - reachable
    for t in TERMINAL:
        assert LEGAL[t] == set(), f"{t} claims to be terminal and is not"
    assert TERMINAL == {"SETTLED", "REJECTED", "EXPIRED", "REVOKED",
                        "CANCELLED", "FAILED"}


def test_a_lifecycle_starts_at_draft_and_nowhere_else():
    for s in STATES:
        assert legal(None, s) is (s == "DRAFT"), s


@pytest.mark.parametrize("frm,to", sorted(
    (f, t) for f, nxt in LEGAL.items() for t in nxt))
def test_every_legal_transition_is_accepted(m, frm, to):
    """One test per edge. Parametrised rather than looped so a broken edge
    names itself instead of failing the whole table."""
    iid = f"int_{frm}_{to}".lower()
    m.open(intent_id=iid, user_id="usr_fsm", now=NOW)
    if frm != "DRAFT":
        _walk_to(m, iid, frm)
    assert m.advance(intent_id=iid, to=to, now=NOW, cause="test") == to
    assert m.state(iid) == to


def _walk_to(m, iid, target):
    """Shortest legal route from DRAFT, breadth first."""
    from collections import deque
    prev, q = {"DRAFT": None}, deque(["DRAFT"])
    while q:
        cur = q.popleft()
        if cur == target:
            break
        for nxt in LEGAL[cur]:
            if nxt not in prev:
                prev[nxt] = cur
                q.append(nxt)
    assert target in prev, f"{target} is unreachable from DRAFT"
    route, cur = [], target
    while prev[cur] is not None:
        route.append(cur)
        cur = prev[cur]
    for step in reversed(route):
        m.advance(intent_id=iid, to=step, now=NOW, cause="walk")


ILLEGAL = [
    ("REVOKED", "EXECUTING"), ("REVOKED", "APPROVED"), ("REVOKED", "EXECUTED"),
    ("EXPIRED", "APPROVED"), ("EXPIRED", "EXECUTING"),
    ("EXECUTED", "EXECUTING"), ("EXECUTED", "APPROVED"),
    ("SETTLED", "EXECUTING"), ("SETTLED", "EXECUTED"),
    ("CANCELLED", "APPROVED"), ("CANCELLED", "EXECUTING"),
    ("REJECTED", "AUTHORIZED"), ("FAILED", "EXECUTING"),
    ("DRAFT", "EXECUTING"), ("DRAFT", "APPROVED"), ("DRAFT", "EXECUTED"),
    ("INTERPRETED", "EXECUTING"), ("GROUNDED", "EXECUTED"),
    ("PENDING_STEP_UP", "EXECUTING"), ("AUTHORIZED", "APPROVED"),
]


@pytest.mark.parametrize("frm,to", ILLEGAL)
def test_every_illegal_transition_is_refused(m, frm, to):
    iid = f"int_bad_{frm}_{to}".lower()
    m.open(intent_id=iid, user_id="usr_fsm", now=NOW)
    if frm != "DRAFT":
        _walk_to(m, iid, frm)
    with pytest.raises(IllegalTransition):
        m.advance(intent_id=iid, to=to, now=NOW, cause="test")
    assert m.state(iid) == frm, "a refused move changed the state anyway"


def test_the_money_states_cannot_be_re_entered(m):
    """The one that matters most. EXECUTED -> EXECUTING would be a second
    financial effect wearing the first one's identity."""
    for terminal_money in ("EXECUTED", "SETTLED"):
        assert "EXECUTING" not in LEGAL[terminal_money]


def test_revocation_is_legal_right_up_to_the_money(m):
    """An order exists and the money has not moved: stopping there is exactly
    what pressing the kill switch means. After it moves it is a refund, which
    is a different authority, and this machine must not claim otherwise."""
    assert "REVOKED" in LEGAL["EXECUTING"]
    assert "REVOKED" not in LEGAL["EXECUTED"]
    assert "REVOKED" not in LEGAL["SETTLED"]


def test_an_unknown_state_is_not_a_state(m):
    m.open(intent_id="int_unknown", user_id="usr_fsm", now=NOW)
    with pytest.raises(IllegalTransition):
        m.advance(intent_id="int_unknown", to="SUPERAPPROVED", now=NOW,
                  cause="test")


def test_the_same_state_twice_is_one_move(m):
    """Two webhooks reporting one outcome are one outcome. The payment machine
    settled on this rule for the same reason."""
    m.open(intent_id="int_same", user_id="usr_fsm", now=NOW)
    m.advance(intent_id="int_same", to="INTERPRETED", now=NOW, cause="a")
    m.advance(intent_id="int_same", to="INTERPRETED", now=NOW, cause="b")
    assert [h["to_state"] for h in m.history("int_same")] == \
        ["DRAFT", "INTERPRETED"]


def test_opening_twice_rejoins_rather_than_resetting(m):
    m.open(intent_id="int_re", user_id="usr_fsm", now=NOW)
    m.advance(intent_id="int_re", to="INTERPRETED", now=NOW, cause="x")
    assert m.open(intent_id="int_re", user_id="usr_fsm", now=NOW) == "INTERPRETED"
    assert m.state("int_re") == "INTERPRETED"


# ──────────────────────────────────────── it is the real path, not a diagram

def test_an_automatic_purchase_walks_the_whole_machine(app):
    r = run(app, "buy running shoes under 5000", human_confirms=True)
    assert r.payment_state == "CREATED"
    assert path(app, r.intent.intent_id) == [
        "DRAFT", "INTERPRETED", "GROUNDED", "AUTHORIZED", "EXECUTING",
        "EXECUTED"]


def test_a_step_up_stops_at_pending(app):
    r = run(app, "buy whisky under 2000")
    assert r.payment_state == "AWAITING_HUMAN"
    assert path(app, r.intent.intent_id) == [
        "DRAFT", "INTERPRETED", "GROUNDED", "PENDING_STEP_UP"]


def test_an_approved_step_up_records_that_a_person_was_asked(app):
    """AUTHORIZED and APPROVED are not interchangeable. Recording a confirmed
    AUTO decision as APPROVED would claim a human made a decision they were
    never asked to make."""
    r = run(app, "buy whisky under 2000", human_confirms=True)
    assert r.payment_state == "CREATED"
    assert path(app, r.intent.intent_id) == [
        "DRAFT", "INTERPRETED", "GROUNDED", "PENDING_STEP_UP", "APPROVED",
        "EXECUTING", "EXECUTED"]


def test_a_hard_refusal_ends_at_rejected(app):
    r = run(app, "buy headphones under $5000")
    assert r.payment_state == "BLOCKED"
    assert path(app, r.intent.intent_id)[-1] == "REJECTED"


def test_a_revoked_authority_ends_at_revoked_not_rejected(app):
    """Both refuse. They are not the same event and the audit trail should not
    say they are: one is the policy, the other is the person."""
    app.revocations.revoke(user_id="usr_fsm", now=NOW)
    r = run(app, "buy running shoes under 5000", human_confirms=True)
    assert r.payment_state == "BLOCKED"
    assert path(app, r.intent.intent_id)[-1] == "REVOKED"


def test_a_declined_step_up_ends_cancelled(app):
    step = run(app, "buy whisky under 2000")
    assert step.payment_state == "AWAITING_HUMAN"
    no = run(app, "buy whisky under 2000", human_confirms=False)
    assert no.payment_state == "DECLINED_BY_HUMAN"
    assert path(app, no.intent.intent_id)[-1] == "CANCELLED"


def test_the_history_is_persisted_with_causes(app):
    r = run(app, "buy running shoes under 5000", human_confirms=True)
    hist = app.authority.history(r.intent.intent_id)
    assert all(h["cause"] for h in hist), hist
    assert all(h["ts"] for h in hist)
    assert hist[-1]["correlation_id"] == r.correlation_id


def test_concurrent_advances_cannot_both_win(app):
    """Two callers racing the same edge. Exactly one transition is recorded --
    the second finds the state already moved and either no-ops or is refused,
    never both applying."""
    m = AuthorityMachine(app.db)
    m.open(intent_id="int_race", user_id="usr_fsm", now=NOW)
    m.advance(intent_id="int_race", to="INTERPRETED", now=NOW, cause="setup")
    m.advance(intent_id="int_race", to="GROUNDED", now=NOW, cause="setup")

    def go(i):
        target = "AUTHORIZED" if i % 2 == 0 else "REVOKED"
        try:
            return m.advance(intent_id="int_race", to=target, now=NOW,
                             cause=f"racer {i}")
        except IllegalTransition:
            return "refused"

    with ThreadPoolExecutor(max_workers=8) as pool:
        out = [f.result() for f in [pool.submit(go, i) for i in range(8)]]

    landed = [h["to_state"] for h in m.history("int_race")]
    assert landed[:3] == ["DRAFT", "INTERPRETED", "GROUNDED"]
    assert m.state("int_race") in ("AUTHORIZED", "REVOKED")

    # The invariant is not "only one thread wins" -- GROUNDED -> AUTHORIZED ->
    # REVOKED is a perfectly good chain and two threads may each own one edge.
    # It is that the recorded history is a LEGAL WALK with no edge applied
    # twice. Before the conditional UPDATE this produced
    # [... AUTHORIZED, REVOKED, REVOKED]: two threads both read AUTHORIZED and
    # both wrote REVOKED, and "same state is a no-op" did not help because both
    # passed that check before either wrote.
    assert len(landed) == len(set(landed)), f"an edge was applied twice: {landed}"
    for a, b in zip(landed, landed[1:]):
        assert legal(a, b), f"illegal edge recorded: {a} -> {b} in {landed}"
    assert set(out) <= {"AUTHORIZED", "REVOKED", "refused"}
