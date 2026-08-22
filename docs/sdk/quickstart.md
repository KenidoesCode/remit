# Quickstart

Ten minutes, start to finish, with nothing to sign up for.

## 1. Install

```bash
npm install remit-sdk
```

## 2. Connect

```ts
import { Remit } from "remit-sdk";

const remit = new Remit({ baseUrl: "https://remit-vvug.onrender.com" });
console.log(await remit.health());
```

No API key. Identity is a session the server signs on your first call — see
[authentication](./authentication.md) for why there is no key.

## 3. Turn a human sentence into an authority

```ts
const { intent, authority } = await remit.intents.create({
  text: "buy a yoga mat under 2000",
});

intent.ceiling;      // { amount_paise: 200000, currency: "INR" }
intent.requested;    // ["yoga mat"]
authority.expires_at // authority is bounded in time, not just in amount
```

Note what you did **not** pass: who is asking. Identity comes from the session.
A field a caller can set is a field a caller can set to somebody else's.

## 4. Ask before doing

```ts
const decision = await remit.authorization.evaluate({ text: intent.utterance });

decision.verdict;   // "AUTO" | "STEP_UP" | "DENY"
decision.failed;    // clause ids that refused
decision.clauses;   // all 21, each with passed + detail
```

`evaluate` moves no money, and that is enforced server-side rather than
promised: it runs on a throwaway instance, so "this is only a question" cannot
be answered by doing the thing.

## 5. See it refuse

```ts
const overreach = await remit.authorization.evaluate({
  text: "buy a laptop under 50000",
});
// verdict: "STEP_UP", failed: ["MATCH-001"]
```

The shop's closest match to "laptop" is a laptop **stand** at ₹4,446. A spending
limit approves it — it is well under ₹50,000. REMIT does not, because a modifier
match is not a product match. That gap is the entire product.

## 6. Execute

```ts
if (decision.verdict === "AUTO") {
  const result = await remit.payments.execute({ text: intent.utterance });
  result.execution.payment_id;
  result.execution.replayed;   // ALWAYS check this
}
```

## 7. Verify the receipt

```ts
const receipt = await remit.receipts.verify(result.decision.correlation_id);
receipt.ok;        // every check passed
receipt.checks;    // including hashes recomputed locally
```

## 8. Revoke

```ts
await remit.authorization.revoke({ reason: "user pressed stop" });
```

Forward only. The next purchase comes back `DENY` / `BLOCKED`.

## Next

- [Authorization](./authorization.md) — verdicts, clauses, step-up, idempotency
- [Security model](./security-model.md) — what is trusted, and what is not
- [Model independence](./model-independence.md) — bring your own model
