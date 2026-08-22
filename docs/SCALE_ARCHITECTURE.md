# Scale

*Measured on this repository before anything was drawn. The numbers below come
from `eval/scale.py` and land in `eval/results/scale.json`; re-run it and you
get your own.*

**Hardware:** 2 vCPU shared container, Python 3.11.15, SQLite in WAL, one
process. Read the *shape*, not the absolute numbers — this is not a benchmark
rig, and saying so is more useful than quoting a throughput figure from it as
though it meant something.

---

## The one number the architecture rests on

```
pure authorize()   p50 27.3 µs   p99 73.0 µs   ≈ 32,000 decisions/second/core
                                                (n = 20,000)
```

`authorize()` takes `now` as an argument and does no I/O. That is not a
micro-optimisation, it is what makes three other things in this repository
possible: the frontier sweep re-decides the same basket at 40 different
authority levels, the Arena runs 540 journeys × 7 agents = 3,780 decisions on
every change, and the property line on the website re-decides your cart while
you drag a slider. All three exist because a decision costs 27 microseconds and
touches nothing.

If the policy engine had reached for a database, none of them would be here —
and the honest version of this document would be about caching decisions,
which is a category of bug rather than a category of optimisation.

---

## What actually happens under load

| n | workers | wall | req/s | p50 | p95 | p99 |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1 | 0.01 s | 169 | 5.9 ms | 5.9 ms | 5.9 ms |
| 10 | 1 | 0.04 s | 277 | 3.9 ms | 4.9 ms | 4.9 ms |
| 100 | 1 | 0.35 s | **289** | 3.5 ms | 4.5 ms | 5.3 ms |
| 100 | 8 | 0.91 s | 109 | 62.8 ms | 179.2 ms | 283.8 ms |
| 500 | 8 | 5.14 s | 97 | 72.9 ms | 187.2 ms | 240.1 ms |
| 1000 | 16 | 16.96 s | **59** | 267.5 ms | 498.1 ms | 574.1 ms |

**Throughput falls as concurrency rises.** 289 req/s single-threaded, 59 req/s
at sixteen workers. That is the opposite of scaling and it is the correct
result for what this is: one process, one `threading.RLock` around every
mutating path, and a GIL. Sixteen threads do not get sixteen cores; they get
one core and a queue, plus the cost of contending for it.

Publishing a graph that went up and to the right would have required either
lying or removing the lock, and the lock is what makes the concurrency tests
pass.

### Where the time goes

Per-stage p50, from the same runs:

| stage | 100 × 1 worker | 1000 × 16 workers | change |
|---|---:|---:|---|
| `interpret` | 0.54 ms | 0.78 ms | ×1.4 |
| **`retrieve`** | **1.29 ms** | **90.36 ms** | **×70** |
| `policy` | 0.047 ms | 0.076 ms | ×1.6 |
| `execute` | 0.12 ms | 9.42 ms | ×79 |

**The bottleneck is retrieval, and it is not close.** Grounding, vector
similarity over 186 products, ranking and cart pricing — CPU-bound Python
under a GIL, serialised behind a lock it does not need. The policy engine, the
part everyone assumes is expensive because it is the part with rules in it,
never leaves 76 microseconds.

`execute` inflating ×79 is the same lock, not the gateway: this run used
`FakeGateway`, so that number is queueing, not network.

---

## What each ladder rung would need

### 1 request
Works today. 3.5 ms end to end, of which the authorization decision is 1.3%.

### 100 requests
Works today, single-threaded, at 289/s. Nothing needs to change.

### 10,000 requests
The lock has to go, and it can — most of it is not protecting anything that
needs a process-wide lock.

- **Retrieval is pure and needs no lock at all.** Embedding, filtering and
  ranking read an immutable index. Moving them outside the critical section is
  the single highest-value change in this document and it is a small one.
- **Writes need a transaction, not a mutex.** The real serialisation points
  are already in the database — the UNIQUE index on `payments.idem_key`, the
  predicated `UPDATE … WHERE used_at IS NULL` on approvals, the predicated
  `UPDATE … WHERE state = ?` on the authority machine. The lock is belt and
  braces on top of correct constraints; SQLite → Postgres with `BEGIN
  IMMEDIATE` makes it redundant rather than replacing it.
- **Then processes, not threads.** The GIL means one Python process saturates
  one core regardless. N workers behind a load balancer, sharing Postgres.

Expected shape after those three: retrieval parallel across cores, writes
serialised by row rather than by process, throughput linear in cores until the
database becomes the constraint.

### 1,000,000 requests
Different questions, and honestly ones this prototype cannot answer from
evidence:

| Component | What it needs |
|---|---|
| Retrieval | a real vector store (pgvector / Qdrant), not an in-process index rebuilt at boot |
| Interpretation | if a real LLM is in the path, an inference service with batching — and it becomes the bottleneck by two orders of magnitude, at which point the interesting engineering is *caching interpretations keyed on (utterance, catalog version, model version)* |
| Policy | still 27 µs. It is the only component that scales for free |
| Payments | rail-bound. The gateway's rate limits, not REMIT's |
| Audit | append-only writes are the second bottleneck. Batched appends, or a log-structured store with periodic chain checkpoints |
| Exposure | the aggregate queries (`EXPO-001/002`, `SPLIT-001`) are per-actor reads on every journey. At this scale they belong in Redis with the database as the source of truth |

---

## The thing that does not scale, and cannot be made to

`SPLIT-001` and the exposure clauses are *aggregate* checks: they ask what this
principal has already spent. That is a read-your-own-writes requirement on the
hot path of a money decision, and it does not survive naive horizontal scaling
— two replicas reading a stale replica of the spend total will both allow the
transaction that crosses the cap.

The correct answer is not caching. It is that aggregate authority must be
enforced at a single serialisation point per actor: a conditional write against
the actor's own row, so that the read and the decision are one operation. The
same shape as everything else that turned out to be correct in this system —
the idempotency key, the approval token, the state machine — and the same shape
as the three bugs found when those were tested concurrently (FAILURES #44).

That is a design note, not an implementation. It is not built, and this
document is not going to imply that it is.

---

## Reproduce

```bash
python eval/scale.py          # writes eval/results/scale.json
curl localhost:8000/api/timing   # per-stage p50/p95/p99 with sample counts
REMIT_LOG=1 uvicorn remit.api:api   # one JSON line per decision
```
