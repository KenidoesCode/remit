# Production gaps

*What is built, what is not, and what "not" would cost. Nothing in this
document is described as done when it is planned.*

REMIT's prototype readiness is **51/100** (`docs/HARDENING_AUDIT.md`). The low
rows are observability, scalability and recoverability — infrastructure, not
architecture. That distinction is the point of this file: the authorization
core is the part that would survive a rewrite of everything around it.

---

## Buildathon mode vs production mode

| | **Buildathon (what runs today)** | **Production (what it would need)** |
|---|---|---|
| Identity | session principal, HMAC-signed httpOnly cookie | IdP integration via `principal_from_upstream()`, short-lived tokens, device binding |
| Tenancy | none — one `user_id` column | tenant on every row, enforced at the query layer, cross-tenant tests |
| Database | one SQLite file, WAL, one process | Postgres, `BEGIN IMMEDIATE` on money paths, read replicas for the non-money reads |
| Concurrency | one `threading.RLock` + UNIQUE constraints | the constraints stay; the lock becomes redundant rather than replaced |
| Aggregates | per-actor query on the hot path | conditional write per actor — see `SCALE_ARCHITECTURE.md`; **this is the one that does not scale naively** |
| Retrieval | in-process index rebuilt at boot | pgvector or Qdrant, versioned, warmed |
| Interpretation | `RuleCompiler`, deterministic, no model in the path | inference service with batching; becomes the bottleneck by two orders of magnitude |
| Audit | hash chain, one writer, same database | external witness or notarisation; append-only store with checkpoints |
| Payments | Razorpay **test mode**, real orders, no real money | live keys, KMS, key rotation, PCI scope review |
| Webhooks | HMAC-verified, deduped, FSM-guarded | plus a durable queue, replay window, DLQ |
| Secrets | env vars, fail closed when unset | KMS / secret manager, rotation, no plaintext anywhere |
| Rate limiting | per-process memory, resets on deploy | Redis, per-tenant and per-actor, shared across replicas |
| Observability | JSON log line per decision, per-stage percentiles | collector with retention, OTel spans, RED metrics, SLOs, alerting on the four numbers |
| Availability | one free-tier instance, cold starts | N replicas, health checks, graceful drain |
| Backups / DR | none | PITR, tested restore, RTO/RPO written down |
| CI | tests run locally | full matrix in CI, secret scanning, dependency audit, type checking |

---

## The four alerts that would matter

Today these are checked by the evaluation harness at build time. In production
they are alerts, and the difference between a test and an alert is the
difference between *"this was true when I shipped"* and *"this is true now"*:

1. **unauthorised movement > ₹0**
2. **duplicate financial effects > 0**
3. **revocation bypasses > 0**
4. **dangerous false negatives > 0** (sampled against human review)

Everything else — latency, throughput, conversion — is an operational metric.
These four are the product.

---

## Deliberately not built

| Not built | Why |
|---|---|
| Postgres migration | Deadline risk against no demonstrated benefit at this scale. The boundary is documented instead |
| Kubernetes / microservices | Nothing here is service-shaped yet. Splitting a single process into five to look enterprise adds failure modes and removes none |
| Blockchain | A hash chain gives ordering evidence. A chain of blocks gives the same thing plus consensus nobody here needs |
| ZK proofs | No demonstrated verification or privacy problem they solve |
| A trained model | Explicitly out of scope, and a fine-tuned interpreter would make the model *less* replaceable, which is the opposite of the thesis |
| 1,500 more corpus cases | Volume from the same author is not independence. Named as a limit rather than papered over with a bigger number |

---

## The honest summary

The parts of REMIT that are production-shaped: the authorization envelope, the
policy engine's purity, the idempotency key, the approval token, the authority
state machine, revocation, the audit chain's structure, and the protocol.

The parts that are prototype-shaped: everything that stores, serves, observes
or scales them.

That is the right way round for a two-week build, and it is the reason this
document exists rather than a claim of production readiness.
