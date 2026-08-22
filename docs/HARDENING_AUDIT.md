# REMIT — hardening audit against the control-plane brief

*Every row below was checked by reading the code or running it. `file:line`
means I looked. **ABSENT** means I looked and it is not there — it does not mean
I did not find it. Nothing in this document is aspirational, and the score at
the bottom is deliberately not flattering.*

**Audited at:** `e30dc40`, 22 August 2026 · 433 tests · 260/260 matrix ·
23/23 attacks · ₹0 unauthorised
**Live:** https://remit-vvug.onrender.com (Razorpay **test mode**, real orders)

---

## The one-paragraph verdict

The authorization core is strong and the surface around it was not. An audit
against the 61 sections found **five real defects, none of them in the policy
engine**: a fault lab that wrote to the shared catalog, a "pure" endpoint that
ran live journeys under a hardcoded shared identity, a hard currency clause
that no input could ever trigger while the parser silently read `$5,000` as
₹5,000, a ceiling that only ever bound one basket, and three separate claims
about concurrency that had never been executed concurrently. All five are
closed and each has a regression test. What remains is ranked below, and the
largest remaining gap is not a bug: **every corpus in this repository was
written by its author.**

---

## Scorecard (§41)

Not "production ready". A prototype, scored against what production would need.

| Dimension | Score | Why not higher |
|---|---|---|
| Authorization integrity | **8**/10 | Immutable envelope, actor-bound, merchant-bound, currency-bound, replay-proof, aggregate-aware. No revocation endpoint; no formal authority state machine |
| Payment safety | **8**/10 | One entry point, asserted by test; FSM with illegal-transition rejection; idempotency on meaning. No `CANCELLED`/`REFUNDED`/`DISPUTED` |
| Semantic correctness | **6**/10 | Grounded in the catalog, head-noun aware, abstains rather than guesses. Precision 0.6511. **Negative intent unsupported.** Ambiguity resolves to step-up rather than to evidence |
| Adversarial coverage | **7**/10 | 23 attacks, 260-case matrix, 31 injection corpus cases, all historical bugs regressed. No generative red team; property tests cover scalars only |
| Auditability | **7**/10 | Hash-chained ledger, per-decision clause trace, immutable version chain. Ledger is a **separate database** by default; parse telemetry is never persisted |
| Observability | **3**/10 | Correlation ids and `/health`. No structured logs, no metrics endpoint, no tracing |
| Scalability | **3**/10 | One process, one `RLock`, one SQLite file. Correct, not concurrent across replicas |
| Recoverability | **3**/10 | Reconciler for `UNKNOWN` payments. No backups, no migrations tool, no DR |
| Security posture | **6**/10 | Session principal, fail-closed secrets, admin-gated reset, rate limits. Rate limiting is per-process; no CORS declaration; no key rotation |

**Prototype readiness: 51/100.** The gap to production is
infrastructure — §22 lists it — not architecture.

---

## Section-by-section

### Closed by this pass

| § | Requirement | Was | Now |
|---|---|---|---|
| 7 | Replay protection | approval single-use, idempotency on meaning | + **contended**, 32 tabs → one redemption (`tests/test_concurrency.py`) |
| 8 | Concurrency | claims only; the "retry storm" was a `for` loop | real threads; 40 identical journeys → one payment; `/api/webhook` takes `LOCK` |
| 10 | Currency | `currency="INR"` hardcoded, `$5,000` → ₹5,000 | `amounts.detect_currency`; CUR-001 fires; no conversion |
| 11 | Merchant boundary | `approvals.merchants` written, never read | compared in `redeem()` (`grants/approval.py`) |
| 21 | One trusted entry point | untested; two bypasses present | `tests/test_no_bypass.py::test_no_payment_path_bypasses_authorization` |
| 53 | Split payment | ceiling bound one basket | `SPLIT-001`, soft, scoped, resend-aware |
| 54 | Cart manipulation | price/shipping/qty/delist caught | + those faults no longer write shared state (`remit/faults.py`) |

### Already true before this pass

| § | Requirement | Evidence |
|---|---|---|
| 3 | Authorization envelope | `domain/intent.py` — actor, merchant, products, constraints, amount, currency, time, policy version, expiry, hash |
| 4 | Immutable intent | `semantic_hash` excludes ids/timestamps; `amend()` writes v n+1 with a reason; `intent_versions` table |
| 5 | Authority separate from AI | `intent/compiler.py:5` — *the model may select, the model may not compute*; amounts come from a deterministic extractor |
| 12 | Actor binding | FAILURES #32 — signed httpOnly principal, no identity field in any request model |
| 13 | Catalog versioning | in the idempotency key and in every `decisions` row; `STOCK-001`, stale-pricing re-evaluation |
| 14 | Semantic authorization | `MATCH-001` (modifier-only match) and `MATCH-002` (embedding-only match) — both step up, never auto |
| 19 | Prompt injection | 23 lab attacks + 20 matrix cases + 31 corpus cases; the decider never reads the text |
| 24 | LLM resilience | fails toward friction: fallback compiler, confidence clamped to 0.5 |
| 31 | Cryptographic integrity | `ledger/chain.py`, with the overclaim explicitly refused in its own docstring |
| 37–38 | Benchmark honesty | Frugal buyer beats REMIT, on the page, in words |
| 51–52 | Velocity + aggregate | `VEL-001`, `EXPO-001/002`, per-actor and time-boxed (FAILURES #21) |

### Open, ranked

| Rank | § | Gap | Why it matters | Size |
|---|---|---|---|---|
| 1 | 15 | **Negative intent is not merely absent — it inverts.** `not`, `no`, `without` are stopwords in `intent/grounding.py:43-54`, so *"shoes but not white"* grounds *white* as a required term | A control plane that reads "not X" as "X" is worse than one that abstains | M |
| 2 | 50 | **No revocation.** `intents.revoked_at` exists, is never written, never read. `AUTH-003` reads a per-request boolean from `inject` | §60 asks "can I revoke it?" and today the answer is no | M |
| 3 | 6 | **No authority state machine.** Payment has one; authority is ad-hoc strings on a dataclass | Illegal transitions are unrepresentable rather than rejected | M |
| 4 | 30 | **Ledger is a separate database** when `REMIT_DB` is unset; parse telemetry never persisted | A `DENY` journey's blocking event lives in a different store from its decision row | S |
| 5 | 28 | **Property testing is 2 scalar properties.** No generated utterances, carts or catalogs | §28's invariant (`executed ≤ authorized`) is asserted by example, not by property | M |
| 6 | 48–49 | **No protocol document, no `/v1`, no OpenAPI** | Without it REMIT is an app, not a primitive | M |
| 7 | 16–17 | **Ambiguity resolves to step-up but never to evidence.** "buy my usual" has no stored-preference path | §17 asks the system to *show* what "usual" resolved to | M |
| 8 | 33 | **No executive mode** | §33's progressive disclosure does not exist | M |
| 9 | 29 | **No LLM/DB fault injection.** `LLMCompiler` is entirely untested | The fail-closed claim is unexercised code | S |
| 10 | 23 | **No `docs/SCALE_ARCHITECTURE.md`** | §23 | S |
| 11 | 55 | **No recurring authority** | §55 | M |
| 12 | 27 | **No generative red team** | §27 — the 23 attacks are hand-written and finite | L |
| 13 | 42 | **Razorpay positioning not researched against current products** | §42 explicitly asks for this before finalising the pitch | S |

### Deliberately not doing

| § | Item | Why |
|---|---|---|
| 22 | Postgres / Redis / event bus / HA | Deadline risk against no demonstrated benefit at this scale. The boundary is **documented** in `FINAL_AUDIT.md` §F, not faked |
| — | Blockchain, zk proofs, microservices, Kubernetes | The brief forbids all four, and none solves a demonstrated problem here |
| 26 | 1,000 semantic + 500 adversarial cases | 540 + 260 + 23 exist. Generating 1,260 more cases **from the same author** adds volume, not independence. §D1 of `FINAL_AUDIT.md` names that limit rather than papering over it with a bigger number |

---

## The corpus problem, stated plainly

Every case in `eval/corpus/cases.jsonl` (540), `eval/matrix.py` (260) and
`remit/lab/attacks.py` (23) was written by the same person who wrote the system.
This is the single largest threat to every number on the page, and no amount of
engineering removes it. Generating more of them makes it worse, not better,
because volume from one author reads as coverage without being coverage.

What would fix it: cases written by somebody who did not build this, or drawn
from a real transcript distribution. Neither exists here. It is named, in three
documents, rather than solved.

---

## What §60 asks, and where the product answers it

| The CEO's question | Answered where | Honest state |
|---|---|---|
| What can it spend? | envelope ceiling, `CEIL-001/002`, policy caps | ✅ on screen |
| Why can it spend it? | clause grid, every id, every detail | ✅ |
| Who authorized it? | signed session principal; approval bound to it | ✅ |
| What did the human mean? | envelope: terms, items, ungrounded words | ✅ |
| What does the AI think they meant? | drift across 14 named dimensions | ✅ |
| How certain is that? | `parse_confidence`, calibrated, shown | ✅ |
| What product did it choose, and why? | `why_selected`, ranked candidates | ✅ |
| What changed? | drift, `cart_changed`, stale pricing | ✅ |
| What policy allowed it? | `policy_version` + clause trace per decision | ✅ |
| Can I revoke it? | — | ❌ **rank 2 above** |
| Can it replay? | approval single-use; idempotency on meaning | ✅ contended |
| Can it exceed the limit through many small actions? | `SPLIT-001`, `EXPO-001/002` | ✅ new |
| Can another agent steal the authorization? | `wrong_actor`, walkable on the page | ✅ |
| Can prompt injection change it? | the decider never reads the text | ✅ |
| Can a merchant manipulate it? | offers priced against a running total; poisoned-name attack | ✅ |
| Can the LLM bypass it? | it may select, it may not compute | ✅ |
| Can I prove what happened? | hash-chained ledger + decision trace | ⚠️ two stores — **rank 4** |
