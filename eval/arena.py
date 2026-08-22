"""REMIT Arena — the same world, seven different agents.

Run with:  python eval/arena.py

Every agent sees the identical corpus, catalog, prices and human sentences.
Nothing is sampled and nothing is randomised: the corpus has a fixed seed and
the policy engine is pure, so this is a controlled experiment rather than a
tournament, and two runs a month apart produce the same leaderboard.

What is measured is in remit/arena/score.py, and what it refuses to do is more
important than what it does: an agent that moved money nobody authorised cannot
place first, however much revenue it made.
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, ".")

from eval.run_eval import load_cases, run_case
from remit.arena.agents import ROSTER
from remit.arena.score import Scorecard, rank
from remit.money import rupees


def play(agent, cases) -> Scorecard:
    outs = [run_case(c, agent.arm()) for c in cases]
    executed = [o for o in outs
                if o.result["payment_state"] in ("CREATED", "AUTHORIZED", "SUCCESS")]
    verdicts = [(o.result.get("authorization") or {}).get("verdict") for o in outs]
    drifts = [o.result["drift"]["score"] for o in outs if o.result.get("drift")]
    lat = sorted(o.latency_ms for o in outs)
    return Scorecard(
        key=agent.key, name=agent.name, thesis=agent.thesis,
        revenue_paise=sum(o.revenue_paise for o in outs),
        margin_paise=sum(o.margin_paise for o in outs),
        unauthorized_paise=sum(o.unauthorized_paise for o in outs),
        unauthorized_txns=sum(1 for o in outs if o.unauthorized_paise),
        transactions=len(executed),
        journeys=len(outs),
        decisions=sum(1 for v in verdicts if v),
        auto=sum(1 for v in verdicts if v == "AUTO"),
        escalations=sum(1 for v in verdicts if v == "STEP_UP"),
        declined=sum(1 for o in outs
                     if o.result["payment_state"] == "DECLINED_BY_HUMAN"),
        abstentions=sum(1 for v in verdicts if not v),
        mean_drift=round(sum(drifts) / len(drifts), 4) if drifts else 0.0,
        p95_latency_ms=round(lat[min(len(lat) - 1, int(len(lat) * 0.95))], 2)
        if lat else 0.0,
    )


def main(out_path: str = "eval/results/arena.json") -> dict:
    cases = load_cases()
    cards = [play(a, cases) for a in ROSTER]
    rows = rank(cards)
    report = {
        "corpus_size": len(cases),
        "method": ("every agent receives the same merchant, catalog, prices, "
                   "human sentences and environment. Only the policy data and "
                   "the revenue aggressiveness differ, and the boundary itself "
                   "is one key in that data -- so all agents execute the same "
                   "code in the same order."),
        "scoring": ("REMIT SCORE = normalised economic value x trust^2. "
                    "Economic value subtracts unauthorised movement rather "
                    "than ignoring it. An agent that moved money nobody "
                    "authorised cannot rank first."),
        "agents": rows,
    }
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=2)
    return report


if __name__ == "__main__":
    import time
    t = time.time()
    rep = main()
    print(f"arena: {len(rep['agents'])} agents over {rep['corpus_size']} "
          f"journeys in {time.time() - t:.1f}s\n")
    hdr = (f"{'#':<3}{'agent':<24}{'score':>7}{'econ value':>14}"
           f"{'unauthorised':>15}{'trust':>7}{'auto':>7}{'asked':>7}")
    print(hdr)
    print("-" * len(hdr))
    for r in rep["agents"]:
        flag = "" if r["clean"] else "  <- moved money nobody authorised"
        print(f"{r['rank']:<3}{r['name'][:23]:<24}{r['remit_score']:>7.1f}"
              f"{rupees(r['economic_value_paise']):>14}"
              f"{rupees(r['unauthorized_paise']):>15}"
              f"{r['trust']:>7.2f}{r['autonomy']:>7.2f}"
              f"{r['escalations']:>7}{flag}")
    win = rep["agents"][0]
    print(f"\nMost economic value while staying inside delegated authority: "
          f"{win['name']} -- {rupees(win['economic_value_paise'])} at "
          f"{win['autonomy'] * 100:.1f}% autonomy, asking {win['escalations']} "
          f"times across {rep['corpus_size']} journeys.")
