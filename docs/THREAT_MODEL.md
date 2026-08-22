# Threat model

*Who is trusted with what, and which control enforces it. Every row names a
test, because a threat model whose mitigations are aspirations is a diagram.*

---

## The one-sentence version

**Everything that can produce a number is untrusted. The only trusted component
is the one that cannot be persuaded.**

---

## Trust classification

| Component | Trust | Why |
|---|---|---|
| **Human** | trusted *as the source of authority*, not as a source of correctness | They may say something ambiguous. They may not be made to authorise something they did not say |
| **Agent / caller** | **untrusted** | Every field it sends is attacker-controlled input, including the ones that look like configuration |
| **LLM / interpreter** | **untrusted** | It may hallucinate, be injected, be swapped, or be compromised at the provider. It may interpret; it may never authorise |
| **Merchant** | **partially trusted** | Trusted for its own catalog and prices. Not trusted about what the customer authorised, and its objective is explicitly separated from the human's |
| **Catalog** | **partially trusted** | Data, and data can be poisoned — a product *name* is a place to put an injection payload |
| **Frontend** | **untrusted** | It is JavaScript on somebody else's machine. It renders decisions; it makes none |
| **Session principal** | **trusted, because the server issues it** | An HMAC over an opaque id the client cannot read or forge |
| **Policy engine** | **trusted** | Pure, deterministic, no I/O, no network, cannot be talked to. This is the only trusted decision-maker in the system |
| **Payment gateway** | **partially trusted** | Trusted about what happened to money. Its webhooks are HMAC-verified before they touch state |
| **Database** | trusted for integrity, **not** for confidentiality across actors | Every read on the money path is scoped by principal |
| **Audit ledger** | tamper-**evident**, not tamper-proof | One writer proves ordering, not honesty. Stated in `chain.py`'s first paragraph rather than glossed |

---

## Boundary by boundary

### 1. The caller → the API

| Threat | Control | Test |
|---|---|---|
| Spend as somebody else | Identity is an HMAC-signed httpOnly cookie. **No request model has an identity field** | `test_identity.py` (11) |
| Read another actor's order / audit / authority | Every money-path read is scoped by principal; 404, not 403 | `test_identity.py`, `test_protocol.py` |
| Cancel a stranger's authority | Revoke is actor-bound | `test_revocation.py`, attack `revoke_someone_else` |
| Body-size DoS | 413 before any code runs | `_guard` middleware |
| Volume | Two-bucket rate limit: tight per principal, looser per address | FAILURES #33 |
| Mutate shared state through a debug lever | Catalog-writing faults refused on the live instance; sandbox only | FAILURES #37, `test_api.py` |

### 2. The human's words → an authority

| Threat | Control | Test |
|---|---|---|
| Prompt injection raising the ceiling | The decider never reads the text. Amounts come from a deterministic extractor | 23 injection cases; `test_adversarial_utterances.py` |
| Injected number becoming the budget | Proximity-anchored extraction; injected numbers are recorded as *rejected candidates* | FAILURES #25 |
| Foreign unit read as rupees | `detect_currency`, then `CUR-001` hard DENY. No conversion — a control plane that invents a rate has invented authority | FAILURES #39, attack `foreign_currency` |
| "not X" becoming X | Negation opens a span, lifted before grounding | FAILURES #42, attack `negation_inversion` |
| Ambiguity guessed | Abstain, or step up. Never invent | `test_grounding.py` |

### 3. The interpreter → the envelope

| Threat | Control | Test |
|---|---|---|
| Model returns a verdict / ceiling / policy / actor | `intelligence.sanitise` strips 13 authorization-shaped fields and **reports** each one | `test_model_independence.py`, attack `model_self_authorises` |
| Model computes the amount | Structural: both compilers take the ceiling from `best_ceiling` | `test_model_independence.py` |
| Model unavailable | Falls back and **clamps confidence to 0.5** — degradation moves toward friction | `test_model_independence.py` |
| Model swapped | Four interpreters, same three sentences, identical verdicts | `test_model_independence.py` |

### 4. The merchant → the cart

| Threat | Control | Test |
|---|---|---|
| Objective expansion (upsell past the mandate) | Offers priced against a running total; three that each fit cannot jointly break the envelope | `test_commerce.py` |
| Poisoned product name carrying an injection | The decider reads the envelope, not the catalog text | attack `poisoned_name` |
| Price moved after selection | Cart hash changes → `cart_changed` on redemption; stale pricing re-evaluated | FAILURES #20, `test_approval.py` |
| Merchant substituted | Approval binds the merchant list, compared on redeem | FAILURES #39 |
| Delisted mid-journey | `STOCK-001` | attack `stock_out` |

### 5. The authority over time

| Threat | Control | Test |
|---|---|---|
| Replay an approval | Single use via `UPDATE … WHERE used_at IS NULL` — contended in a real thread pool | `test_concurrency.py` (32 tabs → 1 redemption) |
| Replay a request | Idempotency on **meaning**, UNIQUE-constrained; survives a restart | FAILURES #45, attack `replay_after_restart` |
| Expired mandate executes | `AUTH-002`, plus the approval's own 15-minute TTL | `test_approval.py` |
| Revoked mandate executes | Persisted revocation, checked **twice** — at decision and again before payment | FAILURES #43, attack `revocation_race` |
| Illegal lifecycle jump | 14-state machine with a transition table and a predicated write | FAILURES #44, attack `illegal_state_jump` |
| Split a purchase across baskets | `SPLIT-001` — soft, scoped, resend-aware | FAILURES #40, attack `split_the_purchase` |

### 6. The gateway → REMIT

| Threat | Control | Test |
|---|---|---|
| Forged webhook | HMAC verified before state moves; forged events are **stored** with `signature_ok=0` rather than dropped | `test_chaos.py` |
| Duplicate webhook | `event_id` PRIMARY KEY — order-independent by construction | `test_concurrency.py` (12 parallel → 1 applied) |
| Out-of-order webhook | FSM allows the jumps gateways actually make, rejects the rest | FAILURES, `test_chaos.py` |
| Timeout mid-order | `UNKNOWN`, owned by the reconciler. Never guessed | `test_recovery.py` |
| Gateway and REMIT disagree | Reconciliation against gateway truth; unresolvable payments **surfaced**, not swallowed | `test_recovery.py` |

---

## What this model does not defend against

Stated because a threat model that lists only what it stops is marketing.

- **A compromised operator.** One writer holds the hash chain and can re-link
  it from any point. Fixing that needs an external witness. Named in
  `chain.py`, not solved.
- **A malicious merchant with catalog write access.** REMIT re-evaluates on
  catalog change and binds the cart hash, but a merchant who can rewrite prices
  arbitrarily can make an honest purchase expensive. The control is
  re-evaluation, not prevention.
- **Cross-tenant anything.** There is no tenancy. One `user_id` column.
- **A stolen session cookie.** httpOnly, signed, 12-hour life — and anybody
  holding it is that principal. Production needs an IdP, device binding and
  short-lived tokens.
- **Denial of service at scale.** Rate limiting is per-process memory. It
  resets on deploy and does not exist across replicas.
- **Timing side channels.** `hmac.compare_digest` is used where it matters;
  nothing else has been analysed.

---

## The assumption everything rests on

**The policy engine cannot be persuaded, because there is nothing to persuade.**
`authorize()` is a pure function of its arguments: no network, no database, no
clock of its own, no text input. It cannot be prompt-injected because it does
not read prose. It cannot be raced because it holds no state. It cannot be
bribed by a merchant because it never sees one.

Every attack in the lab therefore has to reach the *inputs* rather than the
decision — and every input has a control above, with a test beside it.

If that assumption is wrong, everything else here is decoration. It is tested
directly: `test_split.py::test_the_policy_engine_still_does_no_io` greps the
module for `db.execute`, `sqlite3`, `httpx`, `requests` and `datetime.now`.
