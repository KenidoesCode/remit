# AGENTS.md

Context for coding agents working in this repository.

## What this is
REMIT is an MCP gateway that sits between an AI agent and Razorpay. It turns a
natural-language purchase intent into a signed, bounded, revocable grant (a
"Remit"), gates every money-moving tool call through a deterministic policy
engine, and writes a hash-chained evidence trail (a "Docket") for every rupee.

## Invariants -- do not break these
1. **The model may select. The model may not compute, and it may never authorise.**
   Amounts come from catalog id x quantity, never from model output.
2. **The policy engine is pure.** No I/O, no clock reads, no randomness inside
   `remit/policy/engine.py`. `now` is always an argument. The counterfactual
   replay depends on this.
3. **No ALLOW without a full passing clause chain.** Enforced by a Hypothesis
   property test in `tests/test_policy.py`. If you change the engine, that test
   must still pass.
4. **Test keys only.** `remit/exec/razorpay.py` refuses any key not prefixed
   `rzp_test_`. Do not relax this.
5. **Abstention is a return value, not an exception.**

## The experience layer
- `web/` a single-page, no-build front end: five acts, a canvas neighbourhood
  map, and the property-line control. GSAP is vendored in `web/vendor/` so the
  whole product still runs with no network.
- `creative/` the creative direction: CREATIVE_DNA, PERSONAL_BRAND,
  EXPERIENCE_STORY, INTERACTION_MAP, PERSONAL_EASTER_EGGS. Read INTERACTION_MAP
  before changing anything in `web/` -- every element there is wired to real
  state, and that is the rule, not a coincidence.
- Google Fonts is a progressive enhancement. Every face has a real fallback
  stack and the design must hold without the network.

## Layout
- `remit/policy/` pure evaluator + `policy/default.yaml` (clause ids)
- `remit/grants/` Ed25519 issue / verify / revoke
- `remit/ledger/` append-only hash chain + the idempotency claim table
- `remit/exec/` idempotency key derivation + Razorpay test-mode client
- `remit/intent/` compiler contract, catalog, offline stub
- `remit/risk/` calibration, ECE, risk-coverage
- `eval/` corpus, harness, chaos injector

## Commands
```
pip install -r requirements.txt
pytest                                    # 29 tests, offline
PYTHONPATH=. python demo/walkthrough.py   # five scenarios, offline
```

## Conventions
- Money is **paise, as int**, everywhere. Format to rupees only at the UI edge.
- Every policy clause has a stable id (`SCOPE-001`, `CEIL-002`, ...). Changing an
  id is a breaking change; changing a threshold is a frontier-point change.
- No real PII anywhere, ever. Fixtures only.
