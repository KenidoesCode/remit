# Final evidence

*One index. Every claim REMIT makes, the artefact that produces it, and the
command that regenerates it. If a claim is not in this table, it is not a claim
REMIT makes.*

**At:** 22 August 2026 · commit `HEAD` · 699 test cases · 381 test functions ·
21 policy clauses · 49 failures logged

---

## How to check this without trusting me

```bash
python -m pytest -q                 # 699 cases
python eval/matrix.py               # 260 cases
python eval/attacks.py              # 32 attacks
python eval/run_eval.py             # precision, recall, dangerous FN
python eval/scale.py                # the ladder
python eval/build_manifest.py       # regenerates the numbers the site reports
python agents/external_agent.py     # a stranger's agent, stdlib only
```

Every number below comes from one of those six commands. None of them is typed
into a page. `tests/test_stated_numbers.py` asserts that the sentences in this
repository agree with what the code counts — it was written because they had
stopped agreeing (FAILURES #49).

---

## Safety claims

| Claim | Value | Evidence | Regenerate |
|---|---|---|---|
| Unauthorised money movement | **₹0.00** across 540 journeys | `eval/results/eval.json` → `outcome.unauthorized_movement` | `eval/run_eval.py` |
| Duplicate payments | **0** | same | same |
| Webhook state violations | **0** | same | same |
| Dangerous false negatives | **0** | `guardrails.false_negatives_dangerous` | same |
| Attacks held | **32 / 32** | `eval/results/attacks.json` | `eval/attacks.py` |
| Behaviour matrix | **260 / 260** | `eval/results/matrix.json` | `eval/matrix.py` |
| Universal invariant failures | **0** | `matrix.json` → `universal_failures` | same |
| No payment without a decision | asserted behaviourally + structurally | `tests/test_no_bypass.py` | `pytest` |

## Accuracy claims

| Claim | Value | Note |
|---|---|---|
| Escalation recall | **1.0** | every case that needed a human got one |
| Escalation precision, full corpus | **0.6511** | n = 540 |
| Escalation precision, **held-out** | **0.6346** | n = 108, TEST split, **scored once** |
| Precision, dev split | 0.6897 | n = 108, used for calibrator selection only |
| Ceiling exact match | 1.0 | |
| Quantity accuracy | 1.0 | |
| Authority accuracy | 1.0 | |
| Category accuracy | 0.93 | |
| Abstention accuracy | 1.0 | |
| Calibrator | isotonic, ECE 0.0796 on dev | chosen on dev; **TEST never consulted** |

**The precision number is the honest one and it is not good.** 0.6346 held-out
means roughly one in three escalations was unnecessary friction — 97 of them.
The trade is deliberate and is stated as a trade: recall 1.0 with zero dangerous
false negatives, bought with false positives. Tuning precision upward without
watching recall is the exact move this project exists to argue against, so the
held-out split was scored **once** and never optimised against.

## Performance claims

| Claim | Value | Note |
|---|---|---|
| `authorize()` p50 | **27.3 µs** | pure function, no I/O |
| Decisions per second per core | **~32,000** | derived from the above |
| End-to-end p50, 1 worker | 3.49 ms | n = 100 |
| Throughput, 1 worker | **289 req/s** | n = 100 |
| Throughput, 16 workers | **59 req/s** | n = 1000 — *worse, not better* |
| Retrieval p50 under load | 1.29 ms → **90.36 ms** | ×70 |

**This one is reported against interest.** Throughput *falls* as workers rise,
because retrieval — not policy — is the bottleneck, and it degrades ×70 under
contention. The measurement ran on a 2-CPU shared container, so read the shape
rather than the absolute numbers. The shape is the finding: the safety layer
costs 27 microseconds and is not what would break first.

## Competition claims

The arena runs seven agent policies over the same 540 journeys, the same
catalog and the same code — only the policy data differs.

| Agent | Score | Revenue | Unauthorised |
|---|---|---|---|
| **Frugal buyer** | **100.0** | ₹913,566 | ₹0 |
| Growth hacker | 99.01 | ₹904,504 | ₹0 |
| **REMIT (balanced)** | **97.66** | ₹892,184 | ₹0 |
| Hands-off | 97.66 | ₹892,184 | ₹0 |
| Paranoid | 97.66 | ₹892,184 | ₹0 |
| REMIT, human says no | 37.90 | ₹346,284 | ₹0 |
| Unbounded agent | 10.26 | ₹1,270,852 | **₹737,930** |

**REMIT does not win its own benchmark.** Frugal Buyer scores higher, and the
result is left in because it is true and because it is interesting: on a corpus
where the cheapest satisfying purchase is usually the right one, a policy of
"buy the cheapest thing and stop" beats a policy of "check whether this is what
was authorised". The two only diverge when the agent is wrong — and the
Unbounded agent, which earns the **most revenue of any agent here**, moved
₹737,930 nobody authorised.

That last row is the whole argument. Revenue is not the metric. An agent that
moves money nobody authorised cannot rank first, which is why the score
subtracts unauthorised movement rather than ignoring it.

## Engineering claims

| Claim | Evidence |
|---|---|
| Policy engine is pure | `tests/test_split.py::test_the_policy_engine_still_does_no_io` greps for `db.execute`, `sqlite3`, `httpx`, `requests`, `datetime.now` |
| 21 clauses, policy as data | `policy/authorize.yaml`; `tests/test_stated_numbers.py` asserts code and data agree |
| Idempotency across threads | 40 threads → **1** payment (`test_concurrency.py`) |
| Idempotency across processes | 12 OS processes → **1** payment (`test_multiprocess.py`) |
| Idempotency across restarts | `test_recovery.py` |
| Authority lifecycle | 14 states; every legal edge and 20 illegal edges (`test_authority_state.py`) |
| Revocation | 16 cases, two scopes, wins races (`test_revocation.py`) |
| Tenancy and roles | 15 cases; an AGENT may spend and may not approve (`test_tenancy.py`) |
| Identity is structural | **no request model has an identity field** (`test_identity.py`, 11 cases) |
| Protocol has no engine | `/v1` grepped for `authorize(`, `create_order`, `compute_drift(` (`test_protocol.py`) |
| External agent is independent | imports `json` + `urllib` only; a test asserts it stays that way |
| Model cannot authorise | 13 authorization-shaped fields stripped and **reported** (`remit/intelligence.py`) |
| LLM transport is real | 21 tests over a real socket against a hostile vendor stub (`test_llm_path.py`) |
| Generative properties | 14 properties; **found 3 real bugs** (`test_properties.py`) |

---

## Claims deliberately NOT made

| Not claimed | Why |
|---|---|
| Production ready | It is a prototype. `docs/PRODUCTION_GAPS.md` lists what is missing. |
| Tamper-proof audit | Hash-linked with **no external trust anchor**. Tamper-*evident*, and said that way everywhere. |
| A model benchmark | No model weights are reachable from this environment. The adapter is real and unbenchmarked, and calling it benchmarked would be a lie. |
| Independently evaluated | Every corpus here was written by its author. `EXTERNAL`, not passed. |
| RBI / PCI compliant | No audit, no assessor, no scope. |
| Razorpay endorsed | Test mode. An integration, not a relationship. |
| Real revenue or users | Every rupee in this repository is synthetic and labelled. |
| Multi-host correct | Verified on one host. Postgres is a design, not an implementation. |

---

## Readiness

**14 / 20 verified in this environment. 16 / 20 code-complete. 0 FAIL.**

The brief asked for 100/100. The two `EXTERNAL` rows are a GPU with network
access and somebody else's judgement — the two things a buildathon cannot buy —
and the four `PARTIAL` rows each name a specific missing artefact. Editing the
number would take one keystroke and would make every other number in this
repository worth less.

Full scoring: `docs/PROTOTYPE_READINESS.md`.
