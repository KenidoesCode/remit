# Architecture

## Shape

A **modular monolith**. One process, one SQLite file, layered by dependency
direction, not by deployment boundary.

```
                    web/  (vanilla SPA)
                      |
                 remit/api.py           routes translate, nothing decides
                      |
              remit/buyer/journey.py    the orchestrator: the product, in one file
             /        |        |      \
   domain/intent  domain/catalog  domain/revenue   domain/cart
             \        |        |      /
                 domain/drift  domain/risk
                      |
              policy/authorize.py       PURE. deterministic. final.
                      |
        tools/broker.py   exec/payments.py   exec/webhooks.py   exec/recon.py
                      |
              exec/razorpay.py            the ONLY file that knows Razorpay
                      |
                   db.py  ledger/chain.py
```

Dependencies point downward only. `domain/` imports nothing from `exec/`,
`policy/` imports nothing at all except its own types, and `api.py` contains no
business logic. That is what lets the CLI demo, the HTTP API and the 540-case
evaluation harness all drive the identical code.

## Why a modular monolith

Because there is one team, one deployment, and a single serialisation point that
genuinely matters (the ledger's hash chain and the payment claim table). Splitting
this into services would introduce network partitions between components that
must agree about whether money moved. Microservices here would be a way to make
correctness harder in exchange for an architecture diagram.

## Why SQLite

One writer, tens of thousands of rows, and a reviewer must be able to open the
file and look. WAL gives durability. Postgres would add operational cost and buy
nothing at this size; the migration is one interface. Under FastAPI's threadpool
the connection uses `check_same_thread=False` and every write goes through one
`RLock` in `api.py` — SQLite is not the serialisation point, that lock is.

## Why the policy engine is pure

`authorize(env, cart, totals, drift, risk, exposure, policy, now, ...)` performs
no I/O, reads no clock, and uses no randomness. Three things depend on that:

1. **The frontier.** Ten policy configurations × 540 journeys is a real re-run in
   about two minutes, because nothing needs to be re-derived from the world.
2. **Reproducibility.** The same inputs give the same decision on any machine, in
   a test, or in a dispute six months later.
3. **Explainability.** A pure function's output can carry every clause it
   evaluated, passed and failed, without a side-channel.

`now` is an argument, not a call. Policy is **data** (`policy/authorize.yaml`),
not code, so "no integrity layer" in the experiment is the same code path with a
permissive file — not a different build.

## Why the intent envelope is versioned and immutable

Because "the human agreed to this" is a claim that has to survive being
questioned. An envelope is never mutated; a change creates version n+1 with a
reason, and the history is what makes an after-the-fact adjudication possible.
`semantic_hash` deliberately excludes the id and the timestamps, so two identical
utterances from the same person are recognisably the same purchase intent — which
is what makes idempotency work across retries.

## Why drift is a vector, not a score

An LLM producing "drift: 0.42" is unfalsifiable. Twelve named dimensions, each
computed by a small pure function with a documented formula and a published
weight, can be argued with. The formula, the weights and the renormalisation over
*evaluable* dimensions are all in `remit/domain/drift.py`.

The renormalisation matters more than it looks: if the human never stated a
category, category drift is **not measurable** and is reported as
`not_evaluable`. Scoring an unstated constraint as zero drift is exactly how an
unbounded agent looks safe.

## Why drift and risk are separate

Drift asks *is this what they asked for?* Risk asks *what does being wrong cost,
and is this account behaving normally?* A transaction can be perfectly on-intent
and still risky (large, irreversible, on a session that has already spent a lot),
and it can be off-intent and trivially cheap. Collapsing them would lose both
questions.

## Why the LLM cannot authorise money

Structurally, not by instruction:

- `ToolBroker.describe()` does not return financial tools to the model. You
  cannot call what you cannot name.
- `ToolBroker.call()` raises `UnauthorizedTool` if `actor="model"` reaches a
  financial tool, and again if the caller has no `AUTO`/`CONFIRMED` authorization.
- Amounts are derived from catalog id × quantity in `domain/cart.py`. The model
  selects; it never computes. Where it states an amount, the disagreement with the
  catalog is *measured* rather than trusted.

## Why webhooks are event-driven and defensive

Three assumptions that are false in production, refused explicitly: that a
webhook arrives exactly once (dedupe by event id), that it arrives in order (the
state machine rejects illegal transitions instead of regressing), and that it is
authentic (HMAC-SHA256, constant-time compare, and an invalid signature may never
change state).

## Why idempotency is derived, not stored

`H(user:semantic_hash | cart_signature | total | catalog_version)`. Every
component earns its place: two users may buy the same cart; the same cart twice
in one turn is one purchase; a different total is a different purchase; a catalog
change re-prices it. The **UNIQUE constraint** on `payments.idem_key` is the
serialisation point — check-then-act in application code is a race.

## Why the UNKNOWN payment state exists

RBI's TAT circular allows **T+5** for "debited but merchant confirmation not
received". A system without an ambiguous state will either charge twice or refund
something that never settled. On a timeout after order creation, REMIT enters
UNKNOWN deliberately and the reconciler owns it — it asks the gateway what
actually happened and only then moves the payment, or reports an honest
exception.

## Why a hash chain and not a blockchain

Tamper-evidence with a single writer needs an ordered hash chain: each event
carries the hash of the one before, so one altered payload breaks every link
after it. Consensus solves multi-writer disagreement, which does not exist here.
Stated honestly in LIMITATIONS.md: a single-writer chain proves ordering, not
honesty.

## Why the front end is what it is

See DECISIONS.md ADR-018. Short version: the canvas frontier chart is the only
graphic that carries information the tables do not, and it was built; a WebGL
scene and a motion library would have been decoration on top of a system whose
whole argument is that it does not decorate.
