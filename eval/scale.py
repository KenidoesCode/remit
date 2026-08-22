#!/usr/bin/env python3
"""Measure before drawing a diagram.

The brief says: do not prematurely build Kubernetes, first measure, then
identify the actual bottleneck. So this loads REMIT with increasing concurrency
against one process and records what happens, per stage, with the hardware
written down.

It is not a load test of a production system. It is a load test of THIS system,
whose whole point is that the authorization decision is pure and cheap, and the
number worth knowing is where the cost actually is.

    python eval/scale.py            writes eval/results/scale.json
"""
from __future__ import annotations

import json
import os
import platform
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from remit.assembly import build                     # noqa: E402
from remit.domain.risk import Exposure               # noqa: E402
from remit.exec.razorpay import FakeGateway          # noqa: E402
from remit.observe import percentiles, reset         # noqa: E402
from remit.policy.authorize import authorize         # noqa: E402

NOW = datetime(2026, 8, 22, 17, 0, tzinfo=timezone.utc)
NOUNS = ["running shoes", "yoga mat", "earbuds", "notebook", "chips", "soap",
         "water bottle", "rice"]


def hardware() -> dict:
    return {
        "python": platform.python_version(),
        "machine": platform.machine(),
        "system": platform.system(),
        "cpus": os.cpu_count(),
        "note": "a shared container, not a benchmark rig -- read the shape, "
                "not the absolute numbers",
    }


def journeys(app, n: int, workers: int) -> dict:
    """n journeys through the whole pipeline, `workers` at a time."""
    def one(i):
        t = time.perf_counter()
        app.journey.run(utterance=f"buy {NOUNS[i % len(NOUNS)]} under {2000 + i}",
                        user_id=f"usr_load_{i}", now=NOW, exposure=Exposure(),
                        human_confirms=True)
        return (time.perf_counter() - t) * 1000

    t0 = time.perf_counter()
    if workers == 1:
        lat = [one(i) for i in range(n)]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            lat = [f.result() for f in [pool.submit(one, i) for i in range(n)]]
    wall = time.perf_counter() - t0
    lat.sort()
    return {
        "n": n, "workers": workers,
        "wall_s": round(wall, 3),
        "throughput_per_s": round(n / wall, 1) if wall else 0,
        "p50_ms": round(lat[len(lat) // 2], 2),
        "p95_ms": round(lat[int(len(lat) * .95)], 2),
        "p99_ms": round(lat[min(len(lat) - 1, int(len(lat) * .99))], 2),
        "max_ms": round(lat[-1], 2),
    }


def decisions_only(app, n: int) -> dict:
    """The pure part, alone.

    This is the number the whole architecture rests on: `authorize()` does no
    I/O, so re-deciding a basket is free. It is why the frontier sweep, the
    Arena's 3,780 journeys and the property line all exist.
    """
    r = app.journey.run(utterance="buy running shoes under 5000",
                        user_id="usr_pure", now=NOW, exposure=Exposure())
    from remit.domain.drift import compute_drift
    from remit.domain.risk import assess
    env, cart, totals = r.intent, r.cart, r.totals
    drift = compute_drift(env=env, cart=cart, totals=totals, catalog_version=1)
    risk = assess(env=env, total_paise=totals.total_paise, drift=drift,
                  exposure=Exposure(), now=NOW, parse_confidence=0.9,
                  friction_floor_paise=5000, friction_bps=120,
                  session_cap_paise=10 ** 7, daily_cap_paise=10 ** 8,
                  velocity_cap_1h=12)
    lat = []
    for _ in range(n):
        t = time.perf_counter()
        authorize(env=env, cart=cart, totals=totals, drift=drift, risk=risk,
                  exposure=Exposure(), policy=app.policy, now=NOW,
                  catalog_version=1)
        lat.append((time.perf_counter() - t) * 1_000_000)     # microseconds
    lat.sort()
    return {"n": n, "p50_us": round(lat[len(lat) // 2], 1),
            "p95_us": round(lat[int(len(lat) * .95)], 1),
            "p99_us": round(lat[int(len(lat) * .99)], 1),
            "mean_us": round(statistics.mean(lat), 1),
            "per_second_single_core": int(1_000_000 / statistics.mean(lat))}


def main() -> int:
    out = {"generated_at": datetime.now(timezone.utc).isoformat(),
           "hardware": hardware(), "ladder": [], "pure_policy": {}}

    print(f"hardware: {out['hardware']['cpus']} cpus, "
          f"python {out['hardware']['python']}\n")

    app = build(now=NOW, gateway=FakeGateway())
    out["pure_policy"] = decisions_only(app, 20000)
    p = out["pure_policy"]
    print(f"pure authorize()  p50 {p['p50_us']}us  p99 {p['p99_us']}us  "
          f"~{p['per_second_single_core']:,}/s on one core\n")

    print(f"{'n':>6} {'workers':>8} {'wall':>8} {'req/s':>8} "
          f"{'p50':>8} {'p95':>8} {'p99':>8}")
    for n, workers in ((1, 1), (10, 1), (100, 1), (100, 8), (500, 8),
                       (1000, 16)):
        reset()
        app = build(now=NOW, gateway=FakeGateway())
        row = journeys(app, n, workers)
        row["stages"] = percentiles()
        out["ladder"].append(row)
        print(f"{n:>6} {workers:>8} {row['wall_s']:>7.2f}s "
              f"{row['throughput_per_s']:>8.1f} {row['p50_ms']:>7.1f}ms "
              f"{row['p95_ms']:>7.1f}ms {row['p99_ms']:>7.1f}ms")

    dest = Path(__file__).resolve().parent / "results" / "scale.json"
    dest.write_text(json.dumps(out, indent=1))
    print(f"\nwrote {dest}")

    slowest = max(out["ladder"][-1]["stages"].items(),
                  key=lambda kv: kv[1]["p50"])
    print(f"slowest stage at the top of the ladder: {slowest[0]} "
          f"(p50 {slowest[1]['p50']}ms)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
