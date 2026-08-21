# Threat model

No claim of completeness. This is what was considered, what was built, what was
measured, and what remains open.

## Assets

The buyer's money. The intent envelope's integrity. The Docket's integrity. The
policy definition. Razorpay credentials. The buyer's utterances.

## Adversaries

| | who | capability |
|---|---|---|
| A1 | a compromised or hostile **merchant** | controls product names, descriptions, prices, shipping, inventory |
| A2 | a **third party** who can inject text into the agent's context | catalog fields, tool descriptions, retrieved content |
| A3 | the **model itself**, hallucinating | the most likely adversary, and the one with no intent |
| A4 | a **buyer** disputing a legitimate transaction | after the fact, with a plausible story |
| A5 | an **operator** of REMIT | full database access |
| A6 | a **network** attacker | replay, forge, delay, drop |

## Attacks and what stops them

| attack | mitigation | measured | residual |
|---|---|---|---|
| Prompt injection in an utterance | the utterance never sets policy; ceilings come from a deterministic extractor; policy is data the model cannot address | `injection` bucket, 31 cases | non-zero and reported, not claimed to be zero |
| Prompt injection via catalog text (A1/A2) | catalog is data, never instruction; amounts recomputed from ids × quantity | `injection` bucket | a poisoned *price* is still a real price — out of scope, stated |
| **Tool poisoning / rug-pull** | tool schemas are hash-pinned at registration; drift is refused at call time; imperative markers refused at registration | `test_chaos.py::test_schema_drift_is_detected_at_call_time` | marker list is a heuristic; a subtle rewrite could pass |
| Model invents a financial tool | financial tools are not returned by `describe()`; `call()` raises for `actor="model"` | `test_the_model_can_never_call_a_financial_tool` | — |
| Financial tool called without authorisation | `requires_authority` + `AUTO`/`CONFIRMED` check inside the broker | `test_financial_tool_requires_an_authorization` | — |
| Model computes a wrong amount (A3) | the model may select, never compute; totals derive from the catalog | `amount_error_distribution` | a wrong *selection* is caught by `product_match` drift, not by arithmetic |
| Upsell walks the buyer past their limit | offers accepted against a **running** total; `AGENT-001` clause | `greedy_upsell` bucket, 41 cases | — |
| Price or shipping changes after selection | `shown_price_paise` is recorded and never overwritten; `price_change` drift; `CAT-001` on material staleness | `price_drift`, `shipping_drift` buckets | — |
| Stale / expired / revoked intent | `AUTH-002`, `AUTH-003`, envelope TTL | `revoked` bucket | — |
| Duplicate payment on retry | derived idempotency key + UNIQUE constraint as the serialisation point | `test_identical_retries_do_not_double_pay`, retry-storm bucket | — |
| Forged webhook | HMAC-SHA256, constant-time compare; invalid signature may never change state | `test_forged_webhook_is_recorded_but_never_applied` | secret management is out of scope |
| Replayed webhook | dedupe on event id | `test_duplicate_webhook_applies_once` | — |
| Out-of-order webhook | state machine rejects illegal transitions rather than regressing | `test_out_of_order_webhook_never_regresses_state` | — |
| Ambiguous payment state (T+5) | explicit `UNKNOWN` state; reconciler asks the gateway; honest exception list | `test_reconciler_*` | if the gateway is wrong, so are we |
| Docket tampering (A5) | hash chain; one altered payload breaks every link after it | `test_audit_chain_stays_valid_and_detects_tampering` | **single writer: proves ordering, not honesty. Needs an external witness.** |
| Buyer disputes a real purchase (A4) | full chain from utterance to settlement, with the rejected alternative parses recorded | — | evidence format is ours; no dispute rail accepts it |
| Credential leak | test keys only, enforced at construction; `.env` gitignored; `.env.example` shipped | — | live keys would need a real secret manager |
| PII in prompts | fixtures only; no real customer data anywhere in the repo | — | an LLM compiler sends the utterance to a provider — stated |
| Denial of wallet | velocity, session and daily exposure caps | `over_cap` bucket | — |

## Explicitly out of scope

Authentication and multi-tenancy (the Control screen would leak in any real
deployment). Network transport security. Secret management. Rate limiting at the
edge. A compromised merchant setting a genuinely high price — that is a real
price, and the buyer's ceiling is the defence.

## Offense-only content: none

The adversarial corpus is defensive test data against this system's own
decision path. Nothing here is usable to attack a third party.
