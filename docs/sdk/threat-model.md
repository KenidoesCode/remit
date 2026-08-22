# SDK threat model

Each threat, and **which layer handles it**. The last column is the one that
matters: if the answer is "the SDK", the mitigation is decorative, because an
attacker will not use the SDK.

| Threat | What it looks like | Handled by | How |
|---|---|---|---|
| **Stolen session** | attacker replays a valid session | server + human | revocation (`scope: "principal"`); sessions expire in 12h |
| **Forged identity** | caller asserts `user_id` | **server, structurally** | no request model has an identity field; the session signature is HMAC-verified constant-time |
| **Malicious agent** | agent proposes something unauthorised | **server** | 21 policy clauses over a compiled envelope |
| **Malicious model** | model returns `{"authorized": true}` | **server** | 13 authorization-shaped fields stripped and *reported*; the decider reads no text |
| **Wrong product** | "laptop" → laptop stand | **server** | `MATCH-001` / `MATCH-002` step up rather than auto |
| **Wrong currency** | "$2000" spent as ₹2000 | **server** | `CUR-001`; `Money` always carries a unit |
| **Overspending** | total above the stated ceiling | **server** | `CEIL-001` binds the **total**, not the line item |
| **Split spending** | three buys under the ceiling, above it together | **server** | `SPLIT-001` aggregates same-category same-ceiling in a 1h window |
| **Duplicate execution** | retry storm, double click, network retry | **server** | idempotency keyed on meaning under a UNIQUE constraint |
| **Replay of consent** | reuse a human's "yes" | **server** | approval token is single-use: `UPDATE ... WHERE used_at IS NULL` |
| **Consent for another basket** | approve cheap, buy expensive | **server** | token bound to user + intent + cart + amount + merchant + expiry |
| **Expired authority** | act on a stale mandate | **server** | `AUTH-002`; every authority has `expires_at` |
| **Revoked authority** | act after the kill switch | **server** | `AUTH-003`, checked twice per journey, wins races |
| **Cross-tenant leak** | tenant B receives tenant A's order | **server** | tenant is part of the idempotency namespace, not a filter applied after |
| **Cross-principal read** | read someone else's audit trail | **server** | 404, not 403 — whether an id exists is not confirmed |
| **Tampered audit record** | payload edited after the fact | **server + SDK** | hash chain server-side; **the SDK recomputes every hash locally** |
| **Chain rewritten wholesale** | operator re-links the entire chain | **nothing** | needs an external trust anchor. Named as an open gap, not solved |
| **MITM** | intercepting a session | TLS | the SDK warns loudly on plain `http` to a non-localhost host |
| **Credential in logs** | a session ends up in a log file | **SDK** | allow-list `toJSON()`; CLI redacts session-shaped strings from all output |
| **Rate abuse** | hammering the API | server | per-principal and per-address buckets; SDK honours `Retry-After` |
| **Supply chain** | a compromised dependency | **SDK** | **zero runtime dependencies** |

## The row worth reading twice

**Chain rewritten wholesale — handled by: nothing.**

Local hash verification proves the record is internally consistent. It does not
prove the server is honest, and no amount of client-side cryptography can, while
the server is the only witness. Fixing it needs a second party: a notary, a
published root, a counterparty who countersigns.

REMIT does not have one, and says so on every receipt via
`no_external_trust_anchor: true`.
