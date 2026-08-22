# Final scorecard

*PASS means there is evidence and it is named. PARTIAL means the gap is
specific and stated. FAIL means it is not built. Nothing is marked PASS because
it is nearly true.*

**At:** 22 August 2026 · 636 tests · 260/260 matrix · 32/32 attacks · ₹0
unauthorised

---

| Dimension | Verdict | Evidence, or what is missing |
|---|---|---|
| **Authorization integrity** | **PASS** | Immutable envelope, actor-bound, merchant-bound, currency-bound, time-bound, revocable. 21 clauses, pure, `now` as an argument. `test_no_bypass.py` drives the whole public surface and asks the database whether every payment has a decision behind it |
| **Payment safety** | **PASS** | One trusted entry point, asserted structurally and behaviourally. Idempotency on meaning, UNIQUE-constrained, survives a restart. 40 concurrent identical journeys → one payment |
| **Replay protection** | **PASS** | Approval single-use via predicated `UPDATE`; 32 concurrent redemptions → one. Webhook dedupe by PRIMARY KEY; 12 parallel → one applied |
| **Revocation** | **PASS** | Persisted, two scopes, actor-bound, idempotent, checked twice per journey, race-tested with real threads |
| **Authority lifecycle** | **PASS** | 14 states, transition table, predicated write, driven by the real payment path. Every legal edge and 20 illegal edges tested |
| **Aggregate authority** | **PARTIAL** | `SPLIT-001` catches decomposition under one stated ceiling; `EXPO-001/002` catch policy caps. **The corpus does not exercise it** — the eval passes `Exposure()` to keep cases independent, so `test_split.py` is the only evidence |
| **Concurrency** | **PARTIAL** | Genuinely concurrent tests, and three real bugs found by them. **Single process only** — a second process would not share the lock; the UNIQUE constraints would still hold and the exposure read would race |
| **Model independence** | **PASS** | Four interpreters — correct, malformed, malicious, absent — identical verdicts. 13 authorization-shaped fields stripped and reported. The one numeric field a model may return is a *claim*, never the ceiling |
| **Semantic correctness** | **PARTIAL** | Recall 1.0, **0 dangerous false negatives**, precision **0.6346** held-out, scored once. Grounded in the catalog, head-noun aware, negation-aware, abstains rather than guesses. Precision is the weakest number in the project and it is published |
| **Adversarial coverage** | **PARTIAL** | 32 live attacks, 260 matrix cases, 31 injection corpus cases, every historical bug regressed, generative property tests. **No blind corpus** — see below |
| **Auditability** | **PASS** | One database. Hash-chained ledger with the overclaim refused in its own docstring. Full clause trace per decision, immutable envelope versions, authority history. `test_audit_reconstruction.py` answers "why did this happen" from rows alone |
| **Protocol** | **PASS** | Ten routes, six nouns, no engine of its own — asserted two ways. An external agent that imports `json` and `urllib` walks the whole thing |
| **Failure safety** | **PASS** | Model absent → friction. Gateway timeout → `UNKNOWN`, owned by the reconciler. Secret unset → refuses to start. Unresolvable payment → surfaced, not swallowed |
| **Recoverability** | **PARTIAL** | Restart tested against a real file for payments, revocations, authority state, approvals, webhooks and the chain. **No backups, no PITR, no tested restore, no migration tool** |
| **Observability** | **PARTIAL** | JSON line per decision, per-stage p50/p95/p99 with sample counts, payment latency kept separate. **No collector, no tracing, no metrics endpoint, no alerting** |
| **Scalability** | **PARTIAL** | Measured rather than asserted: 289 req/s at one worker, **59 at sixteen**. Bottleneck identified (retrieval, ×70 under contention). The fix is named and not built |
| **Multi-tenancy** | **FAIL** | Not built. One `user_id` column, no tenant on any row |
| **Production identity** | **FAIL** | Session principal only. `principal_from_upstream()` is named in the audit and deliberately not written, because writing it without an IdP behind it would be theatre |
| **Independent evaluation** | **FAIL** | Every corpus was written by the author. The largest weakness in the project, named in four documents, and not fixable by volume |
| **Product value** | **PARTIAL** | The thesis is demonstrable in sixty seconds and defensible in five minutes. No merchant has integrated it and no real user has used it |
| **Razorpay relevance** | **PARTIAL** | Positioned as a complementary control layer, not a replacement rail. Researched positioning is stated as a proposal, not as a Razorpay roadmap claim |
| **Communication** | **PASS** | 46 failure entries, ADRs, threat model, protocol, scale measurement, a rejection analysis written before submission |
| **Memorability** | *not mine to score* | The intended moment is watching the model be wrong and the money not move |

---

## Counts

| | |
|---|---|
| PASS | 10 |
| PARTIAL | 9 |
| FAIL | 3 |

The three FAILs are tenancy, production identity and independent evaluation.
Two are infrastructure. The third cannot be fixed by working harder — it needs
somebody who is not me.

---

## The number I would defend hardest, and the one I would not

**Would defend:** `authorize()` at **27.3 µs p50** with no I/O. It is what makes
replay, the frontier sweep and the Arena possible, and it is why the boundary
cannot be prompt-injected — there is no text and nothing to persuade.

**Would not defend:** **precision 0.6346**. REMIT interrupts more often than it
strictly must. It is the honest held-out number, it is the direction I chose to
be wrong in given that recall is 1.0 and dangerous false negatives are 0, and
it is still the first thing I would work on next.
