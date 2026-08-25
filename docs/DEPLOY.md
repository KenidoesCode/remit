# Deploying REMIT

REMIT is one FastAPI app. `main.py` at the root exposes it as `app`; the
platform detects FastAPI from `requirements.txt` and routes every request to it
with the path intact, so the hosted site and `uvicorn remit.api:api` run the
identical program. `pyproject.toml` states the entrypoint outright
(`remit.api:api`) so nothing is inferred. There is deliberately no `vercel.json`
— see FAILURES.md #11 and #12 for why.

## What "real" means here

REMIT talks to Razorpay for real. `remit/exec/razorpay.py` is the only file that
knows Razorpay exists, and with keys configured it makes live HTTPS calls to
`api.razorpay.com/v1/orders`. Orders it creates appear in the Razorpay dashboard.
Test mode is enforced in code: a key that does not begin `rzp_test_` is refused
at construction. This is a payments system built by a student for a submission,
and it has no business holding anyone's live credentials.

The full round trip is wired:

1. `POST /api/shop` — the agent shops, the policy engine decides. Only on an
   allowed verdict is a Razorpay **Order** created, keyed by an idempotency hash
   so a retry storm cannot produce a second one.
2. `GET /api/checkout/{correlation_id}` — returns the public key id and order id.
   The API secret never leaves the process.
3. Razorpay **Checkout** opens in the browser and the human pays.
4. `POST /api/payment/verify` — the browser is not a trusted narrator. The
   signature is HMAC-SHA256 over `order_id|payment_id` with the API secret,
   verified server-side, constant-time. An invalid signature is written to the
   ledger and changes no state.
5. `POST /api/webhook` — Razorpay's own callback, verified the same way, deduped
   by event id, and refused if it would drive an illegal state transition.

## Environment

| variable | what it does |
|---|---|
| `RAZORPAY_KEY_ID` | test key id, must start `rzp_test_` |
| `RAZORPAY_KEY_SECRET` | test key secret |
| `RAZORPAY_WEBHOOK_SECRET` | the secret you set on the webhook in the dashboard |
| `REMIT_LIVE=1` | use the real gateway instead of the in-process double |
| `REMIT_DB` | path to a SQLite file. **Set this in production.** |
| `ANTHROPIC_API_KEY` | optional; enables the LLM intent compiler |

Get the test keys from the Razorpay dashboard: Account & Settings → API Keys →
Generate Test Key. Add the webhook at Settings → Webhooks, pointing at
`https://<your-host>/api/webhook`, subscribing to `payment.authorized`,
`payment.captured`, `payment.failed` and `order.paid`.

## Where to host it, honestly

**Use a host that keeps a process and a disk** — Render, Railway and Fly all
have a free tier that does. `REMIT_DB=/data/remit.sqlite` and it behaves.

Serverless is the wrong shape for this app and it is worth knowing why rather
than finding out later. Every invocation may be a fresh instance, so with the
default in-memory database the ledger's hash chain, the idempotency table and
the session exposure caps last exactly one request. Idempotency that forgets is
not idempotency. Point `REMIT_DB` at a network filesystem and SQLite's locking
assumptions stop holding; the honest fix on serverless is Postgres, which is a
schema port this project has not done.

`render.yaml` in the repo root is a working blueprint: free plan, health check
on `/health`, `REMIT_DB` pointed at a file, and the three Razorpay values marked
`sync: false` so they are set in the dashboard and never committed.

```bash
#   build:  pip install -r requirements.txt
#   start:  uvicorn remit.api:api --host 0.0.0.0 --port $PORT
#   env:    REMIT_DB=/tmp/remit.sqlite  REMIT_LIVE=1  RAZORPAY_*
```

On Render's free plan the instance sleeps after inactivity, so the first request
after a quiet period takes roughly a minute. That is the plan, not the app.

## Locally, end to end

```bash
pip install -r requirements-dev.txt      # tests + optional LLM compiler
python eval/run_eval.py && python eval/experiments.py && python eval/frontier.py
export RAZORPAY_KEY_ID=rzp_test_xxx RAZORPAY_KEY_SECRET=xxx REMIT_LIVE=1
export REMIT_DB=./remit.sqlite
uvicorn remit.api:api --reload
```

Open http://localhost:8000, run a journey that returns AUTO, and the **Pay**
button opens Razorpay Checkout on the order REMIT authorised. Use test card
`4111 1111 1111 1111`, any future expiry, any CVV. The payment then appears in
your Razorpay dashboard, and `/api/control` shows it settled.

To watch the boundary do its job, run a journey that returns STEP_UP. There is
no Pay button, because there is no order: the policy engine refused to create
one, so there is nothing to pay.

## Front end only

`deploy/site/` is a static build for hosting the page without the engine. It
says so on its face and does not pretend to transact.
