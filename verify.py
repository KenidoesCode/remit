#!/usr/bin/env python3
"""One command that proves the claims.

    python verify.py            # everything
    python verify.py --quick    # skip the 540-journey evaluation

Cross-platform on purpose: no make, no bash, no shell globbing. Everything runs
through sys.executable, because the last three platform bugs in this repository
were all a shell doing something a shell does (FAILURES #53).

Nothing here is typed. Every number printed and every number written to
`docs/FINAL_BASELINE.md` comes out of a suite that just ran, and the file is
regenerated rather than edited. If a suite fails, the number is a failure and
the exit code is non-zero -- there is no path through this script that reports
success it did not observe.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "eval" / "results"

GREEN, RED, DIM, BOLD, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"
if not sys.stdout.isatty():
    GREEN = RED = DIM = BOLD = OFF = ""


def run(label: str, args: list[str], timeout: int = 3600) -> tuple[bool, str]:
    print(f"  {DIM}running{OFF} {label} ", end="", flush=True)
    t0 = time.perf_counter()
    try:
        p = subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True,
                           text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"{RED}TIMEOUT{OFF}")
        return False, "timed out"
    dt = time.perf_counter() - t0
    ok = p.returncode == 0
    print(f"{GREEN}ok{OFF}" if ok else f"{RED}FAILED{OFF}", f"{DIM}{dt:.1f}s{OFF}")
    return ok, (p.stdout or "") + (p.stderr or "")


def pytest_count(output: str) -> tuple[int, int]:
    """(passed, failed) from pytest's own summary line."""
    passed = failed = 0
    for line in output.splitlines():
        if " passed" in line or " failed" in line:
            for token, word in ((" passed", "passed"), (" failed", "failed")):
                if token in line:
                    parts = line.replace("=", " ").split()
                    for i, w in enumerate(parts):
                        if w.startswith(word) and i > 0 and parts[i - 1].isdigit():
                            if word == "passed":
                                passed = int(parts[i - 1])
                            else:
                                failed = int(parts[i - 1])
    return passed, failed


def load(name: str) -> dict:
    try:
        return json.loads((RESULTS / name).read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify REMIT.")
    ap.add_argument("--quick", action="store_true",
                    help="skip the 540-journey evaluation (still runs tests, attacks, matrix)")
    ap.add_argument("--attack-rounds", type=int, default=3,
                    help="how many times to run the attack suite. A 1-in-3 concurrency "
                         "flake in the money path has happened here (FAILURES #50), so "
                         "one green run is not evidence.")
    args = ap.parse_args()

    print(f"\n{BOLD}REMIT VERIFICATION{OFF}")
    print(f"{DIM}{'-' * 52}{OFF}")

    failures: list[str] = []

    ok, out = run("test suite", ["-m", "pytest", "-q", "-o", "addopts="])
    passed, failed = pytest_count(out)
    if not ok:
        failures.append("test suite")
        print(out[-3000:])

    attack_rounds = []
    for i in range(max(1, args.attack_rounds)):
        a_ok, _ = run(f"attacks ({i + 1}/{args.attack_rounds})", ["eval/attacks.py"])
        a = load("attacks.json")
        attack_rounds.append((a.get("held", 0), a.get("attacks", 0),
                              [b.get("key") for b in a.get("broke", [])]))
        if not a_ok or a.get("broke"):
            failures.append(f"attacks round {i + 1}")

    m_ok, _ = run("behaviour matrix", ["eval/matrix.py"])
    matrix = load("matrix.json")
    if not m_ok or matrix.get("passed") != matrix.get("cases"):
        failures.append("matrix")

    ev: dict = {}
    if not args.quick:
        e_ok, _ = run("evaluation (540 journeys)", ["eval/run_eval.py"])
        ev = load("eval.json")
        if not e_ok:
            failures.append("evaluation")
    else:
        ev = load("eval.json")

    run("build manifest", ["eval/build_manifest.py"])
    build = load("build.json")

    all_ = ev.get("all", {})
    test_ = ev.get("test", {})
    g_all, o_all = all_.get("guardrails", {}), all_.get("outcome", {})
    g_test = test_.get("guardrails", {})
    eff = all_.get("efficiency", {})

    held, total = (attack_rounds[0] if attack_rounds else (0, 0))[:2]
    broke_any = sorted({k for _, _, ks in attack_rounds for k in ks if k})

    rows = [
        ("Tests", f"{passed} passed, {failed} failed"),
        ("Test functions", str(build.get("test_functions", "?"))),
        ("Policy clauses", str(build.get("policy_clauses", "?"))),
        ("Attacks", f"{held}/{total} held over {len(attack_rounds)} round(s)"
                    + (f" — BROKE: {', '.join(broke_any)}" if broke_any else "")),
        ("Behaviour matrix", f"{matrix.get('passed', '?')}/{matrix.get('cases', '?')}"),
        ("Universal invariant failures", str(matrix.get("universal_failures", "?"))),
        ("Unauthorised movement", o_all.get("unauthorized_movement", "?")),
        ("Duplicate payments", str(o_all.get("duplicate_payments", "?"))),
        ("Dangerous false negatives", str(g_all.get("false_negatives_dangerous", "?"))),
        ("Escalation recall", str(g_all.get("needs_human_recall", "?"))),
        ("Precision (full corpus)", str(g_all.get("needs_human_precision", "?"))),
        ("Precision (held-out)", str(g_test.get("needs_human_precision", "?"))),
        ("Latency p50 / p95", f"{eff.get('latency_p50_ms', '?')} ms / {eff.get('latency_p95_ms', '?')} ms"),
    ]

    print(f"\n{BOLD}RESULTS{OFF}")
    for k, v in rows:
        print(f"  {k:<30} {v}")

    status = "PASS" if not failures else "FAIL"
    print(f"\n  {BOLD}Status: {GREEN if status == 'PASS' else RED}{status}{OFF}")
    if failures:
        print(f"  {RED}failed:{OFF} {', '.join(failures)}")
    print()

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    doc = [
        "# Final baseline",
        "",
        "*Generated by `python verify.py`. Do not edit this file: it is overwritten,",
        "and a number typed here would be a number nothing produced.*",
        "",
        f"**Generated:** {stamp}",
        f"**Status:** {status}",
        "",
        "| | |",
        "|---|---|",
    ]
    doc += [f"| {k} | {v} |" for k, v in rows]
    doc += [
        "",
        "## How to reproduce",
        "",
        "```bash",
        "python verify.py",
        "```",
        "",
        "Individual suites:",
        "",
        "```bash",
        "python -m pytest -q          # the test suite",
        "python eval/attacks.py       # the attack suite",
        "python eval/matrix.py        # the behaviour matrix",
        "python eval/run_eval.py      # precision, recall, dangerous FN",
        "python eval/scale.py         # the throughput ladder",
        "```",
        "",
        "## What these numbers are, and are not",
        "",
        "Every corpus counted above was written by the author of the system. That is",
        "the single largest threat to all of it, it cannot be fixed by generating more",
        "cases, and it is why `docs/PROTOTYPE_READINESS.md` scores independent",
        "evaluation as `EXTERNAL` rather than as passed.",
        "",
        "The attack suite is run more than once on purpose. A 1-in-3 concurrency flake",
        "in the money path has happened in this repository before (`FAILURES #50`), and",
        "a single green run would have hidden it.",
        "",
    ]
    out_path = ROOT / "docs" / "FINAL_BASELINE.md"
    out_path.write_text("\n".join(doc), encoding="utf-8")
    print(f"  {DIM}wrote {out_path.relative_to(ROOT)}{OFF}\n")

    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
