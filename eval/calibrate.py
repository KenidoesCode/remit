"""Fit the confidence calibrator on the TRAIN split only.

Label: was the parse actually correct? (category AND ceiling AND quantity AND
authority all match the gold label). That is a real, checkable outcome, not a
model's opinion of itself.

Reports Expected Calibration Error and the reliability diagram before and after,
so the improvement is visible rather than asserted. The dev/test splits are not
touched here.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")

from remit.intent.shopping import RuleCompiler
from remit.risk.calibration import (IsotonicCalibrator, TemperatureCalibrator,
                                    expected_calibration_error, risk_coverage)

NOW = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)


def collect(split: str):
    comp = RuleCompiler()
    raws, correct = [], []
    for line in open("eval/corpus/cases.jsonl"):
        c = json.loads(line)
        if c["split"] != split:
            continue
        env, _ = comp.compile(c["utterance"], "usr_cal", NOW)
        if env is None:
            continue
        ceiling = env.max_total_paise or ((env.max_price_paise or 0) * env.quantity) or None
        ok = True
        if c["expect_category"]:
            ok &= env.category == c["expect_category"]
        if c["expect_ceiling_paise"]:
            ok &= ceiling == c["expect_ceiling_paise"]
        ok &= env.quantity == c["expect_quantity"]
        ok &= env.purchase_authority == c["expect_authority"]
        raws.append(env.parse_confidence)
        correct.append(bool(ok))
    return raws, correct


def reliability(ps, correct, bins=5):
    rows = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, p in enumerate(ps) if lo < p <= hi or (b == 0 and p == 0)]
        if not idx:
            continue
        rows.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": len(idx),
                     "mean_confidence": round(sum(ps[i] for i in idx) / len(idx), 3),
                     "actual_accuracy": round(sum(correct[i] for i in idx) / len(idx), 3)})
    return rows


if __name__ == "__main__":
    raws, correct = collect("train")
    draws, dcorrect = collect("dev")

    temp = TemperatureCalibrator.fit(raws, correct)
    iso = IsotonicCalibrator.fit(raws, correct)

    def ece(cal, xs, ys):
        return expected_calibration_error([cal(x) for x in xs], ys)

    base_train = expected_calibration_error(raws, correct)
    base_dev = expected_calibration_error(draws, dcorrect)
    rows = [
        ("uncalibrated", base_train, base_dev),
        ("temperature", ece(temp, raws, correct), ece(temp, draws, dcorrect)),
        ("isotonic", ece(iso, raws, correct), ece(iso, draws, dcorrect)),
    ]
    # Chosen on DEV, never on TEST.
    winner = min(rows, key=lambda r: r[2])
    chosen = {"uncalibrated": None, "temperature": temp, "isotonic": iso}[winner[0]]

    rc = risk_coverage([(chosen(r) if chosen else r) for r in draws], dcorrect)
    out = {
        "chosen": winner[0],
        "selection_rule": "lowest ECE on the DEV split; TEST never consulted",
        "candidates": [{"name": n, "ece_train": round(a, 4), "ece_dev": round(b, 4)}
                       for n, a, b in rows],
        "temperature": round(temp.t, 4),
        "isotonic": iso.to_dict(),
        "reliability_uncalibrated": reliability(raws, correct),
        "reliability_chosen": reliability(
            [(chosen(r) if chosen else r) for r in raws], correct),
        "risk_coverage_dev": [{"coverage": round(c, 3), "error": round(e, 4)}
                              for c, e in rc[::max(1, len(rc) // 20)]],
        "n_train": len(raws), "n_dev": len(draws),
    }
    with open("eval/results/calibration.json", "w") as fh:
        json.dump(out, fh, indent=2)

    print(f"{'method':14} {'ECE train':>10} {'ECE dev':>10}")
    for n, a, b in rows:
        mark = "  <- chosen" if n == winner[0] else ""
        print(f"{n:14} {a:10.4f} {b:10.4f}{mark}")
    print("\nreliability on train, before -> after")
    for a, b in zip(out["reliability_uncalibrated"], out["reliability_chosen"]):
        print(f"  {a['bin']}  n={a['n']:4d}  says {a['mean_confidence']:.3f} -> "
              f"{b['mean_confidence']:.3f}   actually {a['actual_accuracy']:.3f}")
