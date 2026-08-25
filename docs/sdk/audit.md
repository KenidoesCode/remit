# Audit and receipts

## The authorization receipt

One view of a decision — authority, decision, execution and audit — assembled
from records that already exist. It is the fastest way to answer "what was
authorised, what happened, and can I check it" for a single transaction.

```ts
const r = await remit.receipts.get(correlationId);

r.intent.text;              // "buy a laptop under 50000"
r.authority.ceiling;        // { paise: 5000000, display: "₹50,000.00" }
r.decision.verdict;         // "AUTO" | "STEP_UP" | "DENY"
r.decision.failed_clauses;  // e.g. ["MATCH-001"] on a step-up
r.execution.money_moved;    // false for a step-up or a denial
r.execution.order_id;       // the real Razorpay test-mode order, or null
r.self_reported_chain;      // "intact" — the chain's own view
```

A denial or a step-up produces a receipt too, and it says `money_moved: false`
and names the clause that stopped it. That is the failure case, explained —
nothing was hidden because nothing moved.

The receipt **reports** the chain's integrity; it does not prove it. To prove
it, verify:


## Getting the record

```ts
const evidence = await remit.audit.get(correlationId);
evidence.events;              // every event in this trace, ordered
evidence.decision;            // the decision that was made
evidence.authority_history;   // the authority state machine's path
evidence.chain_intact;        // the SERVER's claim about itself
```

Scoped to you. An audit trail carries the sentence somebody typed, so reading
another principal's returns 404.

## Verifying rather than trusting

```ts
const receipt = await remit.receipts.verify(correlationId);
receipt.ok;
receipt.checks;   // named checks, each with passed + detail
```

`verify()` **recomputes every event hash locally**:

```
sha256( prev_hash + canonical({kind, trace_id, ts, payload}) )
```

where `canonical` is the server's exact encoding: sorted keys, no whitespace,
non-ASCII escaped. It does **not** simply echo `chain_intact`.

Checks that run:

| Check | What it proves |
|---|---|
| `has_events` | the record is not empty |
| `sequence_monotonic` | nothing was reordered |
| `trace_scoped` | every event belongs to this correlation id |
| `hashes_recomputed` | **each payload matches its own hash** |
| `server_reports_chain_intact` | the server's end-to-end chain check |
| `decision_present` | the record actually carries a verdict |

A check that could not run is reported as **failed**, never skipped. If the
server is too old to return `prev_hash`, you get `ok: false` and a message
saying why — not a green tick over an absence.

## What this proves, and what it does not

It detects a payload **edited after the fact**. That is real, and it is tested:
`test/integration.test.js` tampers with a payload and asserts verification
fails, so the verifier is not passing vacuously.

It does **not** prove the server is honest. The chain has **no external trust
anchor**. An operator who controls the whole chain can rewrite it from any point
and re-link every hash consistently, and this check would pass.

Every result carries `no_external_trust_anchor: true` for that reason. It is a
field rather than a footnote because footnotes get skipped.

## A note on why this works at all

Python writes a float `0.0` as `0.0`. `JSON.parse` turns it into the JavaScript
number `0`, and `0` and `0.0` are the same value — the literal is gone before
any encoder can see it. Re-serialising produces `0`, the bytes differ, and every
hash over a payload containing a whole-number float fails.

REMIT's `DRIFT_MEASURED` payload is twelve dimensions that are almost always
exactly `0.0`, so this was *every* receipt. The first version of the verifier
reported `hash mismatch at seq 38` against a completely intact chain.

The SDK therefore parses audit responses with a **number-literal-preserving
parser** (`src/canonical.ts`) and re-emits each number as the exact characters
it arrived as. Nothing is normalised and nothing is guessed.

A verifier that cries tampering on healthy data is worse than no verifier: it
trains you to ignore it.
