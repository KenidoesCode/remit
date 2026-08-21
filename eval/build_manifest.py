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


def main() -> int:
    n = 0
    for f in sorted((ROOT / "tests").glob("test_*.py")):
        n += f.read_text(encoding="utf-8").count("\ndef test_")
    out = ROOT / "eval" / "results" / "build.json"
    out.write_text(json.dumps({
        "generated_by": "eval/build_manifest.py",
        "note": "counted by reading tests/test_*.py. shipped so a deployment that "
                "does not carry the test suite can still report a true number.",
        "tests": n, "policy_clauses": 17,
    }, indent=2) + "\n")
    print(f"{out}: {n} tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
