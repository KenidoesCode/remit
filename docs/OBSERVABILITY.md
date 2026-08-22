# Observability

*What is measured, how, and — the longer half — what is not.*

Observability scored **3/10** on the readiness scorecard and the honest reason
was this: REMIT threaded a correlation id through every layer and then gave
nobody a way to look at one. Two `print` calls, a single `latency_ms` on the
journey result that measured the whole thing, and no way to answer *which stage
was slow* or *what happened to `cor_abc`*.

---

## What exists now

### One JSON line per decision

`REMIT_LOG=1` turns it on. Off by default, because a library that writes to
stdout the moment it is imported corrupts somebody's JSON output.

```json
{"ts": 1787392308.7, "event": "decision", "cid": "cor_5dacceaa4857c37d",
 "verdict": "AUTO", "failed": [], "total_paise": 497600, "drift": 0.0,
 "intent": "int_c4a478b58a6747af91", "actor": "u",
 "policy_version": "2026-08-21.a", "catalog_version": 1}
```

`cid` joins to the ledger, the decisions table, the payment row and the
authority history. That is the whole design: one id, five stores, one query
each.

**The utterance is deliberately not in the log line.** It is in the audit
ledger, where it is evidence and access-controlled. Logs go to stdout, stdout
goes to a hosting provider, and a shopping sentence belongs to the user rather
than to the operator. Amounts, verdicts, clause ids and timings are
operational, and are.

### Per-stage percentiles

`GET /api/timing`, and measured rather than asserted:

| stage | what it covers | p50 | p95 | p99 |
|---|---|---|---|---|
| `interpret` | sentence → envelope | 0.61 ms | 0.74 ms | 3.31 ms |
| `retrieve` | grounding, vectors, ranking, pricing | 1.70 ms | 2.15 ms | 5.15 ms |
| `policy` | drift, risk, 22 clauses — pure, no I/O | **0.06 ms** | 0.07 ms | 0.10 ms |
| `execute` | order creation at the gateway (fake) | 0.13 ms | 0.17 ms | 0.22 ms |

*n = 20 per stage, one process, this container. Reproduce with
`REMIT_LOG=1 python -c "..."` or hit `/api/timing` after using the site.*

Two things that matter more than the numbers:

**`n` is reported next to every percentile.** A p99 over eleven samples is the
second-slowest request wearing a statistic's clothes, and a percentile without
its sample count invites exactly that reading.

**Payment latency is not mixed with decision latency.** The gateway is across
the internet and the policy engine is not. Averaging them produces a number
that describes neither, and it is the number most likely to be quoted.

The interesting result is `policy` at **0.06 ms p50** — the deterministic part
is three orders of magnitude cheaper than the retrieval around it. That is why
the frontier sweep, the Arena's 540 journeys × 7 agents, and the property line
are all possible: `authorize()` does no I/O, so re-deciding is free.

---

## What does not exist, and why not

| Missing | Why it is not here |
|---|---|
| `/metrics` endpoint | A Prometheus route that nobody scrapes is decoration. Needs a scraper, which needs infrastructure this deployment does not have |
| Distributed tracing | One process. A trace with one span is a log line |
| Log shipping / retention | stdout to a free-tier host. Retention is whatever the host keeps |
| Alerting | No on-call, no thresholds anyone has agreed |
| RED / USE dashboards | Would need the two rows above first |
| Per-tenant breakdown | There is no tenancy — see the production gaps |

**Production would need:** structured logs to a collector with retention and
redaction, OpenTelemetry spans across interpret → retrieve → policy → execute
→ webhook, RED metrics per route and per clause, a decision-latency SLO kept
separate from a payment-latency SLO, and alerting on the four numbers that
actually matter — unauthorised movement, duplicate financial effects,
revocation bypasses and dangerous false negatives.

Those four are currently checked by the evaluation harness at build time. In
production they are alerts, and the difference between a test and an alert is
the difference between "this was true when I shipped" and "this is true now".
