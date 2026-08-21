# Interaction map

Every interactive element, what it calls, and what real state it reflects.
**No element here is decorative. If it moves, it is showing you data.**

## The neighbourhood canvas

| element | data source | behaviour |
|---|---|---|
| home node | the intent envelope | pulses once when a new intent is compiled |
| catalog field | `/api/catalog` — real product count | node density is the real category size |
| route path | `/api/graph` — real intent-graph events | draws node by node as the journey executes |
| property line | `env.ceiling_paise` | a hard vertical edge; draggable in Act III |
| destination | payment state | fills only when a payment is actually created |
| route collision | policy verdict | on DENY/STEP_UP the path stops **at** the line |

Canvas 2D, not WebGL. Reason in DECISIONS: this is a graph of ~12 nodes and one
boundary. WebGL would add a dependency, a loading state and a fallback path to
draw twelve circles. The cost is real and the benefit is zero.

## The property line (the signature interaction)

| gesture | call | shown |
|---|---|---|
| drag the line | `POST /api/replay` | verdict, clause chain, drift vector, engine latency |
| release | — | the route re-animates to the new boundary |
| keyboard `←/→` | same | same — the interaction is fully keyboard-operable |

`/api/replay` re-runs `authorize()` against a modified envelope. **No LLM call,
no payment, no writes.** The response carries `engine_us` — the actual
microseconds the pure function took — which is displayed.

## Break it

| lever | inject | what it exercises |
|---|---|---|
| price +25% | `price` | `price_change` drift, `CAT-001` |
| shipping ₹799 | `shipping` | `shipping` drift, `CEIL-001` |
| delist product | `delist` | `STOCK-001` |
| revoke intent | `revoked` | `AUTH-003` |
| expire intent | `expire` | `AUTH-002` |
| inflate quantity | `qty` | `quantity` drift |
| inject a prompt | utterance | the injection defence |
| duplicate webhook | `/api/webhook` ×2 | event dedupe |
| out-of-order webhook | `/api/webhook` | state machine legality |
| forged signature | `/api/webhook` | HMAC verification |

Each returns the real response. The panel shows which clause caught it.

## With / without

`POST /api/compare` runs the identical journey twice — once with the default
policy, once with a permissive one — and returns both. Revenue, AOV, drift,
unauthorised value, confirmations, verdict. Side by side, same colours, no
editorialising.

## Failure lab

`GET /api/failures` parses `FAILURES.md` at runtime. The page cannot drift from
the document, because it *is* the document.

## Easter eggs

| trigger | payload | why it is not a gimmick |
|---|---|---|
| DevTools console on load | build banner + one line about fuel | the audience that opens a console is exactly the audience that should find it |
| a perfect-fit purchase (drift 0.0, ≥95% of the envelope used) | a small 😎 next to the total | rare, earned, and it marks a genuinely optimal outcome |
| `?debug=1` or pressing `\`` | live HUD: engine latency, clause count, catalog version, chain head | it is a real debug HUD with real telemetry |
| the five payment states | shown as five, never counted aloud | it was already five |

## Accessibility, non-negotiable

- Opening skips on `prefers-reduced-motion` and on any keypress.
- The property line is a `role="slider"` with `aria-valuenow` in rupees, operable
  by arrow keys.
- Canvas has a text alternative describing the route and the verdict.
- Every act is reachable by keyboard; focus is visible everywhere.
- Colour is never the only signal: every state also has a word.
- The whole product works with JavaScript motion disabled.
