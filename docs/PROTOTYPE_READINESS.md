# Prototype readiness

*Twenty criteria. Each one carries its implementation, its test, and what the
evidence actually shows. `PASS` requires evidence that exists. `PARTIAL` names
what remains. `EXTERNAL` means the code is complete and verification needs
something this environment cannot provide — and it is never counted as PASS.*

**At:** 23 August 2026 · 753 test cases · 260/260 matrix · 32/32 attacks ·
₹0 unauthorised · precision 0.6346 held-out · recall 1.0 · 0 dangerous FN

---

## Score

| | count |
|---|---|
| **PASS** | 14 |
| **PARTIAL** | 4 |
| **EXTERNAL** (code complete, verification requires something outside this environment) | 2 |
| **FAIL** | 0 |

**14/20 verified in this environment. 16/20 code-complete.**

Not 100/100, and the brief asked for it. The two `EXTERNAL` rows are a real
model's weights and an independent human evaluator; the four `PARTIAL` rows
each name a specific missing thing. Editing the number would take one keystroke
and would make every other number in this repository worth less.

---

## The criteria

### 1 · Architecture — **PASS**
Agent → interpretation → grounding → envelope → drift → risk → policy →
authorization → payment → audit. The decider is a pure function: no I/O, no
network, no clock, no text input.
**Evidence:** `remit/policy/authorize.py`; `test_split.py::test_the_policy_engine_still_does_no_io` greps for `db.execute`, `sqlite3`, `httpx`, `requests`, `datetime.now`.

### 2 · Authorization integrity — **PASS**
Immutable envelope, `semantic_hash` excluding ids and timestamps, amendment as
v n+1 with a reason, 21 clauses, policy-as-data.
**Evidence:** `test_intent.py`, `test_policy_surface.py`, `test_hard_authority.py`.

### 3 · Payment boundary — **PASS**
One entry point. Asserted behaviourally — drive the whole public surface, then
ask the database whether every payment has a decision behind it — and
structurally.
**Evidence:** `test_no_bypass.py::test_no_payment_path_bypasses_authorization`. Found two real bypasses when written (FAILURES #37, #38).

### 4 · Identity — **PARTIAL**
HMAC-signed httpOnly session principal. **No request model has an identity
field** — structural, not a rejected field.
**Evidence:** `test_identity.py` (11 cases), attack `identity_forgery`. FAILURES #32.
**Missing:** an identity provider. `principal_from_upstream()` is the seam,
deliberately unwritten — writing it without an IdP behind it would be theatre.
A signed session is a real identity boundary and is not SSO, so this does not
score as PASS.

### 5 · Roles — **PASS**
`human · agent · merchant · admin · system`, with capabilities asked rather
than inferred. **An agent may spend and may not approve** — an agent that can
approve the step-up it triggered has not been stopped by anything.
**Evidence:** `test_tenancy.py` (15 cases).

### 6 · Tenancy — **PASS**
Tenant on every money-path and evidence-path row, in the idempotency namespace,
signed into the session, and absent from every request model.
**Evidence:** `test_tenancy.py`. Found a **silent cross-tenant leak** on the first test written — FAILURES #48.

### 7 · Authority lifecycle — **PASS**
14 states, transition table, predicated write, driven by the real payment path.
`EXECUTING → REVOKED` legal; `EXECUTED → REVOKED` not.
**Evidence:** `test_authority_state.py` — every legal edge, 20 illegal edges, and a contended one.

### 8 · Revocation — **PASS**
Two scopes, persisted, actor-bound, idempotent, in the ledger, checked twice
per journey, and it wins races.
**Evidence:** `test_revocation.py` (16), attacks `revocation_race`, `revoke_someone_else`. FAILURES #43.

### 9 · Replay and idempotency — **PASS**
Keyed on meaning, UNIQUE-constrained, tenant-scoped, and **stable across
process restarts**.
**Evidence:** `test_concurrency.py` (40 threads → 1 payment), `test_multiprocess.py` (12 processes → 1 payment), `test_recovery.py`. FAILURES #45.

### 10 · Multi-process correctness — **PARTIAL**
Real OS processes, one SQLite file, no shared lock. WAL + `busy_timeout` +
`synchronous=FULL` + `BEGIN IMMEDIATE` on every read-then-write.
**Evidence:** `test_multiprocess.py` (10 cases). Found **three** defects invisible to threads, including a permanently forked audit chain — FAILURES #47.
**Missing:** Postgres. Everything above is verified on SQLite on ONE host;
multi-host correctness is a design in `docs/SCALE_ARCHITECTURE.md`, not an
implementation, so this does not score as PASS.

### 11 · Semantic authority — **PASS**
Head-noun vs modifier, category compatibility, negative constraints, semantic
retrieval that may find but never auto-authorise (`MATCH-001`, `MATCH-002`),
and evidence recorded per match.
**Evidence:** `test_grounding.py`, `test_negation.py`, `test_limit_vs_authority.py` — which computes the argument live rather than illustrating it.

### 12 · Abstention — **PASS**
Abstains rather than substituting, and says what the shop does stock.
**Evidence:** `test_grounding.py`, matrix `abstains` checks. FAILURES #13, #19, #42.

### 13 · Dangerous-error metric — **PASS**
Tracked separately and reported first. **0 dangerous false negatives**, recall
1.0, precision 0.6346 held-out, scored once, never tuned against.
**Evidence:** `eval/run_eval.py`, `eval/results/eval.json`.

### 14 · Attack surface — **PASS**
32 attacks run live against a throwaway instance, plus 260 matrix cases and 14
generative properties.
**Evidence:** `eval/attacks.py`, `eval/matrix.py`, `test_properties.py` — which found three real bugs.

### 15 · Protocol and API — **PASS**
Ten `/v1` routes, six nouns, **no engine of its own** — both surfaces return
identical verdicts, and `v1.py` is grepped for `authorize(`, `create_order`,
`compute_drift(`.
**Evidence:** `test_protocol.py` (18), `docs/REMIT_PROTOCOL.md`.

### 16 · External integration — **PASS**
`agents/external_agent.py` imports `json` and `urllib`. A test asserts it stays
that way.

**And now the stronger form of the same claim:** `remit-sdk@0.1.0` is published
on the public npm registry. It was installed **from registry.npmjs.org** into a
directory containing no REMIT source, and driven end to end — intent, decision,
execute, retry (same payment, `replayed: true`), receipt verified against
locally recomputed hashes, revoke, and a refused purchase afterwards. The
malicious-agent example blocked 9/9 from that same directory.
**Evidence:** `test_protocol.py::test_the_external_agent_imports_nothing_from_this_repository`; `npm view remit-sdk`; `docs/FINAL_0_TO_100_AUDIT.md`.

### 17 · Recovery and reconciliation — **PARTIAL**
Restart tested against a real file for payments, revocations, authority state,
approvals, webhooks and the chain. Timeout → `UNKNOWN`, owned by the
reconciler; unresolvable payments surfaced, never swallowed.
**Missing:** backups, PITR, a tested restore, a migration tool.

### 18 · Observability — **PARTIAL**
JSON line per decision keyed on the correlation id; per-stage p50/p95/p99 with
sample counts; payment latency kept separate from decision latency.
**Missing:** a collector, tracing, a metrics endpoint, alerting. A `/metrics`
route nobody scrapes is decoration — `docs/OBSERVABILITY.md` argues that
position rather than hiding behind it.

### 19 · Model integration — **EXTERNAL**
**Code complete.** OpenAI-compatible adapter (llama.cpp, Ollama, vLLM, LM
Studio, any hosted endpoint), pinned prompt and schema version, deterministic
`temperature=0`, timeout, and fail-closed on every path.
**Verified over a real socket** against a vendor stub that returns malformed
JSON, code fences, prose, a hallucinated product id, a verdict it awarded
itself, absurd values, HTTP 500 and a 5-second hang — 21 tests. The vendor is
stubbed; none of REMIT's code is.
**Why not PASS:** no model weights can be fetched in this environment
(huggingface, the GitHub API and every model CDN are tunnel-refused; only PyPI
is reachable) and the deployed instance is a 512 MB free tier. Running it takes
one env var and no code change. **Calling this a model benchmark would be a
lie, so it is not called one.**

### 20 · Independent evaluation — **EXTERNAL**
**Harness complete:** the `/v1` protocol, a public catalog, a documented
submission format and a scoring script that does not touch the system under
test.
**Why not PASS:** every corpus in this repository was written by its author.
That is the single largest threat to every number here, and **generating more
of them makes it worse, not better.** It needs a person who did not build this.
Named in five documents rather than solved.

---

## Reading this honestly

The two `EXTERNAL` rows are the two things a buildathon cannot buy: a GPU with
network access, and somebody else's judgement. Both have complete
implementations and neither is claimed as verified.

The four `PARTIAL` rows are infrastructure — backups, tracing, alerting,
Postgres — and each names the specific missing artefact rather than gesturing
at "production hardening".

**Zero FAIL rows.** Tenancy and production identity were `FAIL` in the previous
scorecard and are not any more, because they were built and tested rather than
rescored.
