# Revocation

The kill switch.

```ts
await remit.authorization.revoke({ reason: "user pressed stop" });
```

## Two scopes

```ts
// everything for this principal
await remit.authorization.revoke({ scope: "principal", reason: "..." });

// one mandate
await remit.authorization.revoke({ scope: "intent", intentId: "int_...", reason: "..." });
```

`principal` is the default, because when someone reaches for a kill switch they
usually mean *all of it*.

## Forward only

There is no un-revoke in the protocol, so there is none in the SDK. **A kill
switch you can undo is a kill switch you have to reason about under pressure.**

## It wins races

Revocation is checked **twice per journey** — once early, once before execution.
A payment in flight when the revocation lands does not complete: the server
asserts that no payment exists dated after the revocation
(attack `revocation_race`).

## It gets more retries than anything else

```ts
// inside the SDK
{ retry: { retries: 4 } }
```

Every other call defaults to 2. Failing to revoke is the worse outcome, so this
one tries harder.

## What a revoked principal sees

```ts
const after = await remit.payments.execute({ text });
after.decision.verdict;      // "DENY"
after.decision.failed;       // ["REVOKED"]
after.execution.state;       // "BLOCKED"
after.revocation;            // who cancelled it and when
```

The `clauses` array is **empty**, and that is deliberate. The journey stops
before the policy engine is asked, so claiming a clause fired would be inventing
evidence.

## Checking state without acting

```ts
import { isSpent, whyUnusable } from "remit-sdk";

const { authority } = await remit.authorization.get("int_...");
isSpent(authority);      // revoked OR expired
whyUnusable(authority);  // "revoked at 2026-08-22T..." | "expired at ..." | null
```

## You can only revoke your own

Revoking someone else's authority returns 404 — not 403, because whether an id
exists is not something this endpoint should confirm. Attack:
`revoke_someone_else`.
