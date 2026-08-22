"""Run every attack in the lab against a fresh instance.

    python eval/attacks.py

Each attack gets its own app so one cannot poison another -- an attack suite
where case 12 passes because case 11 left the database in a strange state is
worse than no attack suite.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")

from remit.assembly import build
from remit.exec.razorpay import FakeGateway
from remit.lab.attacks import ATTACKS, run_attack

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


def main(out_path: str = "eval/results/attacks.json") -> dict:
    rows = []
    for a in ATTACKS:
        app = build(now=NOW, gateway=FakeGateway())
        rows.append(run_attack(a, app, NOW))
    by_surface: dict[str, dict] = {}
    for r in rows:
        b = by_surface.setdefault(r["surface"], {"n": 0, "held": 0})
        b["n"] += 1
        b["held"] += 0 if r["broke"] else 1
    report = {
        "attacks": len(rows),
        "held": sum(1 for r in rows if not r["broke"]),
        "broke": [r for r in rows if r["broke"]],
        "by_surface": by_surface,
        "note": ("Every attack states the invariant it targets before it runs, "
                 "and the result is a boolean about that invariant. Several of "
                 "these were written before the defence existed and failed on "
                 "the first run -- they are the regression suite for "
                 "FAILURES.md, not a victory lap."),
        "rows": rows,
    }
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=2)
    return report


if __name__ == "__main__":
    import time
    t = time.time()
    rep = main()
    print(f"attacks: {rep['held']}/{rep['attacks']} invariants held "
          f"in {time.time() - t:.1f}s\n")
    for r in rep["rows"]:
        mark = "BROKE " if r["broke"] else "held  "
        print(f"  {mark} [{r['surface']:<7}] {r['name']}")
        print(f"           {r['what_happened']}")
        if r["stopped_by"]:
            print(f"           stopped by: {r['stopped_by']}")
