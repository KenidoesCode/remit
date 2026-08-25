# Decisions

Short ADRs. The choice, what else was on the table, why this one. These are the
questions a panel actually asks, so the answers live in the repo rather than in
my head.

## ADR-001 - The model may select, never compute
Amounts are derived from catalog id x quantity in `intent/compiler.py`. The
model's own stated amount is kept only so `CONF-002` can measure the
disagreement. **Alternative rejected:** trusting the model's arithmetic and
validating a range. **Why:** a range check cannot distinguish "confidently
wrong" from "correct", and order-of-magnitude errors in Indic numerals
(das sau vs das hazaar) land inside plausible ranges.

## ADR-002 - The policy engine is pure
`(intent, remit, spend, p, policy, now) -> Decision`, no I/O, clock as an
argument. **Alternative rejected:** letting the engine query the database for
current spend. **Why:** purity is what makes counterfactual replay possible,
and the replay is the demo.

## ADR-003 - Three ceilings, not one
Per-transaction, aggregate, and count. **Why:** a per-transaction cap alone is
defeated by repetition; an aggregate cap alone permits one catastrophic buy.

## ADR-004 - Aggregate exposure is computed across all live grants
`AGG-001` uses `SpendState.subject_live_exposure_paise`. **Why:** UPI Circle
caps a delegate, an SBMD mandate caps a mandate, and nothing in the live
ecosystem caps the union. This is the one clause with no counterpart in any
shipped product.

## ADR-005 - The escalation threshold is derived, not chosen
Escalate when `E[loss] = (1-p) x amount x irreversibility > friction_cost`.
**Alternative rejected:** a fixed confidence threshold of 0.8 or 0.9.
**Why:** a fixed threshold ignores stakes. The demo prints the implied
threshold per transaction, which is the honest way to state it.

## ADR-006 - Idempotency key is derived from four components
`H(remit_id | intent_hash | envelope_epoch | revocation_epoch)`.
**Why each:** two grants may legitimately buy the same cart; the same cart
twice in one turn is one purchase; a new notified envelope is a new
authorisation period; and the revocation epoch closes the TOCTOU gap between
the policy check and the debit.

## ADR-007 - The UNIQUE constraint is the serialisation point
`claims.idem_key PRIMARY KEY`, `INSERT` and catch `IntegrityError`.
**Alternative rejected:** `SELECT` then `INSERT`. **Why:** check-then-act is a
race across processes.

## ADR-008 - A timeout after create enters AMBIGUOUS, never a retry
**Why:** RBI's TAT circular allows T+5 for "debited but merchant confirmation
not received". A system with no ambiguous state either double-charges or
wrongly refunds. The reconciler owns that window; the executor does not guess.

## ADR-009 - SQLite, not Postgres
One writer, tens of thousands of events, and a reviewer must be able to open
the file. **Why not Postgres:** operational cost with no benefit at this size.
Migration is one interface.

## ADR-010 - Hash chain, not a blockchain
Tamper-evidence with a single writer needs an ordered hash chain. **Why not a
blockchain:** consensus solves multi-writer disagreement, which does not exist
here. Stated honestly: a single-writer chain proves ordering, not honesty -
non-repudiation needs an external witness, which is listed as a limitation.

## ADR-011 - No ZK proofs, no Kafka, no Kubernetes, no vector DB, no Rust
No untrusting verifier, one process, one team, a 60-SKU catalog where exact
match beats embeddings and is auditable, and no performance requirement near
any relevant limit. Recorded because rejecting the interesting tools with
reasons is the point.

## ADR-012 - Abstention is a return value, not an exception
`IntentCompiler.compile` returns `None`. **Why:** an abstention is a normal,
measurable outcome that belongs on the risk-coverage curve, not an error path.


---

## ADR-013 — Drift is a weighted vector of named dimensions, not a model output
Twelve dimensions, each a small pure function with a published formula and
weight, renormalised over *evaluable* dimensions only.
**Alternative rejected:** ask an LLM for a drift score.
**Why:** "drift: 0.42" from a model is unfalsifiable. A reviewer can argue with a
weight; they cannot argue with a number that has no derivation. Renormalising over
evaluable dimensions is the load-bearing detail — an unstated constraint is
`not_evaluable`, never zero drift, because scoring silence as compliance is how an
unbounded agent looks safe.

## ADR-014 — Drift and risk are separate engines
**Why:** drift asks *is this what they asked for?*, risk asks *what does being
wrong cost?*. A transaction can be perfectly on-intent and still risky, or
off-intent and trivially cheap. One combined score answers neither question.

## ADR-015 — Friction cost is a function of transaction size
`max(floor, bps × total)`, not a constant.
**Why:** a flat rupee figure asserts that interrupting someone over ₹50 costs the
same as over ₹50,000. The first version used a flat ₹15 and produced 296
unnecessary escalations in 540 journeys (FAILURES.md 16:10). Both parameters are
policy knobs and both are swept by the frontier — the honest claim is "here is the
exchange rate", not "here is the right number".

## ADR-016 — Isotonic calibration, chosen on dev, after temperature failed
**Alternative rejected:** temperature scaling — which was tried first and made
ECE *worse* (dev 0.145 → 0.224).
**Why:** one parameter can only shift confidence uniformly, and this parser is
over-confident in one band and under-confident in another. Isotonic assumes only
monotonicity. Dev ECE 0.145 → 0.080. Selection was made on dev; test was never
consulted. Cost, stated: isotonic can overfit on a few hundred labels and gives a
step function.

## ADR-017 — Financial tools are hidden from the model, not just refused to it
`ToolBroker.describe()` omits them entirely; `call()` raises for `actor="model"`.
**Why:** defence in depth, and the cheaper half is the invisibility. You cannot
call what you cannot name. The raise is the backstop for a bug in the caller, not
the primary control.

## ADR-018 — Vanilla single-page UI, not React + Three.js + GSAP
**What was specified:** React/Next, TypeScript, Tailwind, GSAP, WebGL.
**What was built:** one HTML file, one CSS file, one JS file, plus a canvas chart.
**Why:** the frontier chart is the only graphic in this product that carries
information the tables do not, and it was built properly. A WebGL scene of nodes
lighting up would be decoration on a system whose entire argument is that it does
not decorate — and a half-finished React app with a build step would have cost a
day and made the repo harder to run in sixty seconds. CSS transitions carry the
one piece of motion that means something: the boundary bar moving from green to
amber to a hatched red overflow.
**Cost, stated:** no component reuse, no type checking on the front end, and the
UI would need a real framework the moment it grew a second interactive surface.
This is a deviation from the brief and it is recorded rather than hidden.

## ADR-019 — Offers are accepted against a running total
**Why:** evaluating each offer independently let three that each fit jointly break
the envelope (FAILURES.md 18:40). Any budget check that runs per-item and not
per-basket is arithmetic with a hole in it.

## ADR-020 — `shown_price_paise` is recorded and never overwritten
**Why:** "what we are about to charge" and "what the human was shown" are
different facts, and the gap between them is a first-class drift dimension. It is
also what makes the evaluation's ground truth honest: the metric is defined over
what the human would have noticed, not over what the test fixture configured.

## ADR-021 — A single RLock in the API layer
**Why:** FastAPI runs sync endpoints in a threadpool and SQLite connections are
thread-affine. `check_same_thread=False` alone is a footgun; the ledger's hash
chain and the payment claim table both need exactly one serialisation point, and
at this size a lock is the correct one rather than a queue or a connection pool.


---

## ADR-022 — The experience is five acts, not a dashboard
A reviewer meets the product before the architecture, and the person last. Act I
the neighbourhood, II the agent moves, III the line, IV break it, V the engineer.
**Why:** a dashboard asks the reader to assemble the argument themselves. A
sequence makes the argument. Someone who leaves after Act III has still
understood the whole product.
*(Five acts, and the payment state machine has five states. Both were already
five.)*

## ADR-023 — White, black, and exactly one red
**Superseded ADR-018's blue system.** One accent, and it is red, because the
product is a line that stops money and red is the only colour that already means
that.
**The cost, and how it is paid:** with a single accent you cannot use hue alone
to separate "allowed" from "refused". So state is carried by **form**: allowed is
white and quiet, asked-about is a hollow red, refused is a filled red — and every
state also carries a word. Colour is never doing the work alone, which is the
only honest way to run a one-accent palette in a product about money.
**Consequence worth noting:** red-on-black is also, quietly, the palette of a
certain friendly neighbourhood character. No asset, no logo, no fan art — just a
colour scheme that happened to be right on its own terms.

## ADR-024 — Canvas 2D for the neighbourhood, not WebGL
The map is twelve meaningful nodes, a route and one boundary, all driven by real
`/api/graph` events.
**Why not WebGL:** it would add a dependency, a loading state and a fallback path
in order to draw twelve circles. The cost is real and the benefit is zero. The
rule from ADR-018 stands: a graphic earns its place by carrying information the
tables do not.

## ADR-025 — GSAP, vendored locally
Used for the title card, the route draw and the boundary transitions — real
sequencing work that hand-rolled `setTimeout` chains do badly.
**Why vendored rather than CDN:** this repo runs with no network, and that is a
property worth keeping. 73KB on disk is cheaper than a runtime dependency on
someone else's uptime.

## ADR-026 — The property line is a control, not an illustration
Dragging it calls `POST /api/replay`, which re-runs `compute_drift` → `assess` →
`authorize` on the same basket under a different authority, and returns the real
microseconds it took.
**Why this is the important interaction:** it is the only way to *show* rather
than assert that the policy engine is pure. If `authorize()` did I/O, neither this
nor the frontier sweep would be possible. The engine time is displayed for the
same reason — a claim about speed should arrive as a number.

## ADR-027 — The failure lab reads FAILURES.md at runtime
`GET /api/failures` parses the markdown rather than duplicating it.
**Why:** a page that restates a document drifts from it. This one cannot, because
it is the document.

---

## ADR-028 — One predicate for "does this answer what they asked for?"

**Context.** The catalog search and the drift engine each implemented the
product-match test. The search accepted name, category or attribute matches;
drift accepted only name or category. Both were reasonable in isolation.

**Decision.** Export `term_answers(name, category, attributes, terms)` from
`remit/domain/catalog.py` and have both call it. `CartLine` carries
`attributes` so drift evaluates the identical inputs.

**Alternatives considered.**
- *Lower the `product_match` weight from 2.5.* Rejected: it would have hidden
  the disagreement behind a smaller number and left two definitions in place.
- *Make the search stricter to match drift.* Rejected: it would have shrunk
  what the agent can find in order to make two functions agree, trading real
  capability for internal consistency.
- *Re-label the ground truth so a widened search counts as "should ask".*
  Rejected as metric-gaming. It may well be the correct label, but changing the
  measure to improve the score is not how you find that out.

**Trade-off.** Drift is now marginally more permissive: a product matched only
by attribute no longer scores drift. That is the intended behaviour, and the
compound-attribute rule that stopped "earbuds" matching "earbuds-accessory"
is preserved inside the shared predicate.

**Result.** Held-out precision 0.5238 → 0.55, recall unchanged at 1.0,
dangerous false negatives unchanged at 0, unauthorised movement unchanged at
₹0.00.

---

## ADR-029 — The opening is an application state, not a video

**Context.** The product needed an identity moment before the homepage, and the
obvious cheap answer is an MP4 or a Lottie file.

**Decision.** Build it from what is already on the page: one inline SVG with
three paths, animated with the GSAP the product already loads, using the
existing `--bg`, `--signal`, `--ink`, `--m` and `--s` tokens. No new
dependency, no asset, no network request.

**Alternatives considered.**
- *A video.* Rejected: an asset to download, a codec to fall back on, and it
  cannot inherit the palette if the palette ever changes.
- *A canvas or WebGL scene.* Rejected: the page already runs a WebGL layer, and
  a second GL context for one thread is disproportionate.
- *Show it once and store `introSeen`.* Rejected: identity that only the first
  visitor sees is not identity, and a reviewer who refreshes should see the
  same product.

**Trade-off.** Every visit costs ~3.2 seconds before the product. Mitigated by
keeping it short and by loading the homepage in parallel behind it.

**The failure mode that mattered most.** An opening that hangs is worse than no
opening. Three independent guarantees: a `try/catch` that reveals on any throw,
a no-op GSAP shim already on the page if the CDN and the vendored copy both
fail, and a 5.2-second hard timer that reveals the product regardless of what
the animation is doing. Verified by deleting `window.gsap` at runtime and
confirming the homepage still appears.

---

## ADR-030 — Animation callbacks are not control flow

**Status:** accepted

**Context.** The opening tore itself down inside a GSAP `onComplete`. In a
background tab, `requestAnimationFrame` is throttled to a near halt, GSAP stops
advancing, and the callback never runs — leaving an opaque full-screen panel
over a page that had already been revealed. Nothing threw, so every guard we
had (try/catch, a GSAP shim, a hard timer) sailed past it. See FAILURES #15.

**Decision.** Anything whose absence breaks the product gets a clock that is
independent of the renderer. Animation callbacks may *decorate* a transition;
they may not be the only thing that completes it. Concretely: the opening's
removal runs on `setTimeout`, and GSAP's `onComplete` is a redundant second
path made harmless by `remove()` being idempotent.

**Consequence.** One redundant call and one extra timer, forever. In exchange,
the class of bug where the product is invisible because a frame loop is slow
cannot recur here. The same rule applies to any future reveal, modal or
overlay: if it covers the product, its removal does not run on rAF alone.

**Rejected.** Hiding `#intro` with a CSS rule keyed on `data-intro="done"`
would also have worked and needed no timer. It was rejected because the fade is
worth keeping, and a CSS-only teardown would have to choose between an abrupt
cut and a transition that has the same problem in a different language.


---

## ADR-031 — The vocabulary comes from the catalog, and REMIT never substitutes

**Status:** accepted (supersedes the `term_fallback` behaviour)

**Context.** Grounding was a hand-written dictionary of category words. It knew
only the nouns I had personally thought of; adding 85 products to the catalog
taught it nothing. When it recognised no noun it widened to a category and
bought whatever ranked first — which is how "buy a helicopter under 500000"
returned a yoga mat (FAILURES #13) and how "rice and cooking oil" delivered oil
(FAILURES #16).

**Decision.** Three parts, and they stand or fall together.

1. **The lexicon is derived from the catalog** — product names, categories,
   subcategories and attributes — so the vocabulary is exactly the set of
   things a merchant can actually sell, and it grows when the catalog grows.
   593 phrases from 186 products, rebuilt per catalog version. One small
   hand-written synonym map survives, and a test asserts every entry in it
   resolves to something buyable.

2. **Requested items are first-class.** A conjunction or a comma starts a new
   one. Adjacent words qualify the same one. The catalog arbitrates when the
   grammar guesses wrong: try the conjunction, and if nothing satisfies all of
   it, split it.

3. **Nothing is substituted, ever.** An unstocked noun is named back to the
   human. A stocked-but-expensive one comes back with its real price. A noun
   that matches only as a modifier inside some product's name (MATCH-001,
   ADR-033) goes to a person.

**Consequence.** Recall of "things REMIT will attempt" is now bounded by the
merchant's catalog rather than by my imagination, which is the correct bound.
The cost is that a merchant with badly-written product names gets a worse
grounder, and the fix for that is to fix the names — which is also correct,
because those names are what their customers search.

**Rejected.** Sending the utterance to a model to ground it. It would score
better on the long tail and it would destroy the two properties this system is
built on: determinism (the same sentence must compile to the same envelope
forever, or replay is fiction) and the ability to say *why* a word was
understood the way it was.

---

## ADR-032 — Unknown words are explained, not charged for

**Status:** accepted

**Context.** Once the grounder could report words it did not recognise, the
obvious move was to count them as unfulfilled requests: you asked for three
things, you got two, that is drift. It was obvious and it was wrong. "yaar ek
yoga mat order kar do teen hazaar tak" contains four words this catalog will
never hold and exactly one thing to buy. Counting them turned 87 correct
purchases into interruptions (FAILURES #18).

**Decision.** An unknown word contributes to `drift` **only** if it stood alone
as a request — its own conjunct, with nothing grounded beside it. Otherwise it
is reported to the human in words and costs them nothing. What always counts is
an item that grounded to a real term and still never reached the cart: that is
a shortfall REMIT caused, not a word it failed to parse.

**Consequence.** "buy a ferrari and some rice" buys the rice, tells you plainly
that no ferrari is stocked, and does not interrupt you about it. A reviewer may
reasonably argue that it should ask. The counter-argument is the interruption
budget: every step-up spends attention, and spending it on vocabulary rather
than on money is how people learn to click through.

---

## ADR-033 — A modifier is not a head noun

**Status:** accepted

**Context.** "buy a laptop" bought a Laptop Stand on AUTO with drift 0.00 and
every clause green, because "laptop" is a word in its name (FAILURES #24). No
existing mechanism could have objected: drift, risk and the policy limits are
all questions about magnitude, and this was a question about meaning.

**Decision.** The lexicon records, per phrase, whether it ever appears as a
**head** — the final token or final pair of a product name after stripping
brand, size and packaging, or a category, subcategory, or whole attribute. A
requested item whose every term is modifier-only is `approximate`, and that
fails the new soft clause `MATCH-001`, which routes to a human with the
sentence "you said 'laptop'; the nearest thing this shop sells is 'Deskhaus
Laptop Stand'".

**Consequence.** "buy basmati" also steps up, because basmati is a modifier of
rice. That is a false positive and I am keeping it: the alternative requires
knowing that basmati *is a kind of* rice while a laptop is *not a kind of*
stand, which is world knowledge this system deliberately does not have.

**Rejected.** Refusing outright. The stand may well be what they meant, and a
shop that answers "no" to "buy a laptop" while holding something with LAPTOP
printed on the box is not being safe, it is being useless.

---

## ADR-034 — Ambiguity in the amount resolves downward

**Status:** accepted

**Context.** `best_ceiling` took the largest candidate amount, so any number
later in the sentence became the budget — including one an attacker appended
(FAILURES #25).

**Decision.** Proximity first: the ceiling is the amount adjacent to the word
that makes it a ceiling, on the correct side for the language ("under 200" in
English, "5 thousand tak" in Hindi). When proximity cannot decide, take the
**smallest** candidate. Rejected candidates are recorded in telemetry as the
evidence that the ambiguity was adjudicated rather than missed.

**Consequence.** A person who says two numbers gets the more conservative
reading and a note saying so, and can restate. The system is never generous by
accident.

---

## ADR-035 — Exposure is per-actor and time-boxed, by definition

**Status:** accepted

**Context.** `_exposure` summed every payment row on the instance for all time
and handed it to the policy engine as one person's hourly velocity. After
twelve journeys, `VEL-001` — a hard clause — denied every utterance from every
visitor, permanently (FAILURES #21). The policy engine was correct throughout;
it was fed a lie by twelve lines of SQL in the API layer that nothing tested.

**Decision.** Exposure is scoped to a user id and windowed by time — the last
hour for velocity and session, since midnight for the daily cap. Every browser
session gets its own id. A limit that counts other people's transactions
against you is not a limit; it is a fuse.

**Consequence.** Multi-tenancy is now visible in exactly the place it matters
first. The remaining gap is honest and documented: user ids are
browser-generated and unauthenticated, so this is isolation, not identity. Real
tenancy needs authentication and a tenant column on every table, which is in
the roadmap and is not built.

**The wider lesson, recorded because it will happen again.** The pure,
replayable, well-tested core was undone by untested glue. Purity protects the
function; it does not protect the arguments.

---

## ADR-036 — The protocol is a projection, never a second implementation

**Context.** `/v1` had to be real enough for an external agent to integrate
against, and REMIT already had a working engine reachable only through its own
website.

**Decision.** Every `/v1` route calls `journey.run`. `remit/v1.py` builds views
and nothing else — no verdict is computed there, no cart is priced, no order is
created.

**Why.** A protocol with its own code path will disagree with the engine, and
the disagreement is the first thing an integrator finds. Two tests hold the
line: one asserts both surfaces return an identical verdict and identical
failed-clause list for the same sentence; the other greps `v1.py` for
`authorize(`, `create_order`, `compute_drift(`, `price_cart(` and `assess(`.

**Cost.** The projection layer is dumber than it could be — it cannot, for
example, evaluate an action against a stored intent id without the utterance,
because the authority is bound to the words that created it. That is a
limitation of the design and it is the correct one.

---

## ADR-037 — Revocation is forward-only

**Context.** "Can I stop it?" is the question people ask before handing an
agent money. The obvious implementation also unwinds what already happened.

**Decision.** Revocation stops what has not executed. It does not reverse a
completed payment. Revoking after execution is allowed, recorded, and changes
nothing about the transaction.

**Why.** Reversing settled money is a refund: a different operation, with a
different authority, different rails and different accounting. A control plane
that quietly unwinds completed payments is one nobody can reason about, and the
first time it is wrong it is wrong about somebody's money in the direction that
cannot be undone.

**Cost.** A person who revokes expecting a refund does not get one. The
response says so in its own text rather than leaving them to find out.

---

## ADR-038 — The authority machine is checked twice, and one check can never fire

**Context.** Revocation is enforced by `AUTH-003` at decision time. The
interesting revocation is the one that lands *between* the decision and the
execution.

**Decision.** Check again immediately before the payment is created, even
though a single process-wide lock makes that interleaving impossible today.

**Why.** A control that is correct only because of a lock it does not own is
not a control. The day this runs in two processes is not the day to discover
that the guarantee depended on a mutex somewhere else in the file.

**Cost.** One query per journey that can never return anything in the current
deployment, and a comment explaining why it is there — which is cheaper than
the alternative by a wide margin.

---

## ADR-039 — Observability stops at the process boundary

**Context.** Observability scored 3/10 and the obvious fix is a `/metrics`
endpoint and an OpenTelemetry exporter.

**Decision.** One JSON log line per decision and per-stage percentiles served
from memory. No metrics endpoint, no tracing exporter, no log shipping.

**Why.** A Prometheus route nobody scrapes is decoration, and a trace with one
span is a log line. Both would look like observability without being any. The
honest artefact is the measurement plus a list of what production would need,
which is `docs/OBSERVABILITY.md`.

**Cost.** A reviewer looking for `/metrics` will not find one. They will find a
document explaining that decision, which is the trade I want.

---

## ADR-040 — The demonstration is computed, and allowed to disagree with me

**Context.** "A limit is not an authority" is the central claim. The easy
version is a table of hand-written rows.

**Decision.** One mandate is held fixed, candidates are derived from the
catalog's own relations table, and every row is re-decided by the real drift
engine and the real 21 clauses on a throwaway instance.

**Why.** A demonstration that cannot come out the other way is not evidence. If
REMIT starts allowing the laptop stand, that table will say so — and a test
asserts the gap between the two columns still exists, so the day the argument
stops being true, the build goes red rather than the page going quiet.

**Cost.** Two wrong versions before this one, both recorded in the commit: rows
that each carried their own mandate, and a synthetic baseline that skipped the
ranking and made the honest case look like a refusal.
