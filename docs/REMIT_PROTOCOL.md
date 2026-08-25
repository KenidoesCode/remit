# The REMIT protocol

**Version 1.0** · `GET /v1/` returns this contract as JSON.

> An agent may interpret. A deterministic policy engine authorises. The payment
> rail executes.

---

## Why a protocol at all

REMIT was a website with an engine behind it. Everything a reviewer could do,
they did through a page this repository also owns — which makes *"this is
infrastructure, not an app"* a thing said rather than a thing shown.

`/v1` is the seam. Six nouns, and an agent that has never heard of a drift
dimension, a `RequestedItem`, or the fact that the policy is YAML.

`agents/external_agent.py` imports `json` and `urllib`. That is its entire
dependency list, and `tests/test_protocol.py` asserts it stays that way — if
that file ever needed the journey or the envelope class, REMIT would be a
library with a website on top and this document would be marketing.

---

## The six nouns

| Noun | Question it answers |
|---|---|
| **Intent** | what did the human authorise, in their own words |
| **Authority** | what bounded envelope did that become, and what state is it in |
| **Action** | what does the agent propose to do with it |
| **Decision** | may it — AUTO, STEP_UP or DENY, with every clause behind it |
| **Execution** | the money, once and only once |
| **Evidence** | why did this happen, reconstructable without asking the model |

### Money is never a bare number

```json
{ "amount_paise": 500000, "currency": "INR" }
```

An amount without a unit is not an amount. The bug that let `"under $5,000"`
become a ₹5,000 ceiling — 85× off, in the permissive direction, silently —
lived in exactly the gap this type closes (FAILURES #39). Paise, because
integers do not round.

### Intent keeps the words separate from the interpretation

`utterance` is evidence. Everything under it — category, requested terms,
exclusions, ceiling, objective — is interpretation, and `interpreter` names
which intelligence produced it. An agent that disagrees can argue against the
original sentence.

---

## Routes

| Method | Path | Does |
|---|---|---|
| `GET` | `/v1/` | the contract, including its own limitations |
| `POST` | `/v1/intents` | compile an utterance into a bounded authority |
| `POST` | `/v1/evaluate` | *would* this be allowed — **no money moves** |
| `POST` | `/v1/step-up` | what exactly is being asked of the human |
| `POST` | `/v1/execute` | do it, if the policy allows |
| `POST` | `/v1/approve` | redeem a token bound to one basket |
| `POST` | `/v1/deny` | decline a step-up |
| `POST` | `/v1/revoke` | cancel an authority, forward only |
| `GET` | `/v1/authorization/{intent_id}` | current state and its history |
| `GET` | `/v1/audit/{correlation_id}` | why this happened |
| `GET` | `/v1/receipt/{correlation_id}` | the authorization receipt: authority, decision, execution and audit, in one view |

### Identity

A signed httpOnly session cookie, or `Authorization: Bearer <session>` for a
client with no cookie jar.

**There is deliberately no API key.** A key is a bearer credential, and a
bearer credential in a request is a field a caller can set to somebody else —
which is precisely the bug FAILURES #32 was about. A production deployment
binds this principal to the merchant's own identity provider; that seam is
`principal_from_upstream()` and it is named in `docs/FINAL_AUDIT.md` rather
than faked here.

### Errors that are not failures

| Code | `error` | Means |
|---|---|---|
| 422 | `not_grounded` | this catalog cannot answer that request. REMIT does not substitute |
| 422 | `no_decision` | there was no cart, so the policy engine was never reached |
| 400 | — | `execute` was called with an id and no utterance (see below) |
| 404 | — | that authorization or correlation id is not yours |

`no_decision` exists because the first version of this projection defaulted a
missing authorization to `DENY`. That reads as *"the policy engine refused
this"* when what happened is that the policy engine was never asked. Different
sentences, and an integrator's retry logic depends on which one is true. Caught
by the test asserting `/v1` and the website agree on every verdict, on
`"buy chips under 20"` — a request this shop can answer, and not that cheaply.

404 rather than 403 for another actor's resources: whether an id exists is not
a thing these endpoints should confirm.

### Why `execute` needs the utterance, not just an id

An authority is bound to the words that created it. Executing against an id
alone would let a caller reuse somebody's mandate for a different request —
the same class of mistake as trusting a `user_id` in a body. The semantic hash
is what ties a decision to what was actually said.

---

## A whole conversation

```bash
curl -sX POST $REMIT/v1/intents -H 'content-type: application/json' \
  -d '{"utterance":"buy running shoes under 5000"}'
```
```json
{ "intent": { "intent_id": "int_…", "ceiling": {"amount_paise": 500000, "currency": "INR"},
              "requested": ["running shoes"], "excluded": [],
              "interpreter": "rule", "confidence": 0.92 },
  "authority": { "state": "DRAFT", "revoked": false, "expires_at": "…" } }
```

```bash
curl -sX POST $REMIT/v1/evaluate -d '{"utterance":"buy running shoes under 5000"}'
```
```json
{ "verdict": "AUTO", "would_execute": true, "sandboxed": true,
  "total": {"amount_paise": 429900, "currency": "INR"}, "clauses": [ … 22 … ] }
```

`evaluate` runs on a throwaway instance — an endpoint that says *"this is only
a question"* must not be able to answer it by doing the thing. That lesson cost
FAILURES #38.

```bash
curl -sX POST $REMIT/v1/execute -d '{"utterance":"buy running shoes under 5000"}'
```
```json
{ "decision": { "verdict": "AUTO", "failed": [], "correlation_id": "cor_…" },
  "execution": { "state": "CREATED", "order_id": "order_…", "replayed": false },
  "authority_state": "EXECUTED" }
```

Call it again and `replayed` is `true` with the same `payment_id`. `replayed`
is part of the contract, not an implementation detail: an integrator retrying
needs to know the payment it is looking at is the one it already made.

### When a person has to be asked

```json
POST /v1/step-up  →
{ "required": true,
  "asking": { "clause": "RESTRICT-001",
              "why": "requires a person: The Cellar Blended Whisky 750ml (age)",
              "amount": {"amount_paise": 159900, "currency": "INR"},
              "items": [ … ] },
  "approval": { "token": "apr_…", "binds": ["user","intent hash","cart hash","amount","expiry"] } }
```

The token is bound to five things. Change any of them and it stops verifying,
with the reason named: `already_used`, `wrong_actor`, `cart_changed`,
`amount_changed`, `currency_changed`, `merchant_changed`, `expired`.

### Taking it back

```json
POST /v1/revoke {"reason":"handing the laptop back"}  →
{ "revocation_id": "rvk_…", "scope": "principal", "revoked_at": "…" }
```

Then every subsequent execute:

```json
{ "decision": { "verdict": "DENY", "failed": ["REVOKED"] },
  "execution": { "state": "BLOCKED" },
  "revocation": { … who, when, why … } }
```

`failed: ["REVOKED"]` and **not** a clause id — the journey stops before the
policy engine is asked, so claiming `AUTH-003` fired would be inventing
evidence.

---

## The property that makes this worth publishing

`/v1` has **no engine of its own**. Every route is a projection over the same
journey, the same policy engine and the same payment path that the website
uses. Two tests hold that line:

- `test_v1_and_the_website_agree_on_every_verdict` — same sentence, both
  surfaces, identical verdict and identical failed-clause list.
- `test_v1_has_no_engine_of_its_own` — `remit/v1.py` may not contain
  `authorize(`, `compute_drift(`, `price_cart(`, `create_order` or `assess(`.

If `/v1` had a second code path, the guarantee a reviewer verifies on the
website would not be the guarantee an integrator gets — and the first thing
anyone would find is the disagreement between them.

---

## What this is not

- **Not production identity.** Session principal, no IdP, no tenancy.
- **Not a payment rail.** Razorpay test mode, real orders, no real money.
- **Not multi-tenant.** One `user_id` column; tenancy is in the production gap
  list, not implemented.
- **Not rate-limited across replicas.** Per-process memory.

`GET /v1/` returns those four notes in its own response, so an integrator
reading the contract sees the limitations before they read the routes.
