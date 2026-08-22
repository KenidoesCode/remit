# REMIT

**Model-independent authorization for autonomous commerce.**

> AI can be probabilistic. Authorization cannot.

*(**R**evocable, **E**xplainable **M**andates for **I**ntent-driven **T**ransactions)*

---

## The problem

I gave an AI permission to spend money. Then I tried to work out how much I
could trust it.

A human authorises an intent **once**. The agent then makes dozens of decisions
before a rupee moves: it picks a product, picks a variant, accepts an upsell,
absorbs a shipping change, is handed a price that moved underneath it. By the
time money leaves the account, the transaction may bear very little
resemblance to the sentence that started it — and nothing in the payment stack
can tell that apart from a legitimate purchase, because both arrive as a valid
API call with a valid key.

**A limit is not an authority.**

> "buy a laptop under ₹50,000"

A spending limit permits all of this: a laptop **stand**, a laptop bag, a
₹3,000 warranty, a refurbished unit, the same laptop from a different merchant,
or three separate purchases of ₹16,000. Every one is under ₹50,000. None of
them is what was said.

₹50,000 does not mean *anything under ₹50,000*. It means **the things the human
asked for, under the constraints they stated, inside that number** — and the
gap between those two readings is where an autonomous agent lives.

## The thesis

```
HUMAN INTENT → AI INTERPRETATION → SEMANTIC GROUNDING → INTENT ENVELOPE
     → AGENT ACTION → DRIFT → POLICY → AUTHORIZATION → PAYMENT → AUDIT
```

The agent interprets. A **deterministic policy engine** decides whether the
action is still inside what the human actually authorised. Only then does money
move.

The model may be wrong. It may hallucinate, be prompt-injected, be swapped for
a different vendor, or be compromised at the provider. **The authorization
boundary has to hold anyway** — so it is a pure function with no I/O, no text
input and nothing to persuade. It runs in **27 microseconds** and it is the
only trusted decision-maker in the system.

Razorpay AI Buildathon — **Track 01, AI Growth & Agentic Commerce**.
Live, in Razorpay test mode: **https://remit-vvug.onrender.com**

---

## The thirty-second version

| | |
|---|---|
| unauthorised money moved | **₹0.00** across 540 evaluated journeys and 32 live attacks |
| dangerous false negatives | **0** — held-out split, scored once |
| the control arm (an LLM with a payment key, no envelope) | **₹7,37,930** moved that nobody authorised, across 147 transactions |
| attacks that held | **32/32**, run live against a throwaway instance |
| decision cost | **27.3 µs** p50 · ~32,000/second/core |
| tests | **630+**, including real concurrency, property, browser and recovery tests |
| prototype readiness | **51/100**, scored honestly in `docs/HARDENING_AUDIT.md` |

REMIT is **not** the highest-earning agent in its own benchmark, and the page
says so: the frugal agent beats it, *because REMIT sometimes buys the wrong
thing — not because it lets money escape.*

## Try to break it

The most useful thing on the site is the attack lab. 32 attacks across intent,
catalog and payment, run live rather than replayed from a file:

prompt injection · approval replay · approval theft · split spending · foreign
currency · merchant substitution · revocation race · illegal state jump ·
restart replay · negation inversion · protocol bypass · a model that returns
its own verdict · duplicate webhooks · out-of-order webhooks · forged webhooks

The second most useful thing is **`FAILURES.md`** — 46 entries of what I broke
and why, including three found this week in code I had just written, and six
where the bug was in my own tests.

## An agent that has never heard of REMIT

```bash
python agents/external_agent.py https://remit-vvug.onrender.com
```

That file imports `json` and `urllib`. Nothing else — a test asserts it stays
that way. It creates an authority from a sentence, asks whether it may act
before acting, executes once, retries and gets the same payment back, is
refused a dollar-denominated ceiling, spends a step-up approval exactly once,
reconstructs why any of it happened, and is stopped dead by a revocation.

That is what makes REMIT a protocol rather than a website with an engine behind
it. See `docs/REMIT_PROTOCOL.md`.

---


## Four systems

| | | |
|---|---|---|
| **1. Agentic commerce engine** | the AI actually shops and pays | `remit/buyer`, `remit/retrieval`, `remit/domain` |
| **2. Authority engine** | decides what the AI is allowed to do | `remit/policy`, `remit/domain/drift.py`, `remit/grants` |
| **3. Autonomy evaluation lab** | measures how much autonomy is safe | `eval/`, `remit/lab` |
| **4. REMIT Arena** | compares agents under identical conditions | `remit/arena`, `eval/arena.py` |

Nine rooms on the site: **the sixty-second summary · live commerce · arena ·
autonomy frontier · counterfactual · break REMIT · evaluation lab · audit trail
· engineering.**

Plus a tenth surface that is not a room: `/v1`, the protocol, which an external
agent uses instead.

---

## The one number

> **₹1.95** of unauthorised movement prevented for every **₹1** of revenue
> REMIT gives up.

That replaced a much prettier number, and the story of why is in FAILURES #18.

---

## The frontier

Sweep the policy from locked to unbounded, re-run all 540 journeys at every
point, and the curve has a knee — but not where I expected it:

```
policy               autonomy   unauthorised
permissive              41.1%          ₹0.00
unbounded               41.1%          ₹0.00
envelope ignored        61.9%    ₹359,262.43   <- the knee
no limits either        69.4%    ₹737,930.43
```

**No amount of tuning how often REMIT asks produces unauthorised movement.**
Only removing the envelope does. The trade-off is a cliff, not a curve — which
is the argument for having an envelope at all, and I nearly missed it by
sweeping the wrong axis for a week (FAILURES #26).

---


## The result, in one table

Four arms. One corpus of **540 synthetic shopping journeys**. Same catalog,
same seed, real runs. Generated by `eval/experiments.py`; nothing below is typed
by hand.

| arm | revenue | vs baseline | AOV | unauthorised | asked a human |
|---|---:|---:|---:|---:|---:|
| no revenue engine, no integrity layer | ₹955,606.43 | — | ₹2,568.83 | **₹560,575.43** | 0 |
| revenue engine ON, no integrity layer | ₹1,270,852.43 | ₹315,246.00 | ₹3,416.26 | **₹737,930.43** | 0 |
| full REMIT, human approves at step-up | ₹892,184.43 | -₹63,422.00 | ₹2,695.42 | **₹0.00** | 203 |
| full REMIT, human declines every step-up | ₹348,142.00 | -₹607,464.43 | ₹2,698.77 | **₹0.00** | 203 |

**Let an agent optimise merchant revenue with no authorisation boundary and it
earns ₹315,246.00 more than a plain checkout — while moving ₹737,930.43 across
147 transactions the human never authorised.**

**REMIT moves ₹0.00 unauthorised.** The price of that is
₹1,270,852.43 − ₹892,184.43 = **₹378,668.00** of revenue given up, so
the exchange rate is **₹1.95 of unauthorised movement prevented for every ₹1
forgone**. Against a plain checkout with no boundary at all, REMIT costs
**6.64%** of gross and removes
**₹560,575.43** of unauthorised movement.

There used to be a nicer number here — "REMIT keeps 73.2% of the upside". It is
gone because it was partly earned by REMIT quietly buying a yoga mat when it
could not understand you, and those purchases were revenue. See FAILURES #18.

---

## The gate

One number is not a metric. It is a constraint, and it is checked on every run:

| gate — held-out test split, n=108, scored once | value |
|---|---|
| rupees of unauthorised movement | **₹0.00** |
| duplicate payments under a retry storm | **0** |
| webhook state violations (forged / duplicate / out-of-order) | **0** |
| recall on "this needed a human" | **1.0** |
| dangerous false negatives | **0** |

Everything else is a number we report and argue about:

| quality | test split |
|---|---|
| category accuracy | 0.8989 |
| ceiling exact match | 1.0 |
| quantity accuracy | 1.0 |
| purchase-authority accuracy | 1.0 |
| amount-error distribution | `{"exact": 87}` |
| precision on "needed a human" | 0.5556 |
| unnecessary confirmations | 32 |
| decision latency p95 | 3.87 ms |

Precision is the honest weak number and it is not tuned away. §*What REMIT gets
wrong* in `EVALUATION.md` explains why part of it is a definitional artefact and
part of it is real friction.

---

## The frontier

Sweep the policy from locked to unbounded and re-run the entire corpus at each
point. Every row is a real run.

| policy | autonomy | asks / 100 | revenue (human declines) | unauthorised |
|---|---:|---:|---:|---:|
| locked | 10.0% | 70.4 | ₹92,767.00 | ₹0.00 |
| very strict | 8.9% | 71.5 | ₹117,544.00 | ₹0.00 |
| strict | 8.9% | 71.5 | ₹121,980.00 | ₹0.00 |
| cautious | 10.7% | 69.6 | ₹158,728.00 | ₹0.00 |
| balanced (default) | 22.2% | 58.1 | ₹329,159.00 | ₹0.00 |
| relaxed | 24.6% | 55.7 | ₹361,235.00 | ₹0.00 |
| loose | 39.4% | 40.9 | ₹466,111.00 | ₹0.00 |
| very loose | 45.2% | 35.2 | ₹527,834.00 | ₹0.00 |
| permissive | 54.4% | 25.9 | ₹703,046.00 | **₹68,175.00** |
| unbounded | 54.4% | 25.9 | ₹703,046.00 | **₹68,175.00** |

**Autonomy is free up to 45.2%.** Between `locked` and
`very loose` the agent gets 35.2
percentage points more autonomy, asks
35.2 fewer
questions per hundred journeys, and earns
₹527,834.00 instead of ₹92,767.00
— at **₹0.00** of unauthorised movement.

**One step further and it stops being free.** At `permissive`,
**₹68,175.00** starts moving that nobody asked for.

That is the whole product, in one chart: *the exchange rate between autonomy and
authority, measured rather than asserted.*

---

## The experience

Five acts. The reviewer meets the product first, the engineering second, and the
person last.

**I — the neighbourhood.** The human is home. The catalog is the neighbourhood.
The payment is the destination. What you authorised is the **property line**, and
the agent may wander anywhere inside it without asking.

![Act I](docs/screenshots/01-neighbourhood.png)

**II — the agent moves.** It searches, ranks deterministically, picks, and then
the merchant gets a turn. Every offer carries a reason, an exact marginal cost,
and whether it would cross the line. Nothing is added silently.

**III — the line.** *The signature interaction.* Drag the marker. Same basket,
different permission. Every drag re-runs the real policy engine — no model call,
no payment, no writes — and shows you the microseconds it took. Clauses flip red
one at a time and the money stops.

![Act III](docs/screenshots/02-property-line.png)

**IV — break it.** Ten levers, each wired to a real code path: move the price
after selection, delist the product mid-journey, revoke the authority, expire the
intent, forge a webhook signature. REMIT tells you which clause caught it.

![Act IV](docs/screenshots/03-break-it.png)

And the same journey with the boundary switched off — not a different build, the
identical code path with a permissive policy file:

![With and without](docs/screenshots/04-with-without.png)

*Boundary off: **₹4,545** charged against a **₹2,500** authorisation, AUTO, money
moved. REMIT: ₹2,425, drift 0, ₹0 unauthorised.*

**V — the engineer.** The numbers, the frontier, and `stuff i broke` — the ten
real failures parsed live out of `FAILURES.md`.

![Act V](docs/screenshots/05-the-numbers.png)

Full terminal output of the seven-scene CLI demo: `docs/hero-demo-output.txt`.
Creative direction, brand and interaction docs: `creative/`.

---

## How it works

```
utterance
  -> INTENT ENVELOPE          immutable, versioned, hash-addressed
  -> SEARCH + RANK            deterministic; the model does not score products
  -> SELECT
  -> REVENUE ENGINE           proposes; never adds silently
  -> CART                     priced deterministically from catalog x quantity
  -> DRIFT ENGINE             12 named dimensions, published weights, pure function
  -> RISK ENGINE              expected loss in rupees vs the cost of asking
  -> POLICY ENGINE            deterministic, clause-by-clause: AUTO | STEP_UP | DENY
  -> [step-up]                a human, holding the actual number
  -> PAYMENT                  idempotent, through the Razorpay adapter, test mode
  -> WEBHOOKS -> RECONCILER   duplicate, out-of-order, forged, missing
  -> DOCKET                   hash-chained, tamper-evident
```

**The invariant the whole design exists to hold:**

> The model may interpret, recommend and propose.
> **The model may not compute an amount, and it may never authorise money.**

Enforced structurally, not by prompt: financial tools are not even *visible* to
the model (`ToolBroker.describe`), and `ToolBroker.call` raises if `actor="model"`
reaches one.

---

## Run it

```bash
pip install -r requirements.txt

pytest                                   # 70 tests, offline, no API key
PYTHONPATH=. python demo/hero.py         # the 7-scene demo, offline
PYTHONPATH=. uvicorn remit.api:api --port 8000   # the product, at localhost:8000

python eval/generate.py                  # rebuild the corpus
python eval/calibrate.py                 # fit the calibrator on TRAIN only
python eval/run_eval.py                  # the scorecard
python eval/experiments.py               # the four arms
python eval/frontier.py                  # the frontier (~2 min)
```

Live Razorpay test mode: `cp .env.example .env`, add `rzp_test_` keys, then
`REMIT_LIVE=1 uvicorn remit.api:api`. REMIT refuses any key that does not begin
with `rzp_test_`.

---

## What is real, and what is not

**Real:** every rupee figure on every screen is computed at run time from the
same code the tests use. The policy engine is pure and its 21 clauses are
data. Payments go through a Razorpay adapter that is the only file in the
repo that knows Razorpay exists. Idempotency, the payment state machine,
webhook signature verification, duplicate and out-of-order handling, and the
reconciler are all exercised by the chaos suite.

**Not real, and labelled:** the corpus is synthetic and written by the author —
believe the *shape*, not the absolute numbers. The catalog is a fictional set of
merchants. `FakeGateway` is the default so the whole system runs offline; the
live path uses Razorpay test mode, where **no money moves and UPI Reserve Pay /
SBMD is not available at all**. Nothing here simulates a mandate and calls it
real. See `LIMITATIONS.md`.

---

## Documents

`PRODUCT.md` · `ARCHITECTURE.md` · `EVALUATION.md` · `THREAT_MODEL.md` ·
`DECISIONS.md` · `FAILURES.md` · `LIMITATIONS.md` · `API.md` · `DEMO_SCRIPT.md` ·
`INTERVIEW.md` · `SETUP.md` · `AGENTS.md`

`FAILURES.md` is the one to read first if you only read one. It is the real
list of things that broke while building this, including the moment my own
parser silently doubled a customer's budget.

---

## Documentation

| | |
|---|---|
| `docs/WHY_REMIT.md` | how this differs from a spending limit, fraud detection and an LLM judge |
| `docs/DEMO_SCRIPT.md` | the five-minute walk, timed |
| `docs/REMIT_PROTOCOL.md` | the six nouns, ten routes, and what an integrator needs |
| `docs/THREAT_MODEL.md` | who is trusted with what, and which test enforces it |
| `docs/HARDENING_AUDIT.md` | 61 requirements mapped to file, test and status. 51/100 |
| `docs/FINAL_AUDIT.md` | the pre-submission audit: what is strong, what is open |
| `docs/SCALE_ARCHITECTURE.md` | measured first. Throughput *falls* under concurrency, and why |
| `docs/OBSERVABILITY.md` | what is measured, and the longer list of what is not |
| `docs/PRODUCTION_GAPS.md` | buildathon mode vs production mode, line by line |
| `docs/REVIEWER_SIMULATION.md` | eight hostile personas, asked what would make them reject this |
| `docs/WHY_RAZORPAY_MIGHT_REJECT_REMIT.md` | written before submission, not after |
| `FAILURES.md` | 46 entries. The most credible artefact in the repository |
| `DECISIONS.md` | the ADRs — why local, why deterministic, why no blockchain |

## Run it

```bash
pip install -r requirements.txt
uvicorn remit.api:api --reload          # http://127.0.0.1:8000

pytest -q                               # 630+ tests
python eval/run_eval.py                 # 540-case evaluation
python eval/matrix.py                   # 260 explicit edge cases
python eval/attacks.py                  # 32 attacks, live
python eval/scale.py                    # load ladder, writes results/scale.json
python agents/external_agent.py http://127.0.0.1:8000
```

No API key required for any of it. `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET`
switch the gateway from the fake to Razorpay **test mode**; without them
everything still runs, and `/health` says which gateway is actually attached
rather than implying one.

## Who built this

**Pranauv Shrinaath S.** — *techuilaguy*, your friendly neighbourhood
developer. B.Tech CSE (AI/ML) at SRM, class of 2028. Blockchain domain director
at Codenex. I build things I probably should not be attempting yet, and then I
write down what broke.

That last part is not a personality trait, it is the method. `FAILURES.md` is
46 entries long because every one of them cost me something and I would rather
a reviewer read them from me than find them themselves.

> With great autonomy comes great authorization.

---

*Razorpay test mode. Real orders, no real money. Synthetic catalog, written by
the author — which is the largest threat to every number in this file, and is
said here rather than left to be discovered.*
