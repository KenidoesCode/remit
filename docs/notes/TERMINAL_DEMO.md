# Demo script

Two versions. Both run from a clean clone with no API key.

```bash
PYTHONPATH=. python demo/hero.py                 # terminal, 7 scenes
PYTHONPATH=. uvicorn remit.api:api --port 8000   # the product
```

---

## 3 minutes

**0:00 — the problem, in one sentence.**
"A human authorises an intent once. An agent then makes thirty decisions before
money moves. Nobody measures what happens in between."

**0:15 — the agent works.** Type *find me premium running shoes under ₹5000 and
buy the best value option.* It searches 101 products, ranks them, picks one, says
why. Point at the boundary bar: **authorised ₹5,000 · about to charge ₹4,998 ·
room left ₹2.00.** The revenue engine filled the envelope to within two rupees
without crossing it.

**0:45 — the merchant gets a turn.** Scroll to the offers. Each has a reason, an
exact marginal cost, and a flag for whether it changes what was authorised.
Nothing was added silently. Note one offer with a *negative* delta — adding it
crosses the free-delivery threshold and the total goes **down**.

**1:15 — the world moves.** Re-run with a shipping change injected. The bar turns
red and overflows the line. The verdict is STEP_UP, and the reason is a sentence:
*"this is more than you authorised (CEIL-001: ₹5,775.00 ≤ ₹5,000.00)"*, with the
counterfactual underneath. **₹0 moved.**

**1:45 — the failure, on purpose.** Retry the same request four times. One order.
Then send the webhook twice, out of order, and forged. State machine holds:
duplicate refused, late event refused, forged event recorded and never applied.

**2:15 — the numbers.** Revenue Lab. Four arms, 540 journeys, real runs. An agent
optimising with no boundary earns ₹384,180 more than plain checkout **and moves
₹154,477 nobody authorised**. REMIT keeps 77.3% of that upside at **₹0**, with a
higher AOV.

**2:40 — the frontier.** The chart. Autonomy is free up to 45%. One step further
and ₹68,175 starts moving unasked. *"The goal is not maximum autonomy. It is the
point where autonomy stops paying for itself in money the buyer never
authorised."*

---

## 5 minutes

Insert after 1:45:

**Evaluation (45s).** Gates on the held-out split, scored once: ₹0 unauthorised,
0 duplicates, 0 webhook violations, recall 1.0. Then the calibration table —
temperature scaling made it *worse*, and why: the miscalibration is
non-monotonic, so one parameter cannot fix it. Isotonic nearly halved dev ECE.

**The Docket (30s).** Expand one event. Hash-chained, tamper-evident. Then break
one row in SQLite and reload: the chain reports BROKEN at the exact sequence.

---

## The magic moment, staged

The frontier chart, and the sentence that goes with it:

> "Every point on this line is a full re-run of 540 journeys. The shaded region
> is where the agent starts spending money it was never authorised to spend. The
> useful finding is that the boundary between those two regions is **not where
> anyone would guess** — you can hand an agent nearly half of all decisions for
> free, and the step after that is the expensive one."

Why it lands: it is evidential, not visual. It shows that decisions were
*recorded* rather than re-derived, that the policy engine is pure, that an
evaluation harness exists at all, and that the system can produce a number about
its own safety/revenue tradeoff instead of asserting one.

## What to say if it breaks live

Run `pytest`. 70 tests, offline, six seconds. Then open FAILURES.md and talk
about the retry storm, or the yoga mat that turned into a gym towel. The failures
are the more interesting half of this project.
