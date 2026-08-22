# Deployment

## Pointing the SDK at a deployment

```ts
new Remit({ baseUrl: "https://your-remit.example.com" });
```

or `REMIT_BASE_URL`, or `remit --url ...`. The default is the public reference
deployment, which is a **prototype in Razorpay test mode** — fine for evaluating,
not for anything real.

## Running REMIT yourself

```bash
git clone https://github.com/KenidoesCode/remit
cd remit
pip install -r requirements.txt
python -m uvicorn remit.api:api --port 8099
```

```bash
remit --url http://127.0.0.1:8099 doctor
```

### Environment

| Variable | Required | What it does |
|---|---|---|
| `REMIT_SESSION_SECRET` | **yes when `REMIT_LIVE=1`** | signs session identities |
| `REMIT_LIVE` | no | production posture: demands the secret, sets `Secure` cookies |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | no | test-mode orders; a fake gateway is used without them |
| `REMIT_LLM_BASE` / `REMIT_LLM_MODEL` | no | any OpenAI-compatible endpoint; falls back to the rule interpreter |

REMIT **refuses to start** with `REMIT_LIVE=1` and no `REMIT_SESSION_SECRET`. A
default would be a published key, and anyone who read the repository could mint
a principal and spend against it.

## Before you put this anywhere real

Honest list, from `docs/PRODUCTION_GAPS.md`:

- **SQLite on one host.** Multi-process correctness is tested; multi-host is a
  design, not an implementation. Postgres is the production answer.
- **No backups, no PITR, no tested restore, no migration tool.**
- **No collector, tracing, metrics endpoint or alerting.** There is structured
  per-decision logging and per-stage percentiles, and that is all.
- **No identity provider.** Bind `principal_from_upstream()` to yours.
- **No secret management.** Environment variables only.
- **No WAF, DDoS protection or abuse controls** beyond in-process rate limiting.

## Performance

Measured, not asserted, on a 2-CPU shared container — read the shape, not the
absolute numbers:

| | |
|---|---|
| `authorize()` p50 | **27.3 µs** (pure function, no I/O) |
| end-to-end p50, 1 worker | 3.49 ms |
| throughput, 1 worker | 289 req/s |
| throughput, 16 workers | **59 req/s** — *worse* |
| retrieval p50 under load | 1.29 ms → **90.36 ms** (×70) |

Throughput **falls** as workers rise. The bottleneck is retrieval, not policy.
The safety layer costs 27 microseconds and is not what would break first. That
result is reported against interest because measuring it and then not saying it
would be worse than not measuring it.

## SDK overhead

The SDK adds one HTTPS round trip per call and JSON serialisation. Receipt
verification is O(events) SHA-256 over small payloads — typically 14 events.

No benchmark of SDK overhead in isolation has been run, so no number is claimed
for it here.
