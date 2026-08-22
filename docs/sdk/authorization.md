# Authorization

## Three verdicts

| Verdict | Meaning | What your code should do |
|---|---|---|
| `AUTO` | inside the envelope | proceed |
| `STEP_UP` | a human must say yes | ask a person, not your agent |
| `DENY` | refused | stop; show `failed` and `reason` |

There is no fourth, and there is no "allowed with a warning".

## Decisions are returned, not thrown

REMIT refusing is the product working, so `evaluate()` hands you the decision:

```ts
const d = await remit.authorization.evaluate({ text });
if (d.verdict === "AUTO") { /* ... */ }
```

If you prefer exceptions, opt in:

```ts
import { assertAllowed, RemitAuthorizationError, RemitRevokedError } from "remit-sdk";

try {
  assertAllowed(decision);
} catch (err) {
  if (err instanceof RemitRevokedError) { /* the kill switch was pulled */ }
  else if (err instanceof RemitAuthorizationError) { err.failed; err.verdict; }
}
```

## Clauses

Every decision carries the clauses behind it, so you can show a person *why*
rather than "computer says no":

```ts
decision.clauses.filter(c => !c.passed);
// [{ clause_id: "MATCH-001", passed: false, detail: "..." }]
```

Clause ids are stable across versions. `detail` is human-readable and is not.

The ones you will meet most:

| Clause | Fires when |
|---|---|
| `CEIL-001` | the total exceeds the ceiling the human stated |
| `MATCH-001` | the match is on a modifier, not the head noun (laptop **stand**) |
| `MATCH-002` | the match is semantic only |
| `CUR-001` | the currency is not one the envelope allows |
| `SPLIT-001` | separate purchases aggregate past a ceiling in a window |
| `AUTH-002` | the envelope expired |
| `AUTH-003` | the authority was revoked |
| `DRIFT-001` / `DRIFT-002` | the cart drifted from what was authorised |

## Why every call needs the original sentence

```ts
await remit.payments.execute({ text: "buy a yoga mat under 2000" });   // yes
await remit.payments.execute({ intentId: "int_123" });                 // refused
```

**An authority is bound to the words that created it.** Executing against an id
alone would let a caller reuse somebody's mandate for a different request. The
SDK refuses this locally, before sending anything, so you get a clear error
instead of a server round trip.

## Step-up

```ts
const ask = await remit.authorization.stepUp({ text });
if (ask.required) {
  ask.asking.why;      // what to put in front of the person
  ask.asking.amount;   // Money
  ask.asking.items;    // what is in the basket
}
```

Then, when the human says yes:

```ts
await remit.authorization.approve({ text, approvalToken: ask.approval.token });
```

The token is **single-use** and bound to the person, the intent, the basket, the
amount, the merchant and an expiry. Presenting it for a different basket does
not work — which is why it is a token and not a boolean.

**An agent cannot approve.** `CAN_APPROVE` is `{HUMAN}`. An agent that could
approve the step-up it triggered has not been stopped by anything; the step-up
would be a formality with a round trip in it.

## Idempotency, and why there is no idempotency key

`payments.execute()` is safe to retry. Not because the SDK is careful — because
the protocol makes it safe. Idempotency is derived **server-side from the
meaning of the request**:

```
H( tenant : user : semantic_hash | cart_signature | total | catalog_version )
```

under a UNIQUE constraint, which is the serialisation point. The same sentence
producing the same basket at the same price is the same purchase, so a retry
collapses onto the first payment:

```ts
const a = await remit.payments.execute({ text });
const b = await remit.payments.execute({ text });
b.execution.payment_id === a.execution.payment_id;   // true
b.execution.replayed;                                // true
```

**Always check `replayed`.** It is how you know the payment in front of you is
the one you already made rather than a second one.

This is asserted by a test that runs against a real server
(`test/integration.test.js`, *"the same purchase twice is one payment"*). If it
ever fails, retries on execute must be turned **off**, not explained.

### Why no `idempotencyKey` option

- A key **you** choose can be reused across two genuinely different purchases.
- A key the **SDK** generates per call would make every retry a *new* purchase
  and defeat the deduplication entirely.

Either would be a weaker guarantee wearing a familiar name, so the SDK does not
offer one.

## Retries

| Condition | Retried |
|---|---|
| connection error, timeout | yes |
| 408, 429, 502, 503, 504 | yes |
| 4xx other than the above | **no** — retrying a rejected request just rejects again |
| aborted via `AbortSignal` | **no** |

Exponential backoff with jitter, and the server's `Retry-After` wins when it
sends one. Revocation gets **more** retries than anything else, because failing
to revoke is the worse outcome.

```ts
await remit.payments.execute({ text }, { retry: { retries: 0 } });   // opt out
await remit.payments.execute({ text }, { timeoutMs: 5000, signal });
```
