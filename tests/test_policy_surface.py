"""The policy file and the policy code must describe the same policy.

Every clause id in `authorize.py` appears in the decision, the ledger and the
UI. If it is not also in `policy/authorize.yaml`, then the document that claims
to BE the policy is missing a rule the system is enforcing -- which is worse
than an out-of-date comment, because the file is what a reviewer reads to find
out what REMIT does.

Two clauses (RESTRICT-001, RISK-002) were enforced for a week without being
declared, and the "policy clauses" figure on the home page was a hardcoded 17
while the engine ran 19.
"""
from __future__ import annotations

import re

import yaml

from remit.paths import ROOT


def test_every_enforced_clause_is_declared():
    code = set(re.findall(r'check\("([A-Z]+-\d+)"',
                          (ROOT / "remit/policy/authorize.py").read_text()))
    declared = set(yaml.safe_load((ROOT / "policy/authorize.yaml").read_text())
                   ["clauses"])
    assert code - declared == set(), f"enforced but undeclared: {sorted(code - declared)}"


def test_every_declared_clause_is_enforced():
    code = set(re.findall(r'check\("([A-Z]+-\d+)"',
                          (ROOT / "remit/policy/authorize.py").read_text()))
    declared = set(yaml.safe_load((ROOT / "policy/authorize.yaml").read_text())
                   ["clauses"])
    assert declared - code == set(), f"declared but never checked: {sorted(declared - code)}"
