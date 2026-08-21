"""Four arms, same corpus, same catalog, same seed. Real runs, no hardcoding.

    A  baseline            no agent optimisation, no integrity layer
    B  ai_buyer            AI buyer, no revenue engine, no integrity layer
    C  ai_optimised        AI buyer + revenue engine, NO integrity layer
    D  remit               AI buyer + revenue engine + REMIT integrity layer

C is the arm that matters. It is what "let the agent optimise revenue" looks
like without an authorisation boundary, and it is the honest comparison: if
D cannot beat C on revenue, the product's claim collapses to "safety costs
money", which would still be worth saying -- but it should be measured, not
assumed.

"No integrity layer" is implemented by making the policy permissive, not by
deleting it: caps to infinity, drift thresholds to 1.0, no purchase-authority
requirement, and a friction cost so high nothing ever escalates. Same code
path, different data -- which is the point of policy-as-data.
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, ".")

from eval.run_eval import _needs_human, load_cases, run_case
from remit.money import rupees

PERMISSIVE = {
    "max_transaction_paise": 10 ** 12,
    "session_exposure_paise": 10 ** 12,
    "daily_exposure_paise": 10 ** 12,
    "velocity_1h": 10 ** 6,
    "max_drift_auto": 1.0,
    "max_drift_stepup": 1.0,
    "min_parse_confidence": 0.0,
    "require_purchase_authority": False,
    "allow_agent_added_over_ceiling": True,
    "integrity_layer": False,
    "friction_floor_paise": 10 ** 12,     # never worth asking
    "friction_bps": 0,
}

ARMS = [
    {"name": "A_plain_checkout", "label": "no revenue engine, no integrity layer",
     "overrides": PERMISSIVE, "aggressiveness": 0.0, "accept_offers": "none",
     "human_confirms": True},
    {"name": "B_agent_unbounded", "label": "revenue engine ON, no integrity layer",
     "overrides": PERMISSIVE, "aggressiveness": 1.0, "accept_offers": "all",
     "human_confirms": True},
    {"name": "C_remit_approve", "label": "full REMIT, human approves at step-up",
     "overrides": None, "aggressiveness": 1.0, "accept_offers": "in_envelope",
     "human_confirms": True},
    {"name": "D_remit_decline", "label": "full REMIT, human declines every step-up",
     "overrides": None, "aggressiveness": 1.0, "accept_offers": "in_envelope",
     "human_confirms": False},
]


def run_arm(arm, cases) -> dict:
    outs = [run_case(c, arm) for c in cases]
    executed = [o for o in outs if o.result["payment_state"] in
                ("CREATED", "AUTHORIZED", "SUCCESS")]
    revenue = sum(o.revenue_paise for o in outs)
    margin = sum(o.margin_paise for o in outs)
    unauth = sum(o.unauthorized_paise for o in outs)
    stepups = sum(1 for o in outs if (o.result.get("authorization") or {}
                                      ).get("verdict") == "STEP_UP")
    denies = sum(1 for o in outs if (o.result.get("authorization") or {}
                                     ).get("verdict") == "DENY")
    upsell_rev = 0
    upsell_n = 0
    cross_n = 0
    for o in executed:
        cart = o.result.get("cart") or {"lines": []}
        for l in cart["lines"]:
            if l["origin"] == "upsell":
                upsell_rev += l["unit_price_paise"] * l["qty"]
                upsell_n += 1
            elif l["origin"] == "cross_sell":
                upsell_rev += l["unit_price_paise"] * l["qty"]
                cross_n += 1
    drifts = [o.result["drift"]["score"] for o in outs if o.result.get("drift")]
    return {
        "arm": arm["name"], "label": arm["label"], "n": len(outs),
        "transactions": len(executed),
        "revenue_paise": revenue, "revenue": rupees(revenue),
        "margin_paise": margin, "margin": rupees(margin),
        "aov_paise": revenue // len(executed) if executed else 0,
        "aov": rupees(revenue // len(executed)) if executed else "\u20b90.00",
        "attach_revenue_paise": upsell_rev, "attach_revenue": rupees(upsell_rev),
        "upsell_lines": upsell_n, "cross_sell_lines": cross_n,
        "attach_rate": round((upsell_n + cross_n) / len(executed), 4) if executed else 0.0,
        "unauthorized_paise": unauth, "unauthorized": rupees(unauth),
        "unauthorized_txns": sum(1 for o in outs if o.unauthorized_paise),
        "human_confirmations": stepups,
        "human_confirmation_rate": round(stepups / len(outs), 4),
        "denials": denies,
        "mean_drift": round(sum(drifts) / len(drifts), 4) if drifts else 0.0,
    }


def main(out_path="eval/results/experiments.json"):
    cases = load_cases()
    results = [run_arm(a, cases) for a in ARMS]
    base = next(r for r in results if r["arm"] == "A_plain_checkout")
    for r in results:
        r["incremental_revenue_paise"] = r["revenue_paise"] - base["revenue_paise"]
        r["incremental_revenue"] = rupees(r["incremental_revenue_paise"])
        r["incremental_revenue_pct"] = (
            round(100 * r["incremental_revenue_paise"] / base["revenue_paise"], 2)
            if base["revenue_paise"] else 0.0)
    report = {"corpus_size": len(cases), "arms": results}
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=2)
    return report


if __name__ == "__main__":
    rep = main()
    cols = ["arm", "transactions", "revenue", "incremental_revenue",
            "aov", "attach_rate", "unauthorized", "human_confirmations"]
    widths = [16, 12, 12, 14, 10, 11, 13, 14]
    print("  ".join(c.ljust(w) for c, w in zip(cols, widths)))
    print("-" * (sum(widths) + 2 * len(cols)))
    for r in rep["arms"]:
        print("  ".join(str(r[c]).ljust(w) for c, w in zip(cols, widths)))
    print()
    A, B, C, D = rep["arms"]
    print(f"B (agent, no boundary) moved {B['unauthorized']} across "
          f"{B['unauthorized_txns']} transactions the human never authorised, "
          f"for {B['incremental_revenue']} of extra revenue.")
    print(f"C (REMIT, human says yes) moved {C['unauthorized']} unauthorised and "
          f"captured {C['incremental_revenue']} ({C['incremental_revenue_pct']}%), "
          f"asking {C['human_confirmations']} times across {C['n']} journeys.")
    print(f"D (REMIT, human says no to everything) is the floor: "
          f"{D['incremental_revenue']} ({D['incremental_revenue_pct']}%), "
          f"still {D['unauthorized']} unauthorised.")
