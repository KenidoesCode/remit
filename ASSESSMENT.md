# REMIT — what works, what doesn't, and what it would take to be real

**Written 21 August 2026.** Every figure here is read from a generated file in
this repository or from the running service. Where I am guessing, I say so.

The short version: **REMIT is a working prototype with a defensible thesis and
a real payment rail. It is not a platform, and nothing in it is ready for a
million users. The distance between those two things is roughly a year of work
by a team, and most of that work is not the part I have built.**

---

## 1. What this was supposed to be

The goal was a submission for the Razorpay AI Builder buildathon, Track 01 —
AI Growth & Agentic Commerce. The thesis:

> When an AI agent shops on your behalf, the interesting failure is not that it
> cannot pay. It is that the transaction it finally makes no longer matches what
> you authorised. Merchants want the agent to optimise revenue. That pressure and
> the human's intent pull in opposite directions, and nothing in the payments
> stack measures the gap.

So REMIT is two things pointed at each other: an AI buyer with a merchant
revenue engine trying to grow the basket, and a deterministic boundary that
decides whether the resulting transaction still represents the human.

Judged against *that* goal, the project is in good shape. Judged against "a
product Razorpay ships to millions on Monday", it is a prototype. Both
statements are true and they are not in tension — but only the second one is
what was asked here, so most of this document is about the gap.

---

## 2. What actually works

Verified today: **184 tests passing (128 functions), 9,146 lines of Python, 19
policy clauses, 186 products across 14 categories and 593 grounded phrases, 26
logged failures, 35 decision records.**

### The decision core — genuinely solid

| Piece | State | Evidence |
|---|---|---|
| Intent envelope | Immutable, versioned, `semantic_hash` excludes ids and timestamps | `remit/domain/intent.py`, used as the idempotency key |
| Policy engine | 18 clauses, **pure function**, no I/O, `now` is an argument | `remit/policy/authorize.py`; this is why replay and the frontier sweep are possible at all |
| Policy as data | `integrity_layer: true/false` flips the boundary without a code change | The "with / without" comparison is one YAML key, not a second build |
| Drift engine | 14 named dimensions, renormalised over *evaluable* ones | Unstated constraints are `not_evaluable`, never scored as zero drift |
| Risk engine | `E[loss] = (1−p) × amount × irreversibility` against `friction = max(floor, bps × total)` | Replaced a flat ₹15 that caused 296 needless escalations |
| Calibration | Isotonic (PAVA), chosen over temperature on a DEV split | Temperature made ECE *worse*; the loser is still in `calibration.json` |
| Abstention | A first-class return value, not an exception | `abstention_accuracy: 1.0` on the held-out split |
| Regulated goods | `RESTRICT-001` — alcohol and pharmacy can never be AUTO at any price | Not a risk trade-off a large budget can outvote |

### The payment rail — real, within test mode

- Real HTTPS calls to `api.razorpay.com/v1/orders`. Orders appear in the
  dashboard. Non-test keys are **refused at construction**.
- Razorpay Checkout opens on the order the policy engine allowed.
- `/api/payment/verify` recomputes the `order_id|payment_id` HMAC server-side,
  constant-time. A forged callback is written to the ledger and changes nothing.
  There is a test that asserts exactly that.
- Webhooks: HMAC-SHA256 verify, dedupe by event id, illegal transitions refused.
- Payment FSM with five states including **UNKNOWN**, because RBI's TAT circular
  allows T+5 for "debited, merchant confirmation not received". A system without
  that state either double-charges or refunds something that never settled.
- Idempotency keyed on `H(user:semantic_hash | cart_signature | total |
  catalog_version)`, with a UNIQUE constraint as the serialisation point.

### The evidence — better than most submissions will have

- 540 synthetic journeys, split train/dev/test (324/108/108), **test scored once**.
- Four arms bracketing REMIT, so the comparison cannot be rigged in either
  direction: plain checkout, unbounded agent, REMIT-approve, REMIT-decline.
- Held-out gates: **₹0.00 unauthorised movement, 0 duplicate payments, 0 webhook
  state violations, recall 1.0, 0 dangerous false negatives, p95 4.38 ms.**
- Headline: the unbounded agent earns **₹315,246 more** than plain checkout while
  moving **₹737,930.43 across 147 transactions nobody authorised**. REMIT moves
  **₹0.00**, and the price is ₹378,668 of forgone revenue -- an exchange rate of
  **₹1.95 prevented per ₹1 given up**. (This replaced a flattering "73.2% of the
  upside kept", which was partly earned by REMIT buying the wrong thing quietly.
  FAILURES #18.)
- `FAILURES.md` — 26 entries written when they happened, including two about my
  own tests being wrong (#18, #22) and one about a chart that proved nothing
  (#26).

### The honesty apparatus — the part I would actually defend

The system records what it could not evaluate. It logs a signature rejection it
refuses to act on. It publishes a precision number that is bad. It keeps the
calibration method that lost. That posture is the most transferable thing here,
and it is worth more in an interview than the code.

---

## 3. What does not work

**This is the section that matters.** I have ordered it by how fast it would
break with real users, not by how hard it is to fix.

### 3.1 It breaks immediately with more than one user

| Gap | What happens today |
|---|---|
| **No authentication whatsoever** | Every request is `usr_demo`. Anyone with the URL can spend against the same exposure caps. There is no login, no session, no token, no per-user isolation. |
| **No accounts or tenancy** | One policy file, one catalog, one merchant set, globally. Two merchants cannot have different limits because there is only one `authorize.yaml`. |
| **Single-writer SQLite behind one global `RLock`** | Every request serialises through one lock in one process. `WEB_CONCURRENCY=1`. This does not scale horizontally at all — a second instance would have a second, divergent database. |
| **Ephemeral storage** | `REMIT_DB=/tmp/remit.sqlite` on Render's free plan. The ledger, the idempotency table and the exposure caps reset on every redeploy. **Idempotency that forgets is not idempotency.** |
| **No migrations, no backups, no restore path** | The schema is created by `CREATE TABLE IF NOT EXISTS`. There is no way to evolve it against existing data. |

At ten concurrent users this is slow. At a thousand it is broken. At a million
it does not exist as a design.

### 3.2 The authority model is not a real payment authority

This is the most important gap and the least visible one.

REMIT's "authorisation" is **its own intent envelope** — a structure this
codebase invented, that this codebase checks. It is not a mandate any bank or
network recognises. Nothing outside this process is bound by it.

A real agentic-payments product needs an authority primitive the rail enforces:
UPI Reserve Pay / single-block-multiple-debit, an e-mandate under RBI's
recurring-payments framework, or a network tokenised credential with defined
limits. **UPI Reserve Pay is not available in Razorpay test mode**, which is
why it is not here, and I deliberately did not fake a mandate and call it real.

The consequence is honest but severe: today REMIT can *decide* correctly and
still be bypassed by anything that talks to the gateway directly. The boundary
is advisory, not enforced by the rail.

### 3.3 The intelligence is thin

- The default intent compiler is **regex keyword matching**. `CATEGORY_WORDS` is
  a hand-written dictionary. It handles "buy chips under 200" because I put
  "chips" in a list. It will not handle "something for my kid's lunchbox under
  ₹500", and there are millions of those.
- The LLM compiler exists behind `ANTHROPIC_API_KEY` but is **not measured** —
  the entire 540-case evaluation runs on the rule compiler. Its accuracy,
  latency, cost and failure modes are unknown.
- **Precision is 0.6346 on the held-out split** (up from 0.5238 once the
  grounder stopped substituting). Roughly one step-up in three is still
  unnecessary friction.
  In production that is a merchant revolt: every second autonomous purchase
  interrupts a customer for no reason. Recall 1.0 is the right trade for a
  demo and the wrong one for a business.
- Drift weights are **hand-tuned by me**. They were never fit to human
  judgements, because there are no human judgements — the labels are synthetic
  and I wrote the generator.
- The calibration is fit on synthetic outcomes. A calibrated probability derived
  from data I invented is a well-shaped number about nothing.

### 3.4 The evaluation proves less than it appears to

- Every input is synthetic and generated by the same author as the system.
  The corpus tests the failure modes I thought of.
- Evidence that this matters: **13 tests of mine passed while
  "buy a helicopter under 500000" returned a yoga mat**, because no test ever
  typed a word the catalog does not sell.
- **A regression I introduced today, stated plainly:** widening the catalog
  flattened the autonomy frontier. It used to break at "permissive" with
  ₹68,175 moving unasked. It now shows ₹0 unauthorised at *every* sampled point
  up to 47.2% autonomy, which means the sweep no longer reaches the failure
  point and the "how much autonomy is free" chart currently demonstrates
  nothing. The sweep range needs re-scoping. I have not done it.
- `no_decision_reached: 16` of 108 held-out cases. That is a fifth of the test
  split where the system produced no verdict at all.

### 3.5 There is no human in the human-in-the-loop

STEP_UP is the product's central move, and **there is no channel to actually ask
anyone anything.** No push notification, no SMS, no WhatsApp, no app, no email,
no deep link, no approval token, no expiry on the approval, no audit of who
approved. In the evaluation "the human confirms" is a boolean passed to a
function. In the UI it is a button on the same page.

A production step-up needs: a delivery channel with retries, a signed
approval link that cannot be replayed, a timeout policy for when nobody answers,
and a record of which human approved what. None of it exists.

### 3.6 The payment lifecycle stops at capture

No refunds. No partial captures. No cancellations. No disputes or chargebacks.
No settlement reconciliation against Razorpay's actual settlement reports. The
FSM's terminal states are SUCCESS and FAILED, and real money has more states
than that.

### 3.7 Security and compliance are essentially untouched

| Gap | Detail |
|---|---|
| Webhook secret has a **hardcoded default** | `remit/assembly.py` falls back to `"remit_test_webhook_secret"` if the env var is missing. On a misconfigured deploy, forged webhooks verify. This should refuse to start instead. |
| No rate limiting, no abuse controls | `/api/shop` runs the full engine and hits Razorpay. It is an open, unauthenticated, unmetered endpoint. |
| CORS is wide open on the engine build | `allow_origins=["*"]` was added for reviewer convenience. |
| Secrets live in environment variables only | No vault, no rotation, no per-tenant key isolation. My Razorpay test secret has been pasted into a chat log. |
| No PII handling, retention or residency policy | The ledger stores utterances verbatim and forever. |
| PCI scope never analysed | Card data never touches the server today (Checkout handles it) — but nobody has written that down, drawn the boundary, or had it reviewed. |
| RBI framework not addressed | Tokenisation, AFA, e-mandate rules, the recurring-payments circular. Not read against this design. |
| The ledger proves ordering, not honesty | Single writer, hash chain, no external anchoring. The operator can rewrite history and re-chain it. I have said this in `LIMITATIONS.md` from the start; it is still true. |

### 3.8 Operationally it is a toy

No metrics, no tracing, no alerting, no SLOs, no health dashboards beyond
`/health`. One free-tier instance that **sleeps after 15 minutes** and takes
about a minute to wake. No HA, no failover, no DR, no runbook. Never load
tested — I do not know what it does at 50 concurrent requests, and given the
global lock I can guess it is bad.

### 3.9 The catalog is fiction

186 products from fictional brands, seeded from a Python literal. There is no
product-feed ingestion, no real inventory, no real pricing, no merchant
onboarding. Every commerce number in the evaluation is arithmetic on invented
goods.

### 3.10 There is no integration surface

Razorpay cannot "use" this, because there is nothing to integrate with. No
merchant API keys. No published OpenAPI spec. No SDK in any language. No sandbox
environment. No merchant dashboard. No documentation for a third-party
developer. It is one FastAPI app with a demo UI attached — an argument, not a
service.

---

## 4. Goals versus reality

| Original goal | Status | Honest note |
|---|---|---|
| Demonstrate intent-to-transaction drift as a real problem | **Met** | The four-arm experiment is the strongest artefact here |
| Show revenue and safety are not opposed | **Partly met** | REMIT costs 6.64% of gross and removes ₹560,575 of unauthorised movement — a real trade, not a free lunch |
| Deterministic, replayable authorisation | **Met** | Pure function, ~250 µs re-decision, no model call |
| Real Razorpay integration | **Met, test mode** | Orders + Checkout + signature verify + webhooks |
| Honest evaluation with a held-out split | **Met, on synthetic data** | Scored once; the data is invented |
| Never move money the human did not authorise | **Met in the corpus** | ₹0.00 on the held-out split |
| A product Razorpay could operate | **Not met** | See section 3 in full |
| Usable by millions | **Not met** | Single process, single database, no accounts |

---

## 5. What it would take to be a real platform

Grouped by what blocks what. Effort estimates assume a small competent team, not
one student, and they are estimates.

### Phase 1 — become multi-user at all *(4–6 weeks)*
Nothing else matters until this is done.

1. **Postgres**, with real migrations (Alembic), connection pooling, and the
   global `RLock` replaced by transactional isolation and row-level locking on
   the idempotency and payment tables.
2. **Identity**: accounts, sessions, per-user exposure caps, per-user ledgers.
3. **Merchant tenancy**: policy per merchant, catalog per merchant, keys per
   merchant. `authorize.yaml` becomes a per-tenant row with a version history.
4. **Fail closed on misconfiguration**: refuse to start without a webhook secret,
   refuse `allow_origins=["*"]` outside development.
5. Rate limiting, request authentication, an HTTP `Idempotency-Key` header.

### Phase 2 — make the authority real *(6–10 weeks, partly blocked on Razorpay)*

6. Bind the intent envelope to a **rail-enforced mandate**: UPI Reserve Pay /
   SBMD, or an e-mandate, or a tokenised credential with limits. This requires
   production credentials and a conversation with Razorpay — it cannot be built
   against test mode.
7. **Approval channel**: signed, single-use, expiring approval links delivered
   over push/SMS/WhatsApp, with retries, a no-answer policy, and a record of the
   approving human.
8. **Full lifecycle**: refunds, partial capture, cancellation, disputes,
   chargebacks, and settlement reconciliation against Razorpay's reports.

### Phase 3 — make the intelligence worth trusting *(8–12 weeks, ongoing)*

9. Replace regex parsing with a **measured** LLM compiler: accuracy, latency,
   cost and failure modes benchmarked against the rule compiler, with the rule
   compiler retained as the fallback.
10. **Get real labels.** Have humans judge several thousand real journeys:
    should this have been auto-approved? Fit the drift weights and the
    calibrator to *those*, not to my generator.
11. **Attack precision.** 0.52 must become ~0.85 without losing recall. This is
    the single highest-value engineering problem in the project and it is
    entirely unsolved.
12. Re-scope the frontier sweep so it reaches the failure point again.
13. Adversarial evaluation by someone who did not write the system.

### Phase 4 — production hardening *(6–8 weeks)*

14. Observability: metrics, traces, alerts, SLOs, on-call runbook.
15. HA: multiple instances, health checks, graceful degradation, backups with a
    tested restore, DR plan.
16. Secrets: a vault, rotation, per-tenant isolation.
17. Load testing to a stated target, then fixing whatever it finds.
18. Data policy: PII classification, retention, residency, deletion on request.

### Phase 5 — become something others can build on *(6–8 weeks)*

19. Public OpenAPI spec, SDKs, a sandbox, and documentation.
20. Merchant dashboard: decisions, step-ups, blocked value, drift over time.
21. Real catalog ingestion from merchant product feeds.
22. Outbound webhooks so merchants can react to REMIT's verdicts.

### Phase 6 — the part that is not engineering *(runs alongside, months)*

23. PCI scope analysis and review.
24. RBI compliance review: tokenisation, AFA, e-mandate, the recurring-payments
    framework.
25. Legal: who is liable when the boundary is wrong in either direction? That
    question has no code in it and it decides whether the product can exist.

**Realistic total: nine to fifteen months with a team of four to six**, and
items 6, 23 and 24 need Razorpay to want it, not just permit it.

---

## 6. What I would actually say about this

If someone asks me to defend this project, the answer is not "it's ready".
The answer is:

> It is a prototype that takes one specific, under-served failure — the drift
> between what a human authorised and what an agent finally transacts — and
> shows, with a held-out evaluation and a real payment rail, that measuring it
> costs very little revenue and removes all of the unauthorised movement in the
> corpus. The decision core is production-shaped: pure, replayable,
> policy-as-data, calibrated. Everything around it is a demo: no accounts, no
> real mandate, one SQLite file, and a precision number I am not proud of.

That is a much stronger claim than pretending it is a platform, and it is the
only one the evidence supports.

The most useful thing in this repository is not the code. It is
`FAILURES.md`, and the fact that number thirteen was found by typing a
nonsense sentence into my own product an hour before writing this.
