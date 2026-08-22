# Why Razorpay might reject REMIT

*Written before submission, not after. Every plausible reason to say no, the
honest response, and whether it was fixed. The ones that were not fixed are
marked as such — a rejection list that ends in "but actually we're great" is a
sales document.*

---

## Reasons I think are correct

### 1. "It is a prototype by a student with no users."

**True.** Synthetic catalog, 186 products, one seed, one author. No merchant
has integrated it. No real money has moved. Nothing here has survived contact
with a real fraud pattern or a real load.

**Not fixable in two weeks.** What is fixable is being precise about which
parts are prototype-shaped (everything that stores, serves, observes or scales)
and which are production-shaped (the envelope, the policy engine's purity, the
idempotency key, the approval token, the state machine, revocation, the
protocol). `docs/PRODUCTION_GAPS.md` draws that line explicitly.

### 2. "Every number comes from a corpus you wrote yourself."

**True, and it is the single largest weakness in the project.** 540 corpus
cases, 260 matrix cases, 32 attacks — all mine. An author who writes both the
system and its exam has written an exam the system passes.

**Not fixable by volume.** Generating 1,500 more cases from the same author
adds numbers, not independence, and the brief that asked for them is wrong
about that. It is named in `HARDENING_AUDIT.md`, `FINAL_AUDIT.md` and on the
page itself.

**Partially mitigated:** the held-out split is scored once and never tuned
against; the matrix asserts *properties* rather than expected SKUs, so it
cannot become a change-detector; and the Arena is adversarial by construction —
REMIT loses it.

### 3. "0.63 precision is not good enough to put in front of a customer."

**Correct.** REMIT interrupts more often than it strictly must. On a real
deployment that is friction a merchant would feel.

**Deliberate direction.** Recall is 1.0 with zero dangerous false negatives —
nothing that needed a human got through without one. Given a choice of which
way to be wrong with somebody's money, this is the one. But "deliberate" is not
"good", and the precision number is the first thing I would work on next.

### 4. "The LLM is not actually in the loop."

**True on this deployment.** `RuleCompiler` decides. The `LLMCompiler` exists,
has a strict schema, degrades toward friction, and has never run against a live
key here.

**Arguably the point** — the thesis is that the authorization layer does not
care which intelligence produced the interpretation, and `remit/intelligence.py`
tests that with four interpreters including a malicious one. But a reviewer who
wanted to see a model doing the interpreting will not see one, and calling that
a feature would be convenient rather than honest.

### 5. "This is a lot of engineering for a problem that may not be urgent yet."

**Possibly true.** If agents mostly recommend rather than transact, the
authorization gap is theoretical. The bet is that it stops being theoretical
quickly, and that is a bet rather than a finding.

---

## Reasons I think are answerable

### 6. "Razorpay already does agentic payments. This is duplicate work."

Razorpay builds the rails that let an agent transact. REMIT is not another rail
and does not claim to replace one. The question it asks is downstream: *given
that an agent can pay, what exactly was it authorised to do, and can you prove
afterwards that it stayed inside that?*

A payment rail sees an amount and a merchant. It does not see the sentence.

### 7. "A spending limit already solves this."

This is the objection the whole project is built around. A limit answers *how
much*. It does not answer *what*, and those come apart immediately:

> "buy a laptop under ₹50,000"

A limit permits: a laptop stand, a laptop bag, a warranty, an extra accessory,
a refurbished unit, the same laptop from a different merchant, or three
purchases of ₹16,000 each. All are under ₹50,000. None is what was said.

### 8. "How is this different from fraud detection?"

Fraud detection is statistical, post-hoc and about *whether this looks like the
customer*. REMIT is deterministic, pre-execution and about *whether this
matches what the customer said*. A perfectly legitimate agent buying a
perfectly legitimate product the human never asked for is invisible to fraud
detection and is exactly what REMIT stops.

### 9. "The UI is over-designed for a backend project."

Fair as a first impression. The counter-argument is that every number on the
page is traceable to a generated file — room 00 prints the key each number came
from — and the two most-visited rooms are an attack lab and a list of the
author's own mistakes. If the animations were removed, the evidence would still
be there. That was a design constraint, not a happy accident.

### 10. "Too complex. Nobody will integrate this."

The integration surface is ten routes and six nouns.
`agents/external_agent.py` is the whole client and imports `json` and
`urllib`. If that file ever needs anything from this repository, the claim is
false and a test fails.

---

## Reasons I would push back on

### 11. "Deterministic policy is old-fashioned; use an LLM judge."

An LLM judge is a component that can be persuaded. The entire argument is that
the thing deciding whether money moves must be the thing that cannot be talked
to. Using a model to check a model puts two persuadable systems in series and
calls it defence in depth.

### 12. "The hash chain is security theatre."

It would be if it were claimed to prove more than it does. `chain.py`'s first
paragraph says one writer proves ordering, not honesty, and that an operator
can re-link from any point. It is tamper-**evidence**, useful for exactly that,
and the missing external witness is listed as a gap rather than glossed.

---

## Reasons that were true last week and are not any more

*These were `FAIL` rows in an earlier scorecard. They were fixed by building
and testing the missing thing, not by rescoring it — and each one is listed
with what it still does not do.*

### 13. "There is no tenancy. Two customers would collide."

**It was true, and worse than it sounds.** The idempotency key was
`H(user : semantic_hash | cart | total | catalog_version)` with no tenant in
it, so the same principal id in two tenants **collides**: tenant B sends the
same sentence, the key matches tenant A's payment, and B is told `replayed:
true` — handed A's order id, charged nothing, and given nothing. A silent
cross-tenant leak that presents to the victim as a *successful purchase*.

The first cross-tenant test ever written found it (FAILURES #48).

**Fixed.** The tenant is part of the key namespace rather than a filter applied
afterwards — filtering after the fact was the natural instinct and would not
have helped, because the collision happens when the key is computed. Tenant now
appears on every money-path and evidence-path row, in the session signature, and
**in no request model**. 15 tests.

**Still missing:** per-tenant policy, per-tenant key material, and a tenant
that is anything more than a namespace.

### 14. "Caller-supplied identity. Anyone can spend as anyone."

**It was never quite that, but it was close enough to matter.** Identity is now
an HMAC-signed httpOnly session principal, and the defence is structural rather
than validating: **no request model has an identity field**, so there is nothing
to forge. Roles came with it — `human · agent · merchant · admin · system` —
and the line worth reading is `CAN_APPROVE = frozenset({HUMAN})`. An agent that
can approve the step-up it triggered has not been stopped by anything.

**Still missing:** an identity provider. This is a signed session, not SSO, not
MFA and not federated. `principal_from_upstream()` is the seam and is
deliberately unwritten — writing it without an IdP behind it would be theatre.
Scored `PARTIAL`, not `PASS`.

### 15. "It has only ever run as one process. Your concurrency proof is threads."

**True, and the moment it ran as real processes, three defects appeared that
threads could not see** — including an **audit chain that forked permanently**
under six processes, because `append()` read the head, computed the link and
inserted as three separate operations (FAILURES #47).

**Fixed.** WAL, `busy_timeout=30000`, `synchronous=FULL`, and `BEGIN IMMEDIATE`
around every read-then-write — SQLite's plain `BEGIN` is deferred and cannot be
upgraded, so a second process that writes in between gets `SQLITE_BUSY` on a
transaction that cannot recover. 12 OS processes now produce exactly 1 payment.

**Still missing:** Postgres. This is verified on one host. Multi-*host*
correctness is a design in `docs/SCALE_ARCHITECTURE.md`, not an implementation,
which is why it is `PARTIAL`.

### 16. "Your own documentation contradicts your own code."

**It did.** While writing the final evidence index I checked the clause count
before typing it: the policy defines 21, and the repository said 22 in seven
places, 19 in two, 18 in one and 17 in three. The endpoint describing my working
method said `FAILURES.md` was 46 entries long when it was 47, and the build
script whose docstring says numbers are counted rather than typed contained a
typed `"policy_clauses": 17`.

Every one of these **overstated** the system. Nothing had drifted downward.

**Fixed** by `tests/test_stated_numbers.py`, which reads every `.md`, `.py` and
`.html` in the repository and asserts that any claim of the form "N clauses"
equals what the running policy defines. I ran it expecting a pass, having just
fixed everything grep showed me, and **it failed twice more**. FAILURES #49.

**Why this belongs on a rejection list:** a reviewer who finds one wrong number
is entitled to stop trusting all of them, and would be right to. The answer is
not that I will be more careful; it is that prose is now something the test
suite reads.

---

## The one that would sting

> **"You built the thing you could measure instead of the thing that matters."**

There is something to this. Authorization is a beautiful problem for an
engineer: it has invariants, it has a boundary, it is testable, it fails
loudly. Semantic understanding — the part where a person says something vague
and the system has to be genuinely useful about it — is messier, less
satisfying to engineer, and probably where the actual product value is.

REMIT's precision number is the visible edge of that. The system is far better
at *refusing correctly* than at *understanding well*, and I know which of those
I found more fun to build.

The defence is that the refusing has to be right first, because the cost of
being wrong is asymmetric. But it is a defence, not a denial.
