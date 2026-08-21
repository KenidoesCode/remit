# What broke, and how I got out

Real entries, written when they happened. No reconstructions.

---

## 2026-08-21 14:20 — the parser silently doubled the customer's budget

**What I saw.** Smoke-testing the intent compiler, `"buy 2x earbuds under 3000"`
produced `ceiling = ₹6,000`.

**What I thought it was.** A units bug — paise vs rupees somewhere in
`ceiling_paise()`.

**What it actually was.** Not a bug in the arithmetic. A bug in the *semantics*.
`IntentEnvelope.ceiling_paise()` computes `max_price_paise * quantity`, which is
correct when the human states a per-unit price. But `"2x earbuds under 3000"` is
genuinely ambiguous — ₹3,000 each, or ₹3,000 for the pair? — and the code picked
the permissive reading without noticing there was a choice.

This is the exact failure the whole project exists to catch, produced by my own
code, in the first ten minutes of running it. A system that reads a human's
budget twice as large as they meant it has already lost, no matter how good the
policy engine downstream is.

**How I got out.** Made the ambiguity explicit instead of resolving it silently:
- when `quantity > 1` and only one amount was stated, take the **conservative**
  reading (total ceiling, not per-unit);
- unless the utterance carries a per-unit marker (`each`, `per pair`, `apiece`);
- record the interpretation in `telemetry["notes"]` so it appears in the audit
  trail and the UI;
- cut parse confidence by 0.15, so the risk engine sees a less certain parse and
  is more likely to ask a human.

**What I changed so it can't happen again.** `tests/test_shopping_intent.py::
test_ambiguous_quantity_takes_the_conservative_reading` asserts ₹3,000 for the
bare form and ₹6,000 for the `each` form. Added to the evaluation corpus as its
own bucket (`ambiguous_qty`), so the metric moves if it ever regresses.

**Lesson.** Every place the code resolves an ambiguity is a place a human's
authority can quietly expand. The fix is never "pick better" — it is "pick the
safe one, say that you picked, and lower your confidence."


---

## 2026-08-21 15:00 — a retrying agent bought the same thing five times

**What I saw.** The retry-storm bucket in the first full evaluation reported
**16 duplicate payments**. The gate said zero.

**What I thought it was.** A broken idempotency key derivation.

**What it actually was.** The derivation was fine. The *input* was wrong. The key
was built from `intent_id`, and every call to the compiler mints a fresh
`intent_id` — so five identical utterances produced five different keys and five
legitimate-looking payments. The unique constraint was doing its job perfectly on
five different rows.

**How I got out.** Added `IntentEnvelope.semantic_hash`, which hashes *what was
asked for* and deliberately excludes `intent_id`, `created_at`, `expires_at`,
`version` and `parse_confidence`. The key is now
`H(user:semantic_hash | cart_signature | total | catalog_version)`.

**Regression.** `test_identical_retries_do_not_double_pay` plus the retry-storm
bucket, which now reports 0.

**Lesson.** An idempotency key must be derived from the *meaning* of the request.
Anything generated per-call — an id, a timestamp, a session — silently turns
"the same purchase" into "another purchase".

---

## 2026-08-21 15:05 — a captured payment sat in CREATED forever

**What I saw.** Two webhook state violations in the out-of-order bucket.

**What it actually was.** My state machine only allowed `CREATED → AUTHORIZED`.
Gateways routinely drop intermediate events and send `payment.captured` directly.
The transition was refused as illegal and the payment stayed in `CREATED` while
the money had, in fact, been captured.

**How I got out.** `CREATED → SUCCESS` is now legal, with the reason in a comment
above the transition table.

**Lesson.** A state machine that is stricter than reality does not make the system
safer; it makes it wrong in a way that looks principled.

---

## 2026-08-21 15:40 — 84 people were interrupted because an integer moved

**What I saw.** 84 unnecessary confirmations, all attributed to `CAT-001`.

**What it actually was.** `CAT-001` compared `catalog_version` integers. Any edit
anywhere in the catalog bumps that counter — including edits to products nobody
in this cart is buying. The clause was flagging *catalog activity*, not *price
staleness*.

**How I got out.** Both the clause and the drift dimension now take a materially
computed `stale_pricing` signal: does any line still cost what we showed, and does
the cart still price to the same total? The version integer is kept in the record
for provenance and no longer drives a decision.

**Lesson.** A monotonically increasing counter is provenance, not evidence.

---

## 2026-08-21 16:10 — ₹15 was a magic number and it cost 296 interruptions

**What I saw.** 296 unnecessary escalations out of 540 journeys. Nearly every one
traced to expected loss exceeding a flat `friction_cost_paise: 1500`.

**What it actually was.** A constant. Asserting that interrupting someone over a
₹50 purchase costs exactly as much as interrupting them over a ₹50,000 one is
false in both directions, and I had written it without noticing I had decided
anything.

**How I got out.** `friction_cost(total) = max(floor, bps × total / 10_000)`,
defaults ₹15 and 500 bps, both policy knobs, both swept by the frontier. Friction
false positives fell from 296 to 208 immediately and the auto-execution rate went
from 20% to 36% with the safety gate still at zero.

**Lesson.** Every unexplained constant in a decision path is an undocumented
policy. Either derive it or sweep it.

---

## 2026-08-21 17:05 — my own experiment was rigged, in both directions

**What I saw.** Arms A and B produced byte-identical results, and REMIT appeared
to earn 59% *less* than the baseline.

**What it actually was.** Two design errors in the harness, not the system. A and
B had the same configuration under different names. And REMIT was measured only
with `human_confirms=False` — a human who declines every single confirmation —
against baselines where every confirmation was auto-approved. I had accidentally
built the most flattering possible comparison for the unbounded agent.

**How I got out.** Redesigned the arms: plain checkout / agent with no boundary /
REMIT where the human approves / REMIT where the human declines everything. The
last two bracket the truth instead of asserting it.

**Lesson.** When an experiment produces a number that flatters the alternative
you were about to reject, check the experiment first. I got lucky that it was
unflattering — a rigged experiment in my favour would have shipped.

---

## 2026-08-21 18:20 — "buy a yoga mat" bought a gym towel

**What I saw.** Smoke-testing the hero demo: `buy a yoga mat under 2500` selected
a **Kinetic Gym Towel**. Drift score: 0.0.

**What it actually was.** The intent envelope captured `category` but not the
*thing*. "Yoga mat" resolved to the category `fitness accessories`, and every
product in that category was a valid answer as far as search and drift were
concerned. The agent bought something entirely different and the integrity layer
called it a perfect match — which is precisely the failure this project exists to
catch, produced by my own code.

**How I got out.** Three changes. `IntentEnvelope.product_terms` records the noun
the human actually said. `Catalog.search` filters on it (name, category, or an
exact attribute once hyphens are normalised — substring matching bought a
"Buds Case" for someone who said "earbuds"). And drift gained a `product_match`
dimension, weight 2.5, so buying a different thing inside the right category is
now the second-heaviest dimension in the vector.

When the catalog does not stock the named thing, search widens to the category,
records the fallback, and lets `product_match` flag it — the agent offers an
alternative and asks, rather than substituting silently.

**Lesson.** A category is not an intent. Every field the envelope fails to capture
is a degree of freedom the agent has and the human did not grant.

---

## 2026-08-21 18:40 — three offers that each fit, one basket that did not

**What I saw.** `buy earbuds under 3000` executed at ₹3,397.

**What it actually was.** The revenue engine evaluated each offer independently
against the *current* cart. Three accessories each fit in the remaining headroom
at the moment they were considered. Added together, they did not. The agent kept
its promise three times and broke it once.

**How I got out.** Offers are now accepted against a **running** total, re-pricing
the trial cart after each acceptance. `AGENT-001` remains as the backstop for
anything that slips past.

The pleasant consequence: the engine now fills the envelope precisely —
₹4,976 of ₹5,000, ₹2,898 of ₹3,000 — which is the product thesis working rather
than being described.

**Lesson.** "Each of these is fine" is not the same claim as "these are fine".
Any budget check that runs per-item and not per-basket is arithmetic with a hole
in it.

---

## 2026-08-21 17:40 — the ground truth was measuring my injections, not my system

**What I saw.** After tightening the definition of "should have asked", the gate
broke: **6 dangerous false negatives, ₹6,588 unauthorised**.

**What it actually was.** I had added `if case.inject.get("shipping"): return True`
to the ground truth — treating the *presence of a configured world-change* as
proof the human should have been asked. But some injections set shipping to the
value it already had. Nothing changed, the human would have noticed nothing, and
the system correctly proceeded. My metric called that a safety failure.

**How I got out.** Ground truth is now computed purely from observed outcome:
`total > shown_total`. The journey publishes `shown_total_paise` — what the human
was actually shown before the world was allowed to move.

**Lesson.** A metric that reads the test fixture instead of the result is
measuring the test. The gate must be defined over what happened, never over what
was configured to happen.

---

## 2026-08-21 19:15 — SQLite objects created in a thread…

**What I saw.** Every API call returned 500 the moment the UI touched it, while
`curl` had worked minutes earlier.

**What it actually was.** FastAPI runs sync endpoints in a threadpool; SQLite
connections are thread-affine by default. The CLI demo and the test suite are
single-threaded, so nothing had caught it.

**How I got out.** `check_same_thread=False`, plus a single `RLock` in `api.py`
that every endpoint holds. The lock is the honest answer: the ledger's hash chain
and the payment claim table both need one serialisation point, and at this size a
lock is the correct one. The comment in `db.py` says so, because
`check_same_thread=False` on its own is a footgun.

**Lesson.** The CLI and the test suite exercise one concurrency model. A web
server is a second one, and it will find what they cannot.

---

## 2026-08-21 18:05 — the deployed app 404'd every single route, including its own home page

**What I saw.** First hosted deploy answered `{"detail":"Not Found"}` on `/`,
`/health` and `/api/builder` alike. Locally, all three were 200. Same commit,
same Python, same FastAPI object.

**What I thought it was.** A packaging problem — that `web/`, `policy/` and
`eval/results/` had not been shipped alongside the function, so the routes were
registering but their files were missing.

**What it actually was.** Routing, not packaging. My `vercel.json` had a
catch-all rewrite, `/(.*) -> /api/index`, on the assumption that a rewrite
preserves the requested path and merely picks the handler. It does not: the
destination *replaces* the path. Every request reached the ASGI app with
`scope["path"] == "/api/index"`, which matches none of the seventeen routes, so
FastAPI correctly returned its default 404 — for the home page, for the health
check, for everything. The platform detects FastAPI on its own and routes to it
with the path intact; my configuration was overriding the thing that already
worked.

The diagnosis cost far more than the fix, and the reason is worth writing down:
`{"detail":"Not Found"}` is a true statement about a question I never realised I
was asking. It told me the route did not exist. It did not tell me the request
had been rewritten before it arrived, and so I spent the search looking at the
routing table instead of at the request. Every 404 in this system is a claim
about *a path*, and it had not occurred to me to make it say which one.

**How I got out.**
- Deleted `vercel.json` entirely and put `main.py` at the root exposing `app`,
  which is the framework's own supported entrypoint. No routing config at all.
- Registered a catch-all route last, so an unmatched request now returns the
  path the application actually received, plus the routes it does serve. If the
  path in that response is not the path you asked for, something in front of the
  process rewrote it, and you know that in one request instead of six.

**What it changed.** I had been treating "works locally" as evidence about the
application. It was only ever evidence about the application *plus* the request
that reached it. The boundary between those two is exactly the kind of seam this
project is about everywhere else — an instruction can be altered in transit, and
the receiver should be able to say what it actually got. It is a little
embarrassing that the deployment layer had to teach me that about my own thesis.

---

## 2026-08-21 18:40 — the same deploy, one layer down: the host picked the wrong file

**What I saw.** With the rewrite removed, the build did not even start:
`Found app.py but it does not define a top-level "app" FastAPI instance.`

**What I thought it was.** That `main.py` at the root had not been picked up —
some problem with where I had put the entrypoint.

**What it actually was.** The opposite: the entrypoint search found *too much*.
It resolves by filename, and this repository already had a `remit/app.py` — the
composition root, the thing that wires the object graph together and returns an
`App`. It is not an ASGI application and has no `app` in it, but it is called
`app.py`, so the search stopped there and never reached `main.py`.

Two failures in a row, both the same shape: I let a platform infer something I
could have stated. First the path, now the entrypoint. Convention-over-
configuration is a real virtue right up until your own naming happens to collide
with the convention, and then it is a silent argument you did not know you were
having.

**How I got out.**
- Renamed `remit/app.py` to `remit/assembly.py`. It assembles the object graph;
  it was never an app, and the old name was the kind of thing that reads fine
  until something else agrees with it too strongly.
- Added a four-line `pyproject.toml` whose only content is
  `entrypoint = "remit.api:api"`. Now nothing is guessed. Dependencies stay in
  `requirements.txt`; there is no `[project]` table, because this file exists
  for exactly one setting and should not quietly become the build config too.

**What it changed.** Both of these were found by a deployment rather than by a
test, and that is the honest summary: the suite covers what the application does
once a request reaches it, and nothing at all about whether a request can.

