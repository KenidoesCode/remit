# REMIT — final audit before the submission build

*Written by reading the repository, running every suite, and driving the live
deployment. Every claim below is either a file:line or a command anyone can
re-run. Nothing here is aspirational.*

**Audited at:** `main`, 22 August 2026
**Live:** https://remit-vvug.onrender.com (Razorpay **test mode**, real orders)
**Suites run:** `pytest -q` (392 passed) · `eval/run_eval.py` · `eval/matrix.py`
(260/260) · `eval/attacks.py` (22/23) · `eval/arena.py` · `eval/frontier.py`

---

## A. What is already strong — and must not be rewritten

These are load-bearing. Changing them for tidiness would cost more than it buys.

**A1. `remit/policy/authorize.py` — the pure policy engine.**
21 clauses, no I/O, `now` is an argument. This is the reason replay works, the
reason the frontier sweep is meaningful, and the reason the Arena is a
controlled experiment rather than a tournament. Every clause id surfaces in the
decision, the ledger and the UI. **Do not touch the purity.**

**A2. Policy as data (`policy/authorize.yaml`), and `integrity_layer` as one
key.** "With REMIT" and "without REMIT" is a data change, not a code path. It
is what makes the counterfactual and the Arena honest — all seven agents run
the same 12,167 lines. **Do not turn any of this into a branch.**

**A3. The intent envelope.** Immutable, versioned, `semantic_hash` excludes ids
and timestamps so a resent sentence is one purchase. Amendment on approval
(v n+1 with a reason) is new and correct.

**A4. Drift renormalised over *evaluable* dimensions.** Unstated is
`not_evaluable`, never compliant — so a drift score of 0.00 always comes with
why it is 0.00. This is a genuinely good idea and it is rare.

**A5. Idempotency keyed on meaning.** `H(user:semantic_hash | cart_signature |
total | catalog_version)` with a UNIQUE constraint as the serialisation point.

**A6. `term_answers` as one shared predicate.** Search and drift ask one
question in one place (FAILURES #14). Section 6 of the brief asks for exactly
this; it already exists and is already tested.

**A7. FAILURES.md.** 31 entries, three of them about the author's own tests
being wrong. This is the single most credible artefact in the repository.

**A8. The evaluation discipline.** Fixed seed, deterministic split, held-out
scored once, gates reported separately from metrics, abstention as a first-class
outcome.

---

## B. Critical security issues

### B1 — ~~**P0 — Caller-supplied identity. The trust boundary is open.**~~ **CLOSED, Phase 1**

`remit/api.py:112,122` — `user_id: str = "usr_demo"` on `ShopRequest` and
`CompareRequest`; `api.py:174-175` passes `req.user_id` straight into exposure
and the journey; `api.py:541` takes it from a query string.

Nothing verifies it. Exposure, velocity, idempotency namespace and **approval
ownership** are all keyed on that string, so anyone who knows or guesses
another principal's id inherits their limits and can redeem against them.

This is not theoretical — REMIT's own attack lab demonstrates it and reports it
on the public page:

```
BROKE  [payment] Spend as somebody else
       an unauthenticated caller spent 497600 paise against
       usr_victim_alice's identity and limits.
```

Every other control in this system is downstream of identity. **This is the one
issue that can invalidate the entire trust boundary, and it is Phase 1.**

**Closed at `1be3743`.** `remit/auth.py` mints an opaque principal, signs it
with HMAC-SHA256 and puts it in an httpOnly cookie; the middleware resolves it
before the rate limiter; `ShopRequest` and `CompareRequest` have no identity
field at all. `tests/test_identity.py` covers the seven ownership cases, the
`identity_forgery` attack reports `held`, and the same three checks pass against
the live deployment: a body-supplied `user_id` is ignored, a cookie-less caller
reading another principal's checkout gets 404 where the owner gets 200, and a
cookie-less caller redeeming a valid approval gets `wrong_actor`. What this is
*not* is a login — see the module docstring, which says so at length.

### B2 — **P1 — No origin policy.**

There is no CORS middleware at all (`grep CORSMiddleware remit/` → nothing).
That is *accidentally* the safe default — a browser on another origin cannot
call the API — but it is undeclared, so nobody reading the code knows whether it
is a decision or an omission. It should be an explicit, environment-aware
allowlist that fails closed.

### B3 — **P1 — Rate limiting is per-process memory.**

`api.py:69-127`. It resets on deploy and does not exist across replicas. Fine
for a bored visitor, useless against anyone trying. Already documented in the
middleware comment; needs to stay documented rather than quietly implied.

### B4 — **P2 — No `Idempotency-Key` header support.**

Idempotency is derived internally (A5), which is stronger than a client-supplied
key for *this* flow, but an integrator expects the header. Section 9 of the
brief asks for it.

### B5 — **P2 — Secrets hygiene is good; rotation is owed.**

The webhook secret now fails closed (FAILURES, `assembly.py:_webhook_secret`).
Nothing is committed. But the Razorpay test key and a GitHub PAT were pasted
into a chat during development and **must be treated as compromised and
rotated** after the buildathon. That belongs in the README, not in my head.

---

## C. Correctness issues

### C1 — **P1 — `remit/gateway.py` is dead code.**

A whole parallel orchestrator that nothing imports except a type alias. A
reviewer reading top-down will find it and lose confidence in the map. Delete
or clearly mark.

### C2 — **P2 — The payment FSM stops at SUCCESS.**

`CREATED · AUTHORIZED · SUCCESS · FAILED · UNKNOWN`. No `CANCELLED`,
`REFUNDED`, `DISPUTED`. Section 7 of the brief asks for them. They can be
modelled and locally simulated honestly — but they must be **labelled as
simulated**, because REMIT has never processed a real refund.

### C3 — **P2 — `LLMCompiler` is untested against a live key.**

Every test, the whole 540-case evaluation and the deployment run
`RuleCompiler`. The LLM path has a strict schema and degrades toward *more*
friction, and has never run in anger. Report it as unmeasured; do not imply
otherwise.

### C4 — **P3 — The neural embedder is implemented and unmeasured.**

`remit/retrieval/embed.py:SentenceEmbedder` works when the package and model
are present. This build environment's egress blocks the model host, so the
comparison against the hashing embedder does not exist. Say so.

---

## D. Evaluation issues

### D1 — **P1 — Every corpus in this repository was written by the author.**

540-case eval corpus, 260-case matrix, 23 attacks — all mine. This is the
single largest threat to every number on the page, and the brief's section 14 is
right to demand a blind layer. What exists is **Layer A** only.

### D2 — **P2 — No mutation corpus.**

Nothing systematically mutates a legitimate request along one axis at a time
(amount, quantity, product, currency, merchant, timing, identity) and asserts
the invariant that axis threatens.

### D3 — **P2 — Metrics reported are narrower than section 15 asks for.**

Present: unauthorised movement, dangerous FN, precision, recall, abstention
accuracy, duplicate payments, webhook violations, p50/p95, revenue/AOV.
Missing: p99, grounding accuracy as its own number, approval-replay rate,
authentication-bypass rate (which is currently **100%** — see B1).

---

## E. UX issues

See `docs/UI_AUDIT.md` for the full pass. Summary:

- **P1** The Arena table shows nine columns including a full sentence of prose
  per row. It overflows and buries the finding.
- **P1** The background glow contains `rgba(255,86,40,.26)` — an orange — and
  `.g1` sits at `.62` opacity, which is why the hero reads as a red wash rather
  than as typography on a dark field.
- **P2** Eight rooms is more surface for a first-time visitor than five acts
  were. There is no "start here".
- **P2** No inspector that answers *"why did REMIT stop this?"* in one place.

---

## F. Production architecture gaps

| Gap | Status |
|---|---|
| Authentication | **absent** (B1) |
| Multi-tenancy | one `user_id` column; no tenant, no isolation tests beyond exposure |
| Database | one SQLite file, one process, one `RLock`. Correct; not concurrent |
| Migrations | `_migrate()` adds columns idempotently. Real enough for now, not a migration tool |
| Concurrency | serialisation is a UNIQUE constraint (right); no cross-process test |
| Observability | correlation ids everywhere, `/health`; no structured logs, no metrics endpoint |
| Availability | single free-tier instance, cold starts. **Documented, not hidden** |

**Nothing here should be faked.** The correct move is to build the boundary and
name the gap.

---

## G. Demo gaps

- ~~**P0** The step-up → approve → **replay rejected** → **cart-changed rejected**
  sequence exists in the engine and in tests, but a reviewer cannot walk it in
  the UI without knowing what to click.~~ **CLOSED, Phase 2.** Five presses in
  room 01 (`#walk`), each a real POST to `/api/shop`, each declaring its expected
  outcome before it fires: step-up · approve · replay (`already_used`) · tamper
  (`cart_changed`) · impersonate (`wrong_actor`). Backed by
  `tests/test_walkthrough.py`. FAILURES #34.
- **P1** No guided path. Eight rooms and no "press this first".
- **P2** The Break room requires the reviewer to pick an attack; it does not
  accept an arbitrary sentence to attack *with*.

---

## H. Integration gaps

- No versioned API surface (`/v1/...`), no OpenAPI document, no integration
  guide. FastAPI generates a schema at `/docs`; it is unnamespaced and
  undocumented.
- No `CatalogAdapter` seam — `Catalog` talks to SQLite directly, so the claim
  "REMIT sits between an agent and a merchant stack" is not yet visible in the
  code.

---

## I. Items that must NOT be changed

1. The purity of `authorize()`.
2. `integrity_layer` as a data key.
3. The envelope's `semantic_hash` exclusion list.
4. Drift's `not_evaluable` semantics.
5. The idempotency key composition.
6. `term_answers` as the single predicate.
7. FAILURES.md history — append only, never rewrite.
8. The held-out split boundary.
9. The corpus labels, except where a label is *demonstrably* wrong, and then
   only by making the case produce the outcome it claims to test (FAILURES #18).
10. The red/black/white palette and the two families.

---

## J. Implementation plan

Ordered by the brief's phases, filtered by what this repository actually needs.

| # | Work | Priority | Why |
|---|---|---|---|
| ✅ 1 | **Session principal, server-derived.** Signed httpOnly cookie; money endpoints ignore body identity. Seven ownership tests. Update the `identity_forgery` attack to hit HTTP and report `held`. | **P0** | B1. Everything else is downstream |
| ✅ 2 | **Approval flow walkable in the UI**, including replay and cart-mutation rejection as visible steps | **P0** | G1 — it is the hero demo and it is currently invisible |
| 3 | **"Why did REMIT stop this?" inspector** | P1 | E, and it is cheap |
| 4 | **Mutation corpus** (Layer C): one axis at a time, invariant per axis | P1 | D2 |
| 5 | **Arena information architecture**: headline metrics, compact rows, details on demand | P1 | E1 |
| 6 | **Background: remove the orange, drop the intensity behind type** | P1 | E2 |
| 7 | **Explicit CORS + `Idempotency-Key`** | P1 | B2, B4 |
| 8 | **Security invariants file**, machine-readable, one executable test each | P1 | Brief §34 |
| 9 | Payment FSM: `CANCELLED / REFUNDED / DISPUTED`, **labelled simulated** | P2 | C2 |
| 10 | `CatalogAdapter` seam | P2 | H |
| 11 | `/v1` namespace + OpenAPI + integration doc | P2 | H |
| 12 | Delete `remit/gateway.py` | P2 | C1 |
| 13 | p99 + per-stage latency | P2 | D3 |
| 14 | Blind corpus (Layer B) | P2 | D1 — honest note: a corpus I generate is not blind, and I will say so rather than claim it |

**Not doing, and why:** Postgres migration (deadline risk against no
demonstrated benefit at this scale — the boundary is documented instead);
microservices; blockchain; zk proofs. The brief forbids all four.

---

## The one-line verdict

The trust core is genuinely good and the evaluation discipline is better than
the code it evaluates. There was exactly **one P0**, it was authentication,
REMIT proved it against itself on its own public page, and until it was fixed
every other guarantee in this repository rested on a string in a request body.

**Both P0s are closed** (B1, Phase 1 · G1, Phase 2) and verified against the
live deployment rather than only in the suite. What is left is P1 and below,
and the largest of those is not a bug: every corpus in this repository was
written by its author, and no amount of engineering makes that go away — it
gets named, not solved (D1).
