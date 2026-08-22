# Errors

Every error extends `RemitError`. Catch that to catch everything the SDK throws
deliberately.

```ts
import { RemitError, RemitAuthorizationError, RemitNotGroundedError } from "remit-sdk";
```

| Error | When | Retry? |
|---|---|---|
| `RemitValidationError` | your arguments were wrong; nothing was sent | no — fix the call |
| `RemitAuthenticationError` | 401/403; no usable session | no |
| `RemitAuthorizationError` | REMIT refused (`verdict`, `failed`, `clauses`) | no — this is the product working |
| `RemitRevokedError` | the authority was cancelled | never |
| `RemitExpiredError` | the envelope timed out | no — create a new intent |
| `RemitSemanticDriftError` | the cart drifted from the mandate | no |
| `RemitPolicyError` | a policy clause refused | no |
| `RemitNotGroundedError` | **nothing in the catalog answered the request** | no |
| `RemitRateLimitError` | 429, carries `retryAfterSeconds` | yes, automatic |
| `RemitNetworkError` | DNS, TCP, TLS | yes, automatic |
| `RemitTimeoutError` | client-side timeout | yes, automatic |
| `RemitAbortError` | you aborted it | never |
| `RemitExecutionError` | reached the rail, did not complete | see `replayed` |
| `RemitProtocolError` | server speaks a different protocol MAJOR | no — upgrade |

## `RemitNotGroundedError` deserves its own paragraph

"Nothing in the catalog answered this" and "the policy refused this" are
**different sentences**, and a retry policy built on the wrong one retries
forever.

The server draws the same distinction: a 422 `no_decision` rather than a `DENY`,
because the policy engine was never reached — there was no cart to decide about.
An earlier version defaulted a missing authorization to `DENY`, which reads as
*the policy refused you* when the truth was *the policy was never asked*.

```ts
try {
  await remit.intents.create({ text: "buy a submarine" });
} catch (err) {
  if (err instanceof RemitNotGroundedError) {
    err.stockedHint;   // what this shop does stock
  }
}
```

## Errors are safe to log

```ts
logger.error(err.toJSON());
```

`toJSON()` is built from an **allow-list**: `name`, `code`, `message`, `status`,
`requestId`, `detail`. A deny-list is one new field away from leaking a
credential, so there isn't one.

## Correlating with the server

```ts
err.requestId;   // sent as x-request-id on every call
```

For an authorization refusal, `err.correlationId` ties to the audit trail:

```ts
await remit.receipts.verify(err.correlationId);
```
