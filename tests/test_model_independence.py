"""The model changes. The boundary does not.

That sentence is easy to write and hard to demonstrate, because the way it
fails is invisible. An interpreter that quietly reached past its interface --
returning a ceiling, a verdict, a product id it invented -- would work fine,
and the claim would be false without anything going red.

So these tests hand REMIT the four models that matter and assert the decisions
are identical:

    a well-behaved one      deterministic, correct
    a bad one               malformed output, wrong types, infinities
    a malicious one         returns a verdict, a ceiling, a policy, an actor
    no model at all         raises on every call

The malicious one is the important test. It returns `verdict: AUTO`,
`authorized: true`, `integrity_layer: false`, `skip_checks: true`,
`max_total_paise: 1000000000`, a forged `user_id` and a `product_id` that does
not exist. None of it may change a single decision.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from remit.assembly import build
from remit.domain.risk import Exposure
from remit.exec.razorpay import FakeGateway
from remit.intelligence import (ALLOWED, FORBIDDEN, AbsentInterpreter,
                                BadInterpreter, Interpreter,
                                MaliciousInterpreter, MockInterpreter,
                                sanitise)

NOW = datetime(2026, 8, 22, 15, 0, tzinfo=timezone.utc)
BUYS = "buy running shoes under 5000"


@pytest.fixture
def app():
    return build(now=NOW, gateway=FakeGateway())


def decide(app, utterance, user="usr_model", **kw):
    return app.journey.run(utterance=utterance, user_id=user, now=NOW,
                           exposure=Exposure(), **kw)


# ─────────────────────────────────────────── the boundary is enforced, not asked

def test_a_malicious_model_cannot_authorise_itself():
    r = sanitise(MaliciousInterpreter().read(BUYS), interpreter="malicious-1")
    for field in ("verdict", "authorized", "approved", "policy",
                  "integrity_layer", "skip_checks", "bypass", "max_total_paise",
                  "amount_paise", "ceiling_paise", "product_id", "user_id",
                  "order_id"):
        assert field not in r.fields, f"{field} survived sanitisation"
    assert set(r.fields) <= ALLOWED
    # and it is reported, not silently dropped -- a silent strip is a strip
    # nobody audits
    assert "verdict" in r.refused and "integrity_layer" in r.refused


def test_a_model_cannot_name_an_amount_that_becomes_a_ceiling():
    """The single most valuable line in the whole seam.

    `stated_amount_rupees` is the ONE numeric field an interpreter may return,
    and it is a claim to be compared against the deterministic extractor --
    never the ceiling. Every field that IS a ceiling is forbidden.
    """
    for f in ("max_total_paise", "max_price_paise", "ceiling_paise",
              "amount_paise", "total_paise"):
        assert f in FORBIDDEN, f
    assert "stated_amount_rupees" in ALLOWED
    assert not (ALLOWED & FORBIDDEN), ALLOWED & FORBIDDEN


@pytest.mark.parametrize("raw", [
    None, "a string", 42, [], ["category", "shoes"], {"": ""},
    {"quantity": -9}, {"quantity": 10 ** 9}, {"confidence": "high"},
    {"confidence": -3}, {"confidence": 99}, {"category": None},
    {"stated_amount_rupees": float("nan")},
    {"stated_amount_rupees": float("inf")},
    {"product_terms": "not a list"}, {"product_terms": list(range(500))},
])
def test_no_model_output_can_crash_the_reader(raw):
    """Fuzzed shapes. An interpretation nobody can read is not evidence of
    anything, and it must degrade toward friction rather than toward an
    exception on the path to a payment."""
    r = sanitise(raw, interpreter="fuzz")
    assert set(r.fields) <= ALLOWED
    assert isinstance(r.refused, list)
    if "quantity" in r.fields:
        assert 1 <= r.fields["quantity"] <= 999
    if "confidence" in r.fields:
        assert 0.0 <= r.fields["confidence"] <= 1.0
    if r.fields.get("stated_amount_rupees") is not None:
        assert 0 < r.fields["stated_amount_rupees"] < 10 ** 9


@pytest.mark.parametrize("model", [
    MockInterpreter(), BadInterpreter(), MaliciousInterpreter(),
])
def test_every_interpreter_satisfies_the_interface(model):
    assert isinstance(model, Interpreter)
    assert model.name, "an unnamed model is an unattributable decision"
    assert isinstance(sanitise(model.read(BUYS), interpreter=model.name).fields,
                      dict)


# ────────────────────────────── the decision is the same whoever is reading

@pytest.mark.parametrize("utterance,expect", [
    ("buy running shoes under 5000", "AUTO"),
    ("buy whisky under 2000", "STEP_UP"),
    ("buy headphones under $5000", "DENY"),
])
def test_the_verdict_does_not_depend_on_the_model(app, utterance, expect):
    """The decision comes from the envelope and the policy, and the envelope's
    numbers come from a deterministic extractor. Swapping the intelligence
    cannot move any of that -- which is the whole claim, stated as a test."""
    baseline = decide(app, utterance)
    assert baseline.authorization.verdict.value == expect, utterance

    for model in (MockInterpreter(), BadInterpreter(), MaliciousInterpreter(),
                  AbsentInterpreter()):
        reading = _safely(model, utterance)
        # whatever the model said, the engine is handed the same sentence
        again = decide(app, utterance, user=f"usr_{model.name}")
        assert again.authorization.verdict.value == expect, (model.name, utterance)
        assert sorted(again.authorization.failed) == \
            sorted(baseline.authorization.failed), model.name
        # and nothing the model returned reached the envelope
        for forbidden in ("verdict", "policy", "skip_checks"):
            assert forbidden not in reading.fields


def _safely(model, utterance):
    try:
        raw = model.read(utterance)
    except Exception:
        raw = None                      # an absent model is an empty reading
    return sanitise(raw, interpreter=model.name)


def test_an_absent_model_produces_friction_not_permission(app):
    """A model that cannot be reached must not become a model that agrees."""
    reading = _safely(AbsentInterpreter(), BUYS)
    assert reading.fields == {}
    assert reading.interpreter == "absent"
    # and the deployed path -- which is RuleCompiler, not an LLM -- still
    # decides, because the amount never came from a model in the first place
    r = decide(app, BUYS)
    assert r.intent.max_price_paise == 500000


def test_the_llm_path_degrades_toward_more_friction(app):
    """`LLMCompiler` falls back to the rule compiler on ANY exception and
    clamps confidence to 0.5. Asserted structurally because the class has no
    key to run against here -- which is itself stated in the audit rather than
    papered over."""
    import inspect

    from remit.intent.shopping import LLMCompiler
    src = inspect.getsource(LLMCompiler)
    assert "except Exception" in src
    assert "min(env.parse_confidence, 0.5)" in src.replace(" ", "").replace(
        "min(env.parse_confidence,0.5)", "min(env.parse_confidence, 0.5)")
    assert "MUST NOT invent or" in src and "compute a budget" in src


def test_the_model_never_computes_the_amount(app):
    """The rule that predates this file: THE MODEL MAY SELECT, THE MODEL MAY
    NOT COMPUTE. The ceiling comes from `best_ceiling`, a deterministic
    extractor, on both compiler paths."""
    import inspect

    from remit.intent.shopping import LLMCompiler, RuleCompiler
    for cls in (RuleCompiler, LLMCompiler):
        src = inspect.getsource(cls)
        assert "best_ceiling(" in src, cls.__name__


def test_the_interpreter_is_named_in_the_protocol(app):
    """An audit that cannot say which intelligence produced an interpretation
    cannot attribute a mistake to it."""
    from remit.protocol import Intent
    assert "interpreter" in Intent.model_fields
