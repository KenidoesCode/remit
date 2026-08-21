# Setup

## Sixty seconds, from a clean clone

```bash
pip install -r requirements-dev.txt
pytest                                  # offline, no API key, no network
PYTHONPATH=. python demo/hero.py        # the seven-scene demo
PYTHONPATH=. uvicorn remit.api:api --port 8000
```

Open http://localhost:8000.

Nothing above needs an API key, a Razorpay account, or a network connection.
That is deliberate: an evaluation that cannot be reproduced is an anecdote.

## Regenerating every number in the repo

```bash
python eval/generate.py      # the corpus (540 journeys, fixed seed)
python eval/calibrate.py     # fit on TRAIN only; choose on DEV
python eval/run_eval.py      # the scorecard -> eval/results/eval.json
python eval/experiments.py   # four arms  -> eval/results/experiments.json
python eval/frontier.py      # the frontier -> eval/results/frontier.json  (~2 min)
```

Every figure in `README.md`, `EVALUATION.md` and the web UI is read from those
JSON files. None of them is typed by hand.

## Live Razorpay test mode

```bash
cp .env.example .env      # add rzp_test_ keys
REMIT_LIVE=1 PYTHONPATH=. uvicorn remit.api:api --port 8000
```

REMIT raises at construction if the key does not begin with `rzp_test_`. A live
key in a repo that deliberately injects retries and duplicate webhooks is how a
student ends up explaining real debits to a stranger.

Webhooks: point Razorpay at `POST /api/webhook` and set
`RAZORPAY_WEBHOOK_SECRET`. Signature verification is HMAC-SHA256 over the raw
body with a constant-time compare; an invalid signature never changes state.

## Fonts

The three faces load from Google Fonts as a progressive enhancement. With no
network they fall back to Georgia / system sans / system mono and the design
holds. Nothing in the product depends on them.

## Layout

```
remit/
  domain/      intent, catalog, cart, revenue, drift, risk      (pure, no I/O)
  policy/      authorize.py  -- pure, deterministic, final
  intent/      amounts.py (code-mixed amount extraction), shopping.py
  buyer/       journey.py -- the orchestrator; read this file first
  tools/       broker.py -- schema-pinned, financial-tool gate
  exec/        idempotency, razorpay adapter, payments FSM, webhooks, recon
  ledger/      hash-chained Docket
  seed/        the fictional catalog
  api.py       FastAPI; routes translate, nothing decides
policy/        authorize.yaml -- policy as DATA
eval/          corpus generator, harness, calibration, experiments, frontier
web/           single-page UI
demo/          hero.py, walkthrough.py
web/           the five-act experience (no build step); vendor/ holds GSAP
creative/      creative direction, brand, interaction map, easter eggs
```

## Reading order for a reviewer with four minutes

1. `remit/buyer/journey.py` — the whole product, top to bottom.
2. `remit/domain/drift.py` — the formula, the weights, and why unstated
   constraints are `not_evaluable` rather than compliant.
3. `remit/policy/authorize.py` — 17 clauses, pure, no model input.
4. `FAILURES.md` — the things that actually broke.
