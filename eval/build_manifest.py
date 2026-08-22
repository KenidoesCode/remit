"""Write eval/results/build.json.

The hosted deployment does not carry tests/ (it is a runtime, not a checkout),
so /api/builder would report 0 tests there. Rather than typing a number into the
page, this script counts the real test functions and writes the count to a
generated file, exactly like every other number on the site.

    python eval/build_manifest.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _clauses() -> int:
    """Count the clauses in the policy DATA, the same source the running app
    reads. A hardcoded 17 sat here while the policy grew to 21 -- the file whose
    whole purpose is to stop numbers being typed had a typed number in it.
    FAILURES #49.
    """
    import yaml
    doc = yaml.safe_load((ROOT / "policy" / "authorize.yaml").read_text())
    return len(doc.get("clauses", {}))


def _collected() -> int | None:
    """Ask pytest how many test CASES exist.

    Counting `def test_` in the source counts test FUNCTIONS, and every
    parametrised function is one function and many cases. The two numbers are
    both true and they are not the same number: 372 functions, 690 cases. The
    site was publishing the smaller one under the plain label "tests", which
    under-reported by 318. FAILURES #49.

    Returns None when pytest is not importable -- this runs in a checkout, so
    that is a build problem, not something to paper over with a guess.
    """
    import subprocess
    try:
        r = subprocess.run(
            # -o addopts="" matters: pytest.ini already sets -q, and a second
            # -q is -qq, which prints per-file counts and no total.
            [sys.executable, "-m", "pytest", "--collect-only", "-q",
             "-o", "addopts="],
            cwd=ROOT, capture_output=True, text=True, timeout=900)
    except Exception:
        return None
    for line in reversed(r.stdout.strip().splitlines()):
        # "690 tests collected in 4.11s"
        parts = line.split()
        if len(parts) >= 2 and parts[1].startswith("test") and parts[0].isdigit():
            return int(parts[0])
    return None


def main() -> int:
    n = 0
    for f in sorted((ROOT / "tests").glob("test_*.py")):
        n += f.read_text(encoding="utf-8").count("\ndef test_")
    cases = _collected()
    out = ROOT / "eval" / "results" / "build.json"
    payload = {
        "generated_by": "eval/build_manifest.py",
        "note": "shipped so a deployment that does not carry the test suite can "
                "still report a true number. test_cases is what pytest collects; "
                "test_functions is what the source defines. Parametrisation is "
                "the difference, and neither number is the other.",
        "test_functions": n,
        "test_cases": cases,
        # kept for older readers of this file; it means test CASES.
        "tests": cases if cases is not None else n,
        "policy_clauses": _clauses(),
    }
    out.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"{out}: {n} functions, {cases} cases, {payload['policy_clauses']} clauses")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
