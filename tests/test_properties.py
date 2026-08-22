"""Properties, not examples.

The suite had two `@given` properties in 537 tests, and both drew scalars. That
is property-based testing in the sense that `hypothesis` was imported.

The point of generating cases is not volume -- more hand-written examples from
the author who wrote the system are still that author's imagination. It is to
search combinations nobody chose: an expired mandate that is also revoked, a
foreign currency with a per-unit ceiling, a quantity of nine against a stated
total, an approval redeemed against a cart whose price moved twice.

Each property below is an invariant from the brief, stated once, checked
against inputs the author did not pick. Where a property is bounded, the bound
is stated -- a property that quietly excludes the interesting half of its input
space is an example with extra machinery.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from remit.assembly import build
from remit.domain.authority import LEGAL, STATES, AuthorityMachine, legal
from remit.domain.risk import Exposure
from remit.exec.razorpay import FakeGateway
from remit.grants.approval import cart_hash
from remit.intent.amounts import detect_currency
from remit.intent.grounding import _strip_negations, _tokens
from remit.policy.authorize import Verdict

NOW = datetime(2026, 8, 22, 14, 0, tzinfo=timezone.utc)

# The catalog is fixed and seeded, so these are the nouns a journey can
# actually ground. Generating random strings would exercise the abstain path
# over and over and tell us nothing about authorization.
NOUNS = ["running shoes", "yoga mat", "earbuds", "notebook", "chips",
         "rice", "soap", "whisky", "headphones", "water bottle"]
CEILINGS = st.integers(min_value=100, max_value=60000)
QTYS = st.integers(min_value=1, max_value=9)

SLOW = settings(max_examples=60, deadline=None,
                suppress_health_check=[HealthCheck.function_scoped_fixture])
FAST = settings(max_examples=250, deadline=None)


@pytest.fixture
def app():
    return build(now=NOW, gateway=FakeGateway())


def utterance(noun, ceiling, qty=1, negation=None, currency=""):
    q = "" if qty == 1 else f"{qty} "
    neg = f" but not {negation}" if negation else ""
    return f"buy {q}{noun}{neg} under {currency}{ceiling}"


# ───────────────────────────────── 1. money never exceeds what was stated

@given(noun=st.sampled_from(NOUNS), ceiling=CEILINGS, qty=QTYS,
       confirm=st.booleans())
@SLOW
def test_an_automatic_purchase_never_exceeds_the_stated_ceiling(
        app, noun, ceiling, qty, confirm):
    """The universal invariant, generated rather than enumerated.

    Bounded honestly: this asserts it for AUTO, where no person was asked. A
    STEP_UP that a human approved may exceed the original ceiling, and it does
    so by AMENDING the envelope to a new version with a reason (FAILURES #29) --
    that is consent, not drift, and asserting otherwise here would be asserting
    that people may not change their minds.
    """
    r = app.journey.run(utterance=utterance(noun, ceiling, qty),
                        user_id="usr_prop", now=NOW, exposure=Exposure(),
                        human_confirms=confirm)
    if r.authorization is None or r.totals is None:
        return
    if r.authorization.verdict is not Verdict.AUTO:
        return
    stated = r.intent.ceiling_paise()
    assert stated is not None
    assert r.totals.total_paise <= stated, (
        f"AUTO spent {r.totals.total_paise} against a stated {stated}")


@given(noun=st.sampled_from(NOUNS), ceiling=CEILINGS)
@SLOW
def test_a_blocked_journey_never_leaves_a_payment(app, noun, ceiling):
    r = app.journey.run(utterance=utterance(noun, ceiling), user_id="usr_prop",
                        now=NOW, exposure=Exposure(), human_confirms=True)
    if r.payment_state in ("BLOCKED", "DECLINED_BY_HUMAN", "APPROVAL_REJECTED"):
        assert r.order_id is None
        row = app.db.execute(
            "SELECT COUNT(*) c FROM payments WHERE correlation_id=?",
            (r.correlation_id,)).fetchone()["c"]
        assert row == 0, "a refused journey created a payment row"


# ──────────────────────────── 2. the envelope binds who, what, where, when

@given(noun=st.sampled_from(NOUNS), ceiling=CEILINGS,
       thief=st.text(alphabet="abcdef0123456789", min_size=4, max_size=12))
@SLOW
def test_an_approval_is_never_redeemable_by_another_actor(
        app, noun, ceiling, thief):
    r = app.journey.run(utterance="buy whisky under 2000", user_id="usr_owner",
                        now=NOW, exposure=Exposure())
    assume(r.approval is not None)
    stolen = app.journey.run(utterance="buy whisky under 2000",
                             user_id="usr_" + thief, now=NOW,
                             exposure=Exposure(), human_confirms=True,
                             approval_token=r.approval["token"])
    assert stolen.payment_state != "CREATED"
    assert "wrong_actor" in (stolen.note or "") or \
        stolen.payment_state == "BLOCKED", stolen.note


@given(qty=st.integers(min_value=2, max_value=9))
@SLOW
def test_a_cart_that_changed_never_redeems_the_old_approval(app, qty):
    r = app.journey.run(utterance="buy whisky under 2000", user_id="usr_prop",
                        now=NOW, exposure=Exposure())
    assume(r.approval is not None)
    tampered = app.journey.run(
        utterance="buy whisky under 2000", user_id="usr_prop", now=NOW,
        exposure=Exposure(), human_confirms=True,
        approval_token=r.approval["token"], inject={"qty": qty})
    assert tampered.payment_state == "APPROVAL_REJECTED"
    assert "cart_changed" in tampered.note


@given(hours=st.integers(min_value=1, max_value=72))
@SLOW
def test_an_expired_authority_never_executes(app, hours):
    late = NOW + timedelta(hours=hours)
    r = app.journey.run(utterance="buy running shoes under 5000",
                        user_id="usr_prop", now=NOW, exposure=Exposure())
    assume(r.approval is not None or r.payment_state == "CREATED")
    after = app.journey.run(utterance="buy running shoes under 5000",
                            user_id="usr_prop", now=late, exposure=Exposure(),
                            human_confirms=True, inject={"expire": True})
    assert after.payment_state != "CREATED" or after.replayed, after.payment_state


@given(noun=st.sampled_from(NOUNS), ceiling=CEILINGS)
@SLOW
def test_a_revoked_authority_never_executes(app, noun, ceiling):
    app.revocations.revoke(user_id="usr_revoked", now=NOW)
    r = app.journey.run(utterance=utterance(noun, ceiling),
                        user_id="usr_revoked", now=NOW, exposure=Exposure(),
                        human_confirms=True)
    assert r.payment_state == "BLOCKED", r.payment_state
    assert r.order_id is None
    assert "revoked" in (r.note or "").lower(), (
        f"a revoked principal was told {r.note!r} instead of why")


# ─────────────────────────────────────────── 3. the unit is never invented

@given(amount=st.integers(min_value=1, max_value=999999),
       sym=st.sampled_from(["$", "USD ", "€", " dollars", " euros", "£"]))
@FAST
def test_a_foreign_unit_is_never_read_as_rupees(amount, sym):
    text = (f"buy shoes under {sym}{amount}" if sym[0] in "$€£"
            else f"buy shoes under {amount}{sym}")
    assert detect_currency(text) != "INR", text


@given(amount=st.integers(min_value=1, max_value=999999),
       marker=st.sampled_from(["", "rs ", "₹", "inr ", "rs. "]))
@FAST
def test_a_rupee_amount_is_never_read_as_foreign(amount, marker):
    assert detect_currency(f"buy shoes under {marker}{amount}") == "INR"


# ─────────────────────────────────── 4. a negation never becomes a demand

@given(noun=st.sampled_from(NOUNS),
       bad=st.sampled_from(["white", "refurbished", "basmati", "used",
                            "second hand", "black", "premium"]),
       ceiling=CEILINGS)
@SLOW
def test_an_excluded_word_is_never_what_gets_bought(app, noun, bad, ceiling):
    """The inversion, as a property. `not X` may never produce X."""
    r = app.journey.run(utterance=utterance(noun, ceiling, negation=bad),
                        user_id="usr_prop", now=NOW, exposure=Exposure(),
                        human_confirms=True)
    if r.intent is not None:
        for word in bad.split():
            assert word in r.intent.excluded_attributes, (
                f"{word!r} was said as an exclusion and is not in the envelope")
    for line in (r.cart.lines if r.cart else []):
        assert bad.split()[0] not in line.name.lower().split(), line.name


@given(text=st.text(min_size=0, max_size=120))
@FAST
def test_the_negation_parser_never_raises_and_never_invents(text):
    """Fuzzed against arbitrary text, because the grounder sees whatever a
    person types. Two properties: it does not raise, and it never reports an
    exclusion that is not a word in the input."""
    kept, excluded = _strip_negations(_tokens(text))
    lowered = text.lower()
    for word in excluded:
        assert word in lowered, (word, text)
    assert len(kept) <= len(_tokens(text))


# ──────────────────────────────────── 5. the state machine is a real order

@given(walk=st.lists(st.sampled_from(sorted(STATES)), min_size=1, max_size=10))
@FAST
def test_no_random_walk_ever_reaches_money_illegally(walk):
    """Generated walks through the transition table. Whatever the sequence, an
    authority may not arrive at EXECUTED except through EXECUTING, and may not
    leave a terminal state at all."""
    cur = "DRAFT"
    reached = ["DRAFT"]
    for want in walk:
        if legal(cur, want):
            cur = want
            reached.append(want)
    for a, b in zip(reached, reached[1:]):
        assert b in LEGAL[a]
    if "EXECUTED" in reached:
        assert reached[reached.index("EXECUTED") - 1] == "EXECUTING"
    for i, s in enumerate(reached[:-1]):
        assert LEGAL[s], f"{s} is terminal and was left for {reached[i + 1]}"


@given(seed=st.integers(min_value=0, max_value=10 ** 6))
@settings(max_examples=40, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_the_machine_never_records_an_illegal_edge(app, seed):
    """Drive the machine with arbitrary requested transitions and assert the
    persisted history is always a legal walk."""
    import random
    rng = random.Random(seed)
    m = AuthorityMachine(app.db)
    iid = f"int_prop_{seed}"
    m.open(intent_id=iid, user_id="usr_prop", now=NOW)
    for _ in range(12):
        try:
            m.advance(intent_id=iid, to=rng.choice(sorted(STATES)), now=NOW,
                      cause="fuzz")
        except Exception:
            pass
    hist = [h["to_state"] for h in m.history(iid)]
    for a, b in zip(hist, hist[1:]):
        assert legal(a, b), f"illegal edge persisted: {a} -> {b} in {hist}"


# ─────────────────────────────── 6. one request, at most one financial effect

@given(noun=st.sampled_from(NOUNS), ceiling=CEILINGS,
       repeats=st.integers(min_value=2, max_value=6))
@SLOW
def test_repeating_one_request_never_creates_a_second_payment(
        app, noun, ceiling, repeats):
    u = utterance(noun, ceiling)
    states = [app.journey.run(utterance=u, user_id="usr_idem", now=NOW,
                              exposure=Exposure(), human_confirms=True)
              for _ in range(repeats)]
    ids = {r.payment_id for r in states if r.payment_id}
    assert len(ids) <= 1, f"{len(ids)} payments for one repeated request"


@given(noun=st.sampled_from(NOUNS), ceiling=CEILINGS)
@SLOW
def test_the_cart_hash_changes_whenever_the_money_changes(app, noun, ceiling):
    """The property the approval token rests on: two carts that would charge
    the same person the same money for the same things hash the same, and
    anything else does not."""
    r = app.journey.run(utterance=utterance(noun, ceiling), user_id="usr_prop",
                        now=NOW, exposure=Exposure())
    assume(r.cart is not None and r.cart.lines)
    before = cart_hash(r.cart)
    bumped = r.cart.model_copy(deep=True)
    bumped.lines[0].qty += 1
    assert cart_hash(bumped) != before
    repriced = r.cart.model_copy(deep=True)
    repriced.lines[0].unit_price_paise += 1
    assert cart_hash(repriced) != before
    same = r.cart.model_copy(deep=True)
    same.lines.reverse()
    assert cart_hash(same) == before, "line order changed the hash"
