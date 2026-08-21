"""The autonomy / revenue frontier.

Sweep the policy from permissive to strict and re-run the whole corpus at
each point. Every point is a real run, not an interpolation.

The question the chart answers, which is the question the whole product
exists to ask:

    How much autonomy can we give the agent before the extra merchant value
    stops being worth the human friction and the unauthorised exposure?

Two knobs are swept because they are the two that actually move the
boundary:
  friction_bps    how expensive we consider an unnecessary question
  max_drift_auto  how much deviation from the envelope may execute unasked
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, ".")

from eval.run_eval import load_cases, run_case
from remit.money import rupees

GRID = [
    # label,               friction_bps, max_drift_auto, aggressiveness
    ("locked",                    0,     0.00, 0.0),
    ("very strict",              50,     0.00, 0.5),
    ("strict",                  150,     0.01, 1.0),
    ("cautious",                300,     0.03, 1.0),
    ("balanced (default)",      500,     0.05, 1.0),
    ("relaxed",                 800,     0.10, 1.0),
    ("loose",                  1200,     0.20, 1.0),
    ("very loose",             2000,     0.35, 1.0),
    ("permissive",             8000,     0.60, 1.0),
    ("unbounded",         10 ** 9,       1.00, 1.0),
]


def point(label, bps, drift_auto, aggr, cases, human_confirms=True) -> dict:
    arm = {"name": label,
           "overrides": {"friction_bps": bps, "max_drift_auto": drift_auto},
           "aggressiveness": aggr, "accept_offers": "in_envelope",
           "human_confirms": human_confirms}
    outs = [run_case(c, arm) for c in cases]
    n = len(outs)
    auto = sum(1 for o in outs if (o.result.get("authorization") or {}
                                   ).get("verdict") == "AUTO")
    step = sum(1 for o in outs if (o.result.get("authorization") or {}
                                   ).get("verdict") == "STEP_UP")
    executed = [o for o in outs if o.result["payment_state"] in
                ("CREATED", "AUTHORIZED", "SUCCESS")]
    revenue = sum(o.revenue_paise for o in outs)
    margin = sum(o.margin_paise for o in outs)
    unauth = sum(o.unauthorized_paise for o in outs)
    drifts = [o.result["drift"]["score"] for o in outs if o.result.get("drift")]
    return {
        "label": label, "friction_bps": bps, "max_drift_auto": drift_auto,
        "aggressiveness": aggr,
        "autonomy": round(auto / n, 4),
        "human_friction_per_100": round(100 * step / n, 2),
        "revenue_paise": revenue, "revenue": rupees(revenue),
        "margin_paise": margin,
        "unauthorized_paise": unauth, "unauthorized": rupees(unauth),
        "transactions": len(executed),
        "mean_drift": round(sum(drifts) / len(drifts), 4) if drifts else 0.0,
    }


def main(out_path="eval/results/frontier.json"):
    cases = load_cases()
    # Two human behaviours, reported as a bracket rather than one flattering
    # series: the human who approves what they are asked, and the human who
    # declines everything. A real merchant sits between them.
    pts = [point(l, b, d, a, cases, True) for l, b, d, a in GRID]
    floor = [point(l, b, d, a, cases, False) for l, b, d, a in GRID]
    for p, f in zip(pts, floor):
        p["revenue_if_human_declines_paise"] = f["revenue_paise"]
        p["revenue_if_human_declines"] = f["revenue"]
        p["transactions_if_human_declines"] = f["transactions"]
    report = {"corpus_size": len(cases), "points": pts,
              "note": ("revenue is bracketed: the headline series assumes the human "
                       "approves at a step-up; revenue_if_human_declines is the floor "
                       "where every step-up is refused")}
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=2)
    return report


if __name__ == "__main__":
    rep = main()
    hdr = (f"{'policy':22}{'autonomy':>10}{'ask/100':>9}{'revenue (yes)':>15}"
           f"{'revenue (no)':>15}{'unauthorised':>14}")
    print(hdr)
    print("-" * len(hdr))
    for p in rep["points"]:
        print(f"{p['label']:22}{p['autonomy']*100:9.1f}%{p['human_friction_per_100']:9.1f}"
              f"{p['revenue']:>15}{p['revenue_if_human_declines']:>15}"
              f"{p['unauthorized']:>14}")
    print()
    safe = [p for p in rep["points"] if p["unauthorized_paise"] == 0]
    if safe:
        best = max(safe, key=lambda p: p["revenue_paise"])
        print(f"Most revenue with ZERO unauthorised movement: '{best['label']}' -- "
              f"{best['revenue']} at {best['autonomy']*100:.1f}% autonomy and "
              f"{best['human_friction_per_100']:.1f} confirmations per 100 journeys.")
    leak = [p for p in rep["points"] if p["unauthorized_paise"] > 0]
    if leak:
        first = min(leak, key=lambda p: p["max_drift_auto"])
        print(f"The boundary breaks at '{first['label']}' "
              f"(max_drift_auto={first['max_drift_auto']}): {first['unauthorized']} "
              f"starts moving without being asked for.")
