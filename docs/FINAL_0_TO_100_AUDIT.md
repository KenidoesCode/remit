# Final 0 → 100 audit

*Every requirement, its real status, and the evidence. `PASS` means verified in
this environment. `PARTIAL` names the specific missing thing. `EXTERNAL` means
the code is complete and verification needs something this environment cannot
provide — and is never counted as a pass.*

**At:** 23 August 2026 · 828 tests · 32/32 attacks · 260/260 matrix ·
₹0 unauthorised · `remit-sdk@0.1.0` live on npm

Regenerate the numbers: `python verify.py`

---

## Score

| | count |
|---|---|
| **PASS** | 24 |
| **PARTIAL** | 6 |
| **EXTERNAL** | 2 |
| **BLOCKED** | 1 |
| **FAIL** | 0 |

**24/33 verified here. 26/33 code-complete.** Not 100, and the brief asked for
it. What the remaining nine are is stated below rather than averaged away.

---

## Product and protocol

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 1 | One-sentence definition, true in code | **PASS** | decider is a pure function over an envelope; `test_split.py` greps it for I/O |
| 2 | Core flow corresponds to real code | **PASS** | `/v1` calls `journey.run`; `test_protocol.py` asserts no second engine |
| 3 | Model independence | **PASS** | `test_model_independence.py`; 13 authorization-shaped fields stripped and reported |
| 4 | Semantic matching | **PASS** | `MATCH-001/002`, negation spans, `test_limit_vs_authority.py` computes the argument live |
| 5 | Authority envelope | **PASS** | immutable, versioned, `semantic_hash` excludes ids/timestamps |
| 6 | Policy engine deterministic, no I/O | **PASS** | 21 clauses as data; `now` is an argument |
| 7 | Revocation | **PASS** | two scopes, checked twice per journey, wins races (16 tests) |
| 8 | Authority state machine | **PASS** | 14 states, every legal edge + 20 illegal, one contended |
| 9 | Idempotency | **PASS** | 40 threads → 1 payment; 12 processes → 1 payment; survives restart |
| 10 | Concurrency | **PASS** | real threads and real processes, not a sequential loop |
| 11 | Multi-process | **PARTIAL** | verified on **one host**. Postgres is a design, not an implementation |
| 12 | Identity | **PARTIAL** | HMAC session, no request model has an identity field. **Not an IdP** |
| 13 | Tenancy | **PASS** | tenant in the idempotency namespace, not a filter (15 tests, FAILURES #48) |
| 14 | Payment boundary | **PASS** | `test_no_bypass.py` drives the whole surface, then asks the DB |
| 15 | Razorpay test mode | **PASS** | real orders, real webhooks, no real money |
| 16 | Ledger | **PASS** | hash-linked, atomic append (FAILURES #47) |
| 17 | Receipt verification | **PASS** | hashes recomputed **client-side**; a tamper test asserts it fails |
| 18 | Event schema | **PASS** | who/what/when/why per event; no credentials stored |

## Security

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 19 | Attack matrix | **PASS** | 32 attacks, 32 held, run 3× (FAILURES #50 is why more than once) |
| 20 | Generative testing | **PASS** | 14 properties; found 3 real bugs |
| 21 | Held-out evaluation | **PASS** | dev/test split; TEST scored **once** |
| 22 | Precision | **PASS** | 0.6346 held-out. Bad, reported, never tuned against |
| 23 | Recall | **PASS** | 1.0, 0 dangerous false negatives |
| 24 | Failure history | **PASS** | `FAILURES.md`, 55 entries, none removed |
| 25 | Observability | **PARTIAL** | per-stage p50/p95/p99 with sample counts. **No collector, tracing or alerting** |
| 26 | Recovery | **PARTIAL** | restart tested against a real file. **No backups, no PITR, no tested restore** |
| 27 | Reconciliation | **PASS** | `UNKNOWN` state owned by the reconciler; unresolvable payments surfaced |

## Developer surface

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 28 | Protocol `/v1` | **PASS** | 11 routes, version checked, index derived from the router (FAILURES #49) |
| 29 | External agent | **PASS** | stdlib only; a test asserts it imports nothing from this repo |
| 30 | SDK | **PASS** | TypeScript, ESM + CJS + types, **zero runtime dependencies**, 19 unit + 14 integration tests |
| 31 | npm package | **PASS** | **`remit-sdk@0.1.0` live on the public registry.** `npm view remit-sdk` |
| 32 | Clean install | **PASS** | installed **from registry.npmjs.org** into a directory with no REMIT source; full journey ran |
| 33 | CLI | **PASS** | `npm i -g remit-sdk` from the registry; doctor / intent / evaluate / execute / revoke / audit / receipt verify |
| 34 | Cross platform | **PARTIAL** | Linux verified. **Windows verified only by a real publish** that found two shell bugs (#53). **macOS: nothing has run** |
| 35 | API inventory | **PASS** | every route audited; no debug, replay or CLI bypass (FAILURES #37, #38) |

## Public surface

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 36 | Documentation matches code | **PASS** | `test_stated_numbers.py` reads the prose and asserts it |
| 37 | README | **PASS** | hero, real badges, verified links, 60-second flow, evidence, failures |
| 38 | Website ↔ repo consistency | **PASS** | one package name, one protocol version, one set of numbers |
| 39 | Decoration never crosses text | **PASS** | renderer skips protected zones; `test_background_collision.py` at 7 widths |
| 40 | Responsive | **PASS** | no horizontal overflow at 1440/1280/1024/768/430/390/375 |
| 41 | Open source hygiene | **PASS** | LICENSE, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, CHANGELOG, docs index |
| 42 | Launch materials | **PASS** | `LAUNCH.md`, every figure traceable to `verify.py` |
| 43 | Reproducibility | **PASS** | `python verify.py` regenerates the baseline and writes it |
| 44 | CI | **BLOCKED** | matrix committed at `.github/workflows/sdk.yml`. **Has never run** — the push token lacks `workflow` scope |
| 45 | Independent evaluation | **EXTERNAL** | every corpus is self-authored. Needs a person who did not build this |
| 46 | Real model benchmark | **EXTERNAL** | adapter complete and socket-tested against a hostile stub; **no weights reachable** from this environment |

---

## The nine that are not PASS

**Two `EXTERNAL`.** A GPU with network access, and somebody else's judgement.
Both have complete implementations; neither is claimed as verified. Calling the
LLM adapter a "model benchmark" would be a lie, so it is not called one.

**One `BLOCKED`.** The CI matrix that would have caught both Windows bugs and
the npm `bin` deletion exists, is committed, and has executed **zero times**,
because the token used to push cannot carry a workflow file. That is a human
action, not an engineering one — and until it runs, macOS support is an
expectation rather than a measurement.

**Six `PARTIAL`**, each naming its missing artefact: Postgres; an identity
provider; a metrics collector and tracing; backups and a tested restore; macOS;
and multi-host correctness.

---

## What this pass actually changed

| | |
|---|---|
| Found | `/v1` documented Bearer auth and never implemented it — headless clients silently got a new principal every call (**#51**) |
| Found | the receipt verifier reported tampering on a healthy chain (**#52**) |
| Found | two shell-portability bugs, on Windows, during a real publish (**#53**) |
| Found | npm **deleted the CLI entry** from the published manifest over a `./` prefix (**#54**) |
| Found | a test that was **keeping a stale number alive** by asserting it (**#55**) |
| Fixed | decoration crossing text — the renderer now yields, and a test proves it at 7 widths |
| Shipped | `remit-sdk@0.1.0` to npm, verified from the registry rather than from a local tarball |

Five real defects, four of them found by leaving the repository: publishing the
package, running it on someone else's operating system, and reading the site as
a stranger. None were found by re-reading the code.
