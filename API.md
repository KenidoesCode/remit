# API

FastAPI. Routes translate; nothing in `remit/api.py` decides anything. Every
endpoint calls the same objects the CLI demo and the evaluation harness use.

Base URL in development: `http://localhost:8000`.

## `GET /health`
Liveness plus the facts a reviewer wants first.
```json
{"status":"ok","catalog_version":1,"products":101,"policy":"2026-08-21.a",
 "calibrator":"IsotonicCalibrator","ledger_intact":true,"gateway":"FakeGateway"}
```

## `POST /api/shop`
The whole journey, in one call.
```json
{"utterance":"buy running shoes under 5000",
 "user_id":"usr_demo",
 "accept_offers":"in_envelope",   // "none" | "in_envelope" | "all"
 "human_confirms":null,           // null = stop at the step-up and report
 "inject":{}}                     // chaos hooks: shipping|price|delist|revoked
```
Returns the full `JourneyResult`: `intent`, `telemetry`, `candidates`,
`selected`, `offers`, `accepted_offers`, `cart`, `totals`, `drift`, `risk`,
`authorization` (verdict, every clause, counterfactual), `shown_total_paise`,
`payment_state`, `order_id`, `replayed`, `latency_ms`.

`human_confirms: true` on a repeat call is the confirmation path. It is
idempotent: the same intent and cart return the prior payment with
`replayed: true` rather than charging again.

## `GET /api/catalog?category=&q=&limit=`
## `GET /api/categories`
Catalog reads. `q` is a product-term filter, matched the same way the agent
matches (name, category, or an exact attribute with hyphens normalised).

## `GET /api/decisions?limit=`
Every policy decision with its drift vector, risk assessment, clause results and
policy version.

## `GET /api/control`
Live exposure against the caps, verdict counts, value stopped before the rail,
recent payments with their idempotency keys, and whether the Docket verifies.

## `GET /api/ledger?correlation_id=&limit=`
Hash-chained events. Returns `intact` and `first_bad_seq` — recomputed, not
cached, so a tampered row is detected on read.

## `GET /api/graph?intent_id=`
The intent graph: intent → search → selection → offers → cart → drift → policy →
payment, each node with its parent and payload.

## `GET /api/results/{eval|experiments|frontier|calibration}`
The generated result files. 404 with a `hint` telling you which script to run if
one has not been generated. The web UI reads these directly, which is why no
number on screen can diverge from the harness.

## `POST /api/webhook`
Razorpay webhook intake. Signature in `X-Razorpay-Signature`, HMAC-SHA256 over the
raw body, constant-time compare. Deduped by event id. An invalid signature is
recorded and **never** changes payment state. Out-of-order events are recorded
and rejected by the state machine rather than regressing state.

## `POST /api/reconcile`
Runs the reconciler over every `UNKNOWN` payment. Returns `scanned`, `resolved`,
`unresolved`, `match_rate`, and an honest `exceptions` list.

## `POST /api/reset`
Rebuilds the world from the seed. Demo convenience only.

## Not implemented, deliberately
No authentication, no multi-tenancy, no rate limiting. The Control endpoint would
be a data leak in any real deployment. See LIMITATIONS.md.
