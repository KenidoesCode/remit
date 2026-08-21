"""The policy engine is the component an interviewer will interrogate.
These tests are the answer to 'prove it is pure' and 'prove it explains'."""
from datetime import timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from remit.models import Alternative, IntentItem, SpendState, Verdict
from remit.intent.compiler import build_intent
from remit.policy.engine import evaluate

from tests.consts import T0


def _intent(catalog, items, category, conf, **kw):
    return build_intent(utterance="x", merchant_id="mch_grocer",
                        catalog=catalog, items=items, category=category,
                        raw_confidence=conf, **kw)


def test_allow_happy_path(catalog, policy, remit, spend):
    i = _intent(catalog, [IntentItem(item_id="atta_5kg", qty=1)], "grocery", 0.99,
                stated_amount_paise=25000)
    d = evaluate(intent=i, remit=remit, spend=spend, p_correct=0.99,
                 policy=policy, now=T0)
    assert d.verdict is Verdict.ALLOW
    assert d.failed_clauses == []


def test_low_confidence_high_stakes_steps_up(catalog, policy, remit, spend):
    """The centrepiece of the demo: 10k recharge at p=0.61 must not execute."""
    i = _intent(catalog, [IntentItem(item_id="rc_10000", qty=1)], "recharge", 0.61,
                stated_amount_paise=1000000,
                alternatives=[Alternative(description="das sau", probability=0.29)])
    d = evaluate(intent=i, remit=remit, spend=spend, p_correct=0.61,
                 policy=policy, now=T0)
    assert d.verdict is not Verdict.ALLOW
    assert d.expected_loss_paise > d.friction_cost_paise


def test_user_stated_ceiling_is_binding(catalog, policy, remit, spend):
    i = _intent(catalog, [IntentItem(item_id="atta_5kg", qty=3)], "grocery", 0.99,
                user_ceiling_paise=50000)
    d = evaluate(intent=i, remit=remit, spend=spend, p_correct=0.99,
                 policy=policy, now=T0)
    assert d.verdict is Verdict.DENY
    assert "USER-001" in d.failed_clauses


def test_aggregate_exposure_across_all_grants(catalog, policy, remit):
    """The hole nothing in the real ecosystem closes: each grant passes,
    the union does not."""
    i = _intent(catalog, [IntentItem(item_id="atta_5kg", qty=1)], "grocery", 0.99)
    s = SpendState(subject_live_exposure_paise=2_490_000)
    d = evaluate(intent=i, remit=remit, spend=s, p_correct=0.99,
                 policy=policy, now=T0)
    assert d.verdict is Verdict.DENY
    assert "AGG-001" in d.failed_clauses
    assert "ALL live grants" in (d.counterfactual or "")


def test_revocation_denies(catalog, policy, remit, spend, keys):
    from remit.grants.issuer import revoke
    sk, _ = keys
    dead = revoke(remit, now=T0, signing_key=sk)
    i = _intent(catalog, [IntentItem(item_id="atta_5kg", qty=1)], "grocery", 0.99)
    d = evaluate(intent=i, remit=dead, spend=spend, p_correct=0.99,
                 policy=policy, now=T0)
    assert d.verdict is Verdict.DENY
    assert "LIFE-002" in d.failed_clauses


def test_cooloff_is_a_reduced_cap_not_a_freeze(catalog, policy, keys):
    """UPI Circle restricts spend to Rs 5,000 in the first 24h after a
    delegate is added. It is NOT a freeze -- get this right, they might ask."""
    from remit.grants.issuer import issue
    sk, _ = keys
    fresh = issue(signing_key=sk, subject="u", agent_instance="a",
                  merchant_ids=["mch_grocer"], categories=["grocery"],
                  per_txn_ceiling_paise=120000, aggregate_ceiling_paise=800000,
                  count_ceiling=8, valid_days=90, now=T0 - timedelta(hours=2),
                  policy_version=policy.version)
    small = _intent(catalog, [IntentItem(item_id="milk_1l", qty=1)], "grocery", 0.99)
    d = evaluate(intent=small, remit=fresh, spend=SpendState(), p_correct=0.99,
                 policy=policy, now=T0)
    assert "COOL-001" in [c.clause_id for c in d.clause_hits]
    assert [c for c in d.clause_hits if c.clause_id == "COOL-001"][0].passed


def test_envelope_notice_must_age_24h(catalog, policy, keys):
    """RBI e-mandate 2026 wants notice 24h before debit."""
    from remit.grants.issuer import issue
    sk, _ = keys
    justnow = issue(signing_key=sk, subject="u", agent_instance="a",
                    merchant_ids=["mch_grocer"], categories=["grocery"],
                    per_txn_ceiling_paise=120000, aggregate_ceiling_paise=800000,
                    count_ceiling=8, valid_days=90, now=T0,
                    policy_version=policy.version)
    i = _intent(catalog, [IntentItem(item_id="milk_1l", qty=1)], "grocery", 0.99)
    d = evaluate(intent=i, remit=justnow, spend=SpendState(), p_correct=0.99,
                 policy=policy, now=T0)
    assert "ENV-001" in d.failed_clauses


def test_purity_same_inputs_same_output(catalog, policy, remit, spend):
    i = _intent(catalog, [IntentItem(item_id="atta_5kg", qty=1)], "grocery", 0.9)
    a = evaluate(intent=i, remit=remit, spend=spend, p_correct=0.9,
                 policy=policy, now=T0)
    b = evaluate(intent=i, remit=remit, spend=spend, p_correct=0.9,
                 policy=policy, now=T0)
    assert a.model_dump() == b.model_dump()


@settings(max_examples=300, deadline=None)
@given(qty=st.integers(min_value=1, max_value=200),
       p=st.floats(min_value=0.0, max_value=1.0),
       spent=st.integers(min_value=0, max_value=2_000_000),
       exposure=st.integers(min_value=0, max_value=5_000_000))
def test_invariant_no_allow_without_full_clause_chain(qty, p, spent, exposure):
    """THE invariant. No input may produce an ALLOW with a failed clause."""
    from remit.intent.compiler import Catalog
    from remit.models import CatalogItem
    from remit.policy.engine import Policy
    from remit.grants.issuer import issue, new_keypair

    pol = Policy.load("policy/default.yaml")
    cat = Catalog([CatalogItem(item_id="atta_5kg", name="a", category="grocery",
                               unit_price_paise=25000)])
    sk, _ = new_keypair()
    s = issue(signing_key=sk, subject="u", agent_instance="a",
              merchant_ids=["mch_grocer"], categories=["grocery"],
              per_txn_ceiling_paise=120000, aggregate_ceiling_paise=800000,
              count_ceiling=8, valid_days=90, now=T0 - timedelta(days=2),
              policy_version=pol.version)
    i = build_intent(utterance="u", merchant_id="mch_grocer", catalog=cat,
                     items=[IntentItem(item_id="atta_5kg", qty=qty)],
                     category="grocery", raw_confidence=p)
    st_ = SpendState(per_remit_spent_paise={s.remit_id: spent},
                     subject_live_exposure_paise=exposure)
    d = evaluate(intent=i, remit=s, spend=st_, p_correct=p, policy=pol, now=T0)
    if d.verdict is Verdict.ALLOW:
        assert d.failed_clauses == [], d.failed_clauses
        assert d.expected_loss_paise <= d.friction_cost_paise
