"""The autonomy / revenue frontier.

Sweep the policy from permissive to strict and re-run the whole corpus at
each point. Every point is a real run, not an interpolation.

The question the chart answers, which is the question the whole product
exists to ask:

    How much autonomy can we give the agent before the extra merchant value
    stops being worth the human friction and the unauthorised exposure?

Three knobs are swept, in that order of severity:
  friction_bps      how expensive we consider an unnecessary question
  max_drift_auto    how much deviation from the envelope may execute unasked
  integrity_layer   whether the envelope is consulted at all

The third one is not decoration. Sweeping only the first two produced a chart
with ZERO unauthorised movement at every single point, right out to
`max_drift_auto = 1.0` -- because the hard clauses (the ceiling, the purchase
authority, the exposure caps) never yield to a threshold. The curve had no knee,
so it demonstrated nothing, and I shipped it that way for a week and said so
rather than quietly deleting the chart.

The knee only exists where the boundary itself is switched off, which is the
honest place for it: the last three points below are what an agent with a
payment key and no intent envelope actually does. FAILURES #26.
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, ".")

from eval.run_eval import load_cases, run_case
from remit.money import rupees

# The boundary is data, so switching it off is data too -- exactly the same
# code runs at every point on this curve.
OFF = {"integrity_layer": False}
WIDE = dict(OFF, max_transaction_paise=10 ** 12, session_exposure_paise=10 ** 12,
            daily_exposure_paise=10 ** 12, velocity_1h=10 ** 6,
            min_parse_confidence=0.0, require_purchase_authority=False,
            allow_agent_added_over_ceiling=True)

GRID = [
    # label,               friction_bps, max_drift_auto, aggressiveness, extra
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
    # --- past here the envelope stops being consulted -------------------
    ("envelope ignored",  10 ** 9,       1.00, 1.0, OFF),
    ("no limits either",  10 ** 9,       1.00, 1.0, WIDE),
]


def point(label, bps, drift_auto, aggr, cases, human_confirms=True,
          extra: dict | None = None) -> dict:
    arm = {"name": label,
           "overrides": {"friction_bps": bps, "max_drift_auto": drift_auto,
                         **(extra or {})},
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
        "integrity_layer": (extra or {}).get("integrity_layer", True),
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
    pts = [point(g[0], g[1], g[2], g[3], cases, True,
                 g[4] if len(g) > 4 else None) for g in GRID]
    floor = [point(g[0], g[1], g[2], g[3], cases, False,
                   g[4] if len(g) > 4 else None) for g in GRID]
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
