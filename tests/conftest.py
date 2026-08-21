from datetime import timedelta

import pytest

from remit.grants.issuer import issue, new_keypair
from remit.intent.compiler import Catalog, StubCompiler
from remit.ledger.chain import Ledger
from remit.models import CatalogItem, SpendState
from remit.policy.engine import Policy
from tests.consts import T0



@pytest.fixture
def policy():
    return Policy.load("policy/default.yaml")


@pytest.fixture
def catalog():
    return Catalog([
        CatalogItem(item_id="atta_5kg", name="Atta 5kg", category="grocery",
                    unit_price_paise=25000),
        CatalogItem(item_id="milk_1l", name="Milk 1L", category="grocery",
                    unit_price_paise=6600),
        CatalogItem(item_id="rc_1000", name="Recharge 1000", category="recharge",
                    unit_price_paise=100000),
        CatalogItem(item_id="rc_10000", name="Recharge 10000", category="recharge",
                    unit_price_paise=1000000),
    ])


@pytest.fixture
def compiler():
    return StubCompiler({
        "usual groceries": {
            "items": [{"item_id": "atta_5kg", "qty": 1},
                      {"item_id": "milk_1l", "qty": 2}],
            "category": "grocery", "raw_confidence": 0.94,
            "stated_amount_paise": 38200, "user_ceiling_paise": 80000,
        },
        "das hazaar ka recharge": {
            "items": [{"item_id": "rc_10000", "qty": 1}],
            "category": "recharge", "raw_confidence": 0.61,
            "stated_amount_paise": 1000000,
            "alternatives": [{"description": "das sau = Recharge 1000",
                              "probability": 0.29, "amount_paise": 100000}],
        },
        "big grocery run": {
            "items": [{"item_id": "atta_5kg", "qty": 40}],
            "category": "grocery", "raw_confidence": 0.97,
            "stated_amount_paise": 1000000,
        },
    })


@pytest.fixture
def keys():
    return new_keypair()


@pytest.fixture
def remit(keys, policy):
    sk, _ = keys
    # Granted 2 days before T0 so the 24h cool-off and the 24h envelope
    # notice have both aged out.
    return issue(
        signing_key=sk, subject="usr_kk", agent_instance="agt_demo",
        merchant_ids=["mch_grocer"], categories=["grocery", "recharge"],
        per_txn_ceiling_paise=120000, aggregate_ceiling_paise=800000,
        count_ceiling=8, valid_days=90, now=T0 - timedelta(days=2),
        policy_version=policy.version)


@pytest.fixture
def ledger():
    return Ledger(":memory:")


@pytest.fixture
def spend():
    return SpendState()
