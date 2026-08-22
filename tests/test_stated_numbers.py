"""Numbers written in prose must equal numbers the code can count.

FAILURES #49. Three of them had drifted at once: the repository said "22
clauses" in seven places while the policy defined 21, the builder page said
FAILURES.md was 46 entries long while it was 47, and the shipped build manifest
carried a typed `"policy_clauses": 17` inside the very file whose docstring
says numbers are counted rather than typed.

None of them was load-bearing. That is exactly why they survived: nothing broke,
so nothing complained. A repository whose entire argument is that its numbers
are real cannot have a category of number that nobody checks -- so this file
makes prose a thing the test suite reads.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from remit import paths
from remit.assembly import build, utcnow
from remit.exec.razorpay import FakeGateway

ROOT = Path(__file__).resolve().parent.parent
SKIP = {".git", "node_modules", "__pycache__", ".pytest_cache", "venv", ".venv"}

# FAILURES.md is a log of what was true at a moment, and several entries quote
# the wrong number on purpose -- entry 49 exists *because* the repository said
# 22 clauses where there are 21, and it cannot record that without writing "22
# clauses". A historical statement is not a stale one.
#
# This is the only exemption, it is one file, and it is the one file where
# every sentence is explicitly dated. Dated snapshot headers elsewhere
# ("433 tests" atop docs/HARDENING_AUDIT.md) are counts, not clause claims, and
# this test does not read them.
HISTORICAL = {"FAILURES.md"}


def _prose_files() -> list[Path]:
    out = []
    for pat in ("*.md", "*.py", "*.html"):
        for f in ROOT.rglob(pat):
            if any(part in SKIP for part in f.parts):
                continue
            out.append(f)
    return out


@pytest.fixture(scope="module")
def real_clauses() -> int:
    a = build(now=utcnow(), gateway=FakeGateway())
    return len(a.policy.clauses)


def test_the_policy_defines_the_clauses_the_prose_claims(real_clauses):
    """Any sentence of the form "N clauses" must state the real N."""
    pattern = re.compile(r"(\d+)\s+(?:policy\s+)?clauses\b")
    wrong = []
    for f in _prose_files():
        if f.name == Path(__file__).name or f.name in HISTORICAL:
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            for m in pattern.finditer(line):
                if int(m.group(1)) != real_clauses:
                    wrong.append(f"{f.relative_to(ROOT)}:{i}: {line.strip()[:100]}")
    assert not wrong, (
        f"the policy defines {real_clauses} clauses, but these say otherwise:\n"
        + "\n".join(wrong))


def test_the_clause_ids_in_the_engine_are_the_clause_ids_in_the_data(real_clauses):
    """The engine may not check a clause the policy data has never heard of.

    A clause id that exists in code but not in the YAML is a rule with no
    published definition -- the reviewer reading policy/authorize.yaml would not
    know it can fire.
    """
    a = build(now=utcnow(), gateway=FakeGateway())
    src = (ROOT / "remit" / "policy" / "authorize.py").read_text(encoding="utf-8")
    in_code = set(re.findall(r'"([A-Z]+-\d{3})"', src))
    undocumented = in_code - set(a.policy.clauses)
    assert not undocumented, f"checked in code, absent from the policy data: {sorted(undocumented)}"


def test_the_shipped_build_manifest_agrees_with_the_repository(real_clauses):
    """eval/results/build.json is what a deployment reports when it has no
    tests/ directory. If it drifts, the site lies and nothing else notices."""
    d = json.loads(paths.BUILD.read_text(encoding="utf-8"))
    assert d["policy_clauses"] == real_clauses, (
        "build.json is stale -- run `python eval/build_manifest.py`")
    assert d["test_functions"] == paths.test_function_count(), (
        "build.json is stale -- run `python eval/build_manifest.py`")


def test_test_count_reports_cases_and_not_functions():
    """Parametrisation makes one function many cases. Reporting the function
    count under the label "tests" under-reported by 318."""
    assert paths.test_count() >= paths.test_function_count()


def test_no_prose_hardcodes_a_failure_count_that_has_gone_stale():
    """FAILURES.md only ever grows, so a typed count is wrong on a schedule."""
    import remit.api as api

    real = len(api.failures()["entries"])
    # Both phrasings this repository uses. "49 failures logged" in a doc
    # header is the same claim as "FAILURES.md is 49 entries" and drifts the
    # same way -- it did, in docs/FINAL_EVIDENCE.md, within the hour.
    pattern = re.compile(r"FAILURES\.md is (\d+) entries|(\d+) failures logged")
    wrong = []
    for f in _prose_files():
        if f.name == Path(__file__).name or f.name in HISTORICAL:
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            for m in pattern.finditer(line):
                claimed = int(m.group(1) or m.group(2))
                if claimed != real:
                    wrong.append(f"{f.relative_to(ROOT)}:{i}: {line.strip()[:90]}")
    assert not wrong, f"FAILURES.md has {real} entries; stale claims at {wrong}"


def test_the_builder_page_counts_rather_than_asserts():
    """The paragraph claiming 'I would rather a reviewer read them from me'
    must not itself contain a typed number."""
    import remit.api as api

    body = api.builder()
    real = len(api.failures()["entries"])
    assert str(real) in body["method"]
    assert body["this_build"]["clauses"] == len(
        build(now=utcnow(), gateway=FakeGateway()).policy.clauses)
