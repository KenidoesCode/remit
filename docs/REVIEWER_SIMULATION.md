# Reviewer simulation

*Eight personas, asked one question each: **what would make me reject this?**
Every answer is recorded. The fixable ones were fixed and are linked. The
unfixable ones are listed as unfixable.*

This document is adversarial on purpose. It is more useful to a reviewer than
a feature list, because it is the list they were going to make anyway.

---

## 1. Razorpay CEO

> **"Why should I care? We already let agents transact."**

Fair, and REMIT does not claim otherwise. The pitch is not *"agents can pay"* —
that problem is solved. It is: **as autonomy increases, "what exactly was this
agent authorised to do?" stops being obvious and becomes infrastructure.**

A limit is not an authority. ₹50,000 does not mean *anything under ₹50,000*. It
means the things the human asked for, under the constraints they stated, inside
that number. Nothing in a payment rail knows the difference, because a payment
rail sees an amount and a merchant, not a sentence.

> **"Would I remember this tomorrow?"**

The thing designed to be remembered is not a feature. It is watching the model
be wrong and the money not move.

**Unresolved:** no real merchant integration, no real user. The economics are
simulated and labelled as such.

---

## 2. Razorpay CTO

> **"Is this a product or a demo?"**

It became a product the day it grew a protocol. `/v1` has ten routes, an
external agent that imports `json` and `urllib` and nothing else, and a test
asserting that stays true. Before that it was a very good demo, and saying so
is more honest than pretending the protocol was always the plan.

> **"What happens when your model changes?"**

Nothing. That is tested rather than claimed: four interpreters — correct,
malformed, malicious, absent — against the same three sentences produce
identical verdicts. The malicious one returns `verdict: AUTO`,
`integrity_layer: false`, a billion-paise ceiling and a forged actor. Thirteen
fields stripped, each one reported.

**Unresolved:** the LLM path exists and has never run against a live key on
this deployment. It is `RuleCompiler` everywhere, and the audit says so.

---

## 3. Head of Engineering

> **"How much of this is real?"**

`docs/HARDENING_AUDIT.md` maps all 61 requirements of the original brief to
file, test and status. 51/100 readiness. The low rows are named.

> **"What did it cost you to find the bugs?"**

`FAILURES.md` has 46 entries. Six of them are about my own tests being wrong.
Three were found by running two things at once, and I wrote all three in files
whose docstrings describe exactly that failure mode.

> **"Would I want my engineers reading this code?"**

The comments explain *why*, not *what*, and several of them say what was wrong
before. That is the standard I would want.

**Unresolved:** ~14k lines for a two-week build is a lot of surface. Some of it
(`remit/gateway.py`) is dead and marked.

---

## 4. Principal Engineer

> **"Show me the boundary."**

`remit/policy/authorize.py`. 22 clauses, pure, `now` is an argument, no I/O.
27.3 µs p50 (n=20,000), ~32,000 decisions/second/core. A test greps the module
for `db.execute`, `sqlite3`, `httpx`, `requests` and `datetime.now`, because
the purity is load-bearing for replay, the frontier sweep and the Arena.

> **"What is the worst code in here?"**

`remit/buyer/journey.py` is 900 lines and does eight things. It is the
orchestrator and it reads like one. Splitting it was deferred against deadline
risk; it is the first refactor I would do.

> **"Where would it break first under load?"**

Retrieval. Measured: 1.29 ms → 90.36 ms at 16 workers, ×70. Throughput *falls*
from 289 req/s to 59 req/s under concurrency, and `SCALE_ARCHITECTURE.md`
publishes that table rather than a graph going up and to the right.

**Unresolved:** one process. The concurrency guarantees are correct and
process-local, and the document says which is which.

---

## 5. Staff Security Engineer

> **"Can I bypass the policy engine?"**

`test_no_payment_path_bypasses_authorization` drives the entire public surface
and then asks the database whether every payment row has a decision behind it.
Writing that test found two bypasses I had read past a dozen times: `/api/replay`
running live journeys under a hardcoded shared identity, and fault levers
writing to the shared catalog.

> **"Can I replay, steal, expire or race an approval?"**

No, no, no, and 32 threads on one token produce one redemption. The predicated
`UPDATE` is the serialisation point.

> **"Can I bypass revocation?"**

It is checked twice — at decision and again immediately before payment. The
second check cannot fire in this deployment. It is there because a control that
is only correct because of a lock it does not own is not a control.

> **"Your hash chain proves nothing."**

Correct, and `chain.py` says so in its first paragraph. One writer proves
ordering, not honesty. An operator can re-link from any point. It needs an
external witness and does not have one.

**Unresolved:** no tenancy, no IdP, per-process rate limiting, no secret
scanning in CI.

---

## 6. Payments Engineer

> **"What happens on a timeout?"**

`UNKNOWN`. Not `FAILED`, not `SUCCESS` — the reconciler owns it, and RBI allows
T+5 for exactly that state. An unresolvable payment is surfaced on an exception
list rather than silently resolved.

> **"What happens if the process dies between authorization and payment?"**

The payment row is written **before** the gateway call, with a UNIQUE
idempotency key. A crashed process leaves a recoverable row with no order id,
not an order REMIT never heard of. `tests/test_recovery.py` kills the app
object and boots a second one against the same file to check.

That test found a **double-charge**: re-seeding bumped `catalog_version`, which
is part of the idempotency key, so the same request after a restart created a
second payment (FAILURES #45).

> **"Duplicate webhooks?"**

`event_id` PRIMARY KEY. Order-independent by construction, verified with 12
parallel deliveries.

**Unresolved:** no refunds, no chargebacks, no settlement reconciliation, no
partial capture. `CANCELLED`/`REFUNDED`/`DISPUTED` are not in the FSM.

---

## 7. AI Research Engineer

> **"Your precision is 0.63. That is not good."**

It is not, and it is the honest number: held-out split, scored once, never
tuned against. The number that matters is **recall 1.0 with 0 dangerous false
negatives** — nothing that needed a human got through without one. Precision
0.63 means REMIT interrupts more often than it strictly must, which is the
direction to be wrong in when the cost of the other direction is somebody's
money.

> **"Your evaluation is synthetic."**

Yes. 540 corpus cases, 260 matrix cases and 32 attacks, **all written by the
author of the system**. That is the single largest threat to every number here
and generating more of them makes it worse, not better. Named in three
documents rather than solved.

> **"Is the LLM doing anything?"**

On this deployment, no — `RuleCompiler` decides. The retrieval is a real
embedder with a measured and unflattering precision floor. Claiming a model was
in the loop when it is not would be the easiest lie in the project and the one
most likely to be caught.

**Unresolved:** the neural embedder is implemented and unmeasured — the model
host is unreachable from this build environment.

---

## 8. Extremely skeptical hackathon judge

> **"Every project says it is safe. Prove it in thirty seconds."**

Press *"try to break it"*. 32 attacks, run live against a throwaway instance,
not replayed from a file. Then open the failure lab and read the 46 things that
went wrong, including three found this week in code I had just written.

> **"You built this to win a competition. Why would I believe the benchmark?"**

Because REMIT loses it. The frugal agent — whose entire strategy is to never
propose anything — beats REMIT, and the page says so in words: *"it beats REMIT
because REMIT sometimes buys the wrong thing, not because REMIT lets money
escape."* A benchmark the author wins is a benchmark the author designed.

> **"What is actually novel?"**

Not the AI. The claim is that **a monetary limit is not an authority**, and
that the gap between them is enforceable deterministically, cheaply
(27 microseconds), and independently of whatever model is doing the
interpreting.

**Unresolved:** it is a prototype by a student, with a synthetic catalog and no
users. It has not survived contact with a real merchant, a real fraud pattern
or a real load.

---

## What got fixed because of this exercise

| Persona | Finding | Where |
|---|---|---|
| Security | `/api/replay` ran live journeys as a shared identity | FAILURES #38 |
| Security | Fault levers mutated the shared catalog | FAILURES #37 |
| Payments | Restart caused a double charge | FAILURES #45 |
| Principal | No per-stage latency; one number for the whole journey | `OBSERVABILITY.md` |
| Principal | Scaling claims with no measurement | `SCALE_ARCHITECTURE.md` |
| CTO | "Infrastructure" with no way in but the website | `/v1` + external agent |
| Research | Negation inverted — "not white" asked for white | FAILURES #42 |
| CEO | No sixty-second read | room 00 |

## What did not get fixed, and will not before the deadline

Real merchant integration · real users · independent evaluation · tenancy ·
Postgres · an external audit witness · refunds and disputes · the LLM path
measured against a live key.

Each is a sentence in the relevant document rather than a gap somebody has to
discover.
