"""Two scenarios, no API key needed. Run: python demo/walkthrough.py

This is the spine of the 5-minute video, in text form. If this stops
printing what you expect, something in the trust path broke.
"""
from datetime import datetime, timedelta, timezone

from remit.exec.razorpay import FakeGateway
from remit.gateway import Gateway
from remit.grants.issuer import issue, new_keypair, revoke, verify
from remit.intent.compiler import Catalog, StubCompiler
from remit.ledger.chain import Ledger
from remit.models import CatalogItem, SpendState
from remit.money import rupees
from remit.policy.engine import Policy

NOW = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)

CATALOG = Catalog([
    CatalogItem(item_id="atta_5kg", name="Atta 5kg", category="grocery",
                unit_price_paise=25000),
    CatalogItem(item_id="milk_1l", name="Milk 1L", category="grocery",
                unit_price_paise=6600),
    CatalogItem(item_id="rc_1000", name="Recharge 1000", category="recharge",
                unit_price_paise=100000),
    CatalogItem(item_id="rc_10000", name="Recharge 10000", category="recharge",
                unit_price_paise=1000000),
])

SCRIPT = {
    "usual groceries under 800": {
        "items": [{"item_id": "atta_5kg", "qty": 1}, {"item_id": "milk_1l", "qty": 2}],
        "category": "grocery", "raw_confidence": 0.94,
        "stated_amount_paise": 38200, "user_ceiling_paise": 80000,
    },
    "recharge kar do": {
        "items": [{"item_id": "rc_1000", "qty": 1}],
        "category": "recharge", "raw_confidence": 0.61,
        "stated_amount_paise": 100000,
        "alternatives": [{"description": "Recharge 10000 (das hazaar)",
                          "probability": 0.29, "amount_paise": 1000000}],
    },
    "das hazaar ka recharge kar do": {
        "items": [{"item_id": "rc_10000", "qty": 1}],
        "category": "recharge", "raw_confidence": 0.61,
        "stated_amount_paise": 1000000,
        "alternatives": [{"description": "das sau -> Recharge 1000",
                          "probability": 0.29, "amount_paise": 100000}],
    },
}


def show(title, out):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)
    if out.intent:
        print(f"  cart           {rupees(out.intent.computed_amount_paise)}"
              f"   ({len(out.intent.items)} lines)")
        if out.intent.alternatives:
            a = out.intent.alternatives[0]
            print(f"  rejected alt   {a.description}   p={a.probability}")
    d = out.decision
    if d:
        print(f"  verdict        {d.verdict.value}")
        print(f"  E[loss]        {rupees(d.expected_loss_paise)}"
              f"   vs cost of asking {rupees(d.friction_cost_paise)}")
        print(f"  clauses        {len(d.clause_hits)} evaluated, "
              f"failed: {d.failed_clauses or 'none'}")
        print(f"  reason         {d.reason}")
        if d.counterfactual:
            print(f"  counterfactual {d.counterfactual}")
    print(f"  order          {out.order['id'] if out.order else '-- no money moved --'}")
    if out.note:
        print(f"  note           {out.note}")


def main():
    sk, pk = new_keypair()
    policy = Policy.load("policy/default.yaml")
    ledger = Ledger(":memory:")
    fake = FakeGateway()
    g = Gateway(ledger=ledger, policy=policy, catalog=CATALOG,
                compiler=StubCompiler(SCRIPT), gw=fake)

    remit = issue(signing_key=sk, subject="usr_kk", agent_instance="agt_claude_01",
                  merchant_ids=["mch_grocer"], categories=["grocery", "recharge"],
                  per_txn_ceiling_paise=120000, aggregate_ceiling_paise=800000,
                  count_ceiling=8, valid_days=90, now=NOW - timedelta(days=2),
                  policy_version=policy.version)
    print(f"remit {remit.remit_id}   signature verifies: {verify(remit, pk)}")

    show("1. HAPPY PATH   'order my usual groceries, under 800'",
         g.handle(utterance="usual groceries under 800", merchant_id="mch_grocer",
                  remit=remit, spend=SpendState(), now=NOW))

    show("2. THE REFUSAL  'das hazaar ka recharge kar do' (user meant das sau)",
         g.handle(utterance="das hazaar ka recharge kar do",
                  merchant_id="mch_grocer", remit=remit, spend=SpendState(), now=NOW))

    show("3. THE STEP-UP   inside every ceiling, but the model is unsure",
         g.handle(utterance="recharge kar do", merchant_id="mch_grocer",
                  remit=remit, spend=SpendState(), now=NOW))
    print("  ^ nothing here breached a limit. It stopped because being wrong "
          "costs more than asking.")

    show("4. RETRY STORM  the agent repeats itself four times",
         [g.handle(utterance="usual groceries under 800", merchant_id="mch_grocer",
                   remit=remit, spend=SpendState(), now=NOW) for _ in range(4)][-1])
    print(f"  create_order calls reaching the gateway: "
          f"{len([c for c in fake.calls if c[0] == 'create_order'])}   (must be 1)")

    dead = revoke(remit, now=NOW, signing_key=sk)
    show("5. REVOKED MID-FLIGHT",
         g.handle(utterance="usual groceries under 800", merchant_id="mch_grocer",
                  remit=dead, spend=SpendState(), now=NOW))

    ok, bad = ledger.verify_chain()
    print(f"\nledger: {'intact' if ok else f'TAMPERED at seq {bad}'}")


if __name__ == "__main__":
    main()
