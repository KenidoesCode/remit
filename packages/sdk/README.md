# remit-sdk

**The authorization boundary between autonomous agents and financial action.**

Your agent can be as intelligent as you like. Your authorization should still be
explicit.

```bash
npm install remit-sdk
```

```bash
npm i -g remit-sdk    # the `remit` CLI
```

> The npm package is `remit-sdk`. The name `remit` was already taken on npm by
> an unrelated microservices library, and shipping an install command that does
> not work would be a strange way to open a README about not overstating things.
> The CLI binary is still `remit`.

---

## What REMIT is

REMIT is **not an agent** and **not a payment provider**. It is the layer that
decides whether what an agent proposes is something a human actually authorised.

```
APPLICATION -> AI AGENT -> REMIT SDK -> AUTHORIZATION PROTOCOL
                                            |
                                        AUTHORITY -> POLICY -> EXECUTION
                                            |
                                    PAYMENT PROVIDER -> AUDIT RECEIPT
```

One invariant holds the whole thing up:

> **No financial action may execute unless it is consistent with a valid
> authorization envelope.**

## Why it exists

A spending limit can only ask *how much*. It cannot ask *is this the thing the
human asked for*.

Say a person tells their agent **"buy a laptop under ₹50,000"**. The shop's
closest match is a **laptop stand** at ₹4,446. Every spending limit in existence
approves that purchase — it is well under ₹50,000. REMIT refuses it, because
`MATCH-001` says a modifier match is not a product match:

```
verdict    STEP_UP
because    awaiting human confirmation
failed     MATCH-001
```

That is the entire product, and you can run it yourself in ten seconds:

```bash
npx remit-sdk evaluate "buy a laptop under 50000"
```

## Install and use

```bash
npm install remit-sdk
```

```ts
import { Remit } from "remit-sdk";

const remit = new Remit({ baseUrl: "https://remit-vvug.onrender.com" });

// 1. what the human said becomes a bounded authority
const { intent } = await remit.intents.create({ text: "buy a yoga mat under 2000" });

// 2. ask before doing. no money moves.
const decision = await remit.authorization.evaluate({ text: intent.utterance });

// 3. act only if you may
if (decision.verdict === "AUTO") {
  const result = await remit.payments.execute({ text: intent.utterance });
  console.log(result.execution.payment_id, "replayed:", result.execution.replayed);

  // 4. check the receipt rather than trusting it
  const receipt = await remit.receipts.verify(result.decision.correlation_id);
  console.log("verifies:", receipt.ok);
}
```

Works in ESM and CommonJS, ships TypeScript declarations, and has **zero runtime
dependencies**.

```js
const { Remit } = require("remit-sdk");
```

## How authorization works

Three verdicts, and there is no fourth:

| Verdict | Meaning |
|---|---|
| `AUTO` | inside the envelope. proceed. |
| `STEP_UP` | a **human** must say yes. an agent cannot approve this. |
| `DENY` | refused, with the clauses that refused it. |

Every decision carries the clauses behind it, so you can show a person *why*:

```ts
decision.verdict;   // "STEP_UP"
decision.failed;    // ["MATCH-001"]
decision.clauses;   // 21 clauses, each with passed + detail
decision.reason;    // human-readable
```

REMIT refusing is the product working, so `evaluate()` **returns** the decision
rather than throwing. If you prefer exceptions:

```ts
import { assertAllowed, RemitAuthorizationError } from "remit-sdk";
try {
  assertAllowed(decision);
} catch (err) {
  if (err instanceof RemitAuthorizationError) console.log(err.failed);
}
```

## Bring your own model

The SDK depends on no AI provider. There is no `RemitGPT` and there never will
be. Use OpenAI, Anthropic, Gemini, a local Llama, or a deterministic function —
whatever produces the proposal crosses the same boundary:

```ts
const proposal = await yourModel(humanSaid);   // any model, any vendor
const decision = await remit.authorization.evaluate({ text: proposal });
```

**The model is untrusted input, not a participant in the decision.** REMIT's
policy engine reads no free text at all: it is a pure function over a compiled
envelope. A model cannot return `{"authorized": true}` and be believed, because
there is nowhere in the protocol to put it — the server strips 13
authorization-shaped fields and records that they were sent.

## Integrating an agent

See [`examples/autonomous-agent.mjs`](./examples) for the honest path and
[`examples/malicious-agent.mjs`](./examples) for an agent that tries to
overspend, buy the wrong product, add accessories, switch currency, split the
purchase, replay a payment, spend after revocation, read another principal's
audit trail, and forge an identity.

Run it against a live server and read what it prints. It reports failures rather
than asserting a happy ending.

## Revoking authority

```ts
await remit.authorization.revoke({ reason: "user pressed stop" });          // everything
await remit.authorization.revoke({ scope: "intent", intentId: "int_..." }); // one mandate
```

**Forward only.** There is no un-revoke in the protocol, so there is none in the
SDK. Revocation gets more retries than any other call, because failing to revoke
is the worse outcome.

## Receipts

```ts
const receipt = await remit.receipts.verify(correlationId);
receipt.ok;                        // every check passed
receipt.checks;                    // each check, named, with its result
receipt.no_external_trust_anchor;  // always true — see below
```

`verify()` **recomputes every event hash locally**:

```
sha256( prev_hash + canonical({kind, trace_id, ts, payload}) )
```

It does not repeat the server's own `chain_intact` claim, and it will not return
`ok: true` when it could not run the check.

**What this does not prove.** The chain is hash-linked with **no external trust
anchor**. An operator who controls the whole chain can rewrite it from any point
and re-link every hash consistently, and this check would pass. It is
tamper-**evident** against partial edits and is **not tamper-proof**. That is
stated on every result rather than in a footnote.

## Idempotency — read this before retrying a payment

`payments.execute()` is safe to retry, and not because the SDK is careful.
REMIT derives idempotency **server-side from the meaning of the request**:

```
H( tenant : user : semantic_hash | cart_signature | total | catalog_version )
```

under a UNIQUE constraint. The same sentence producing the same basket at the
same price is the same purchase, so a retry collapses onto the first payment:

```ts
const result = await remit.payments.execute({ text });
if (result.execution.replayed) {
  // this is the payment you already made, not a second one
}
```

**Always check `replayed`.** It is part of the contract.

There is deliberately **no `idempotencyKey` option**. A key you choose could be
reused across two different purchases; a key the SDK generated per call would
make every retry a *new* purchase and defeat the deduplication entirely. Either
would be a weaker guarantee wearing a familiar name.

## Authentication

**There is no API key, on purpose.** A bearer key is a credential that says
"spend as whoever this belongs to" — exactly the bug that lets a caller choose
whose limits to spend.

Identity is a session the server signs. Either let the SDK take one:

```ts
const remit = new Remit({ baseUrl });   // server issues a session on first call
```

or bring one, to keep a single identity across processes so exposure limits,
revocation and audit history accumulate against it:

```bash
export REMIT_SESSION="$(remit session)"
```

```ts
const remit = new Remit({ baseUrl, session: process.env.REMIT_SESSION });
```

A session is a credential. The SDK never logs it, the CLI redacts anything
session-shaped from every output, and `remit init` never writes it to a file.

## CLI

```bash
npm i -g remit-sdk

remit --help
remit doctor                                    # node, sdk, endpoint, protocol, identity
remit init                                      # remit.config.json, no secrets in it
remit protocol                                  # what the server says it is
remit intent   "buy a yoga mat under 2000"
remit evaluate "buy a laptop under 50000"       # exit 2 when not AUTO
remit execute  "buy a yoga mat under 2000"
remit revoke --reason "stop"
remit audit <correlation-id>
remit receipt verify <correlation-id>
```

Every command adds `--json` for machine-readable output and `--url` to point at
your own deployment.

```
REMIT DOCTOR
------------

  ok   Node.js  v22.22.2
  ok   SDK  remit-sdk 0.1.0
  ok   fetch  available
  ok   API reachable  https://remit-vvug.onrender.com
  ok   Protocol compatible  server 1.0, SDK speaks 1.x
  ok   Identity  session issued by the server

REMIT IS READY.
```

## Running REMIT yourself

The SDK talks to any REMIT deployment. To run one locally:

```bash
git clone https://github.com/KenidoesCode/remit
cd remit
pip install -r requirements.txt
python -m uvicorn remit.api:api --port 8099
```

```bash
remit --url http://127.0.0.1:8099 doctor
```

## Errors

Every error is typed and every one is safe to log — `toJSON()` is built from an
allow-list, not by deleting known-bad keys.

`RemitAuthenticationError`, `RemitAuthorizationError`, `RemitValidationError`,
`RemitRevokedError`, `RemitExpiredError`, `RemitSemanticDriftError`,
`RemitPolicyError`, `RemitNetworkError`, `RemitTimeoutError`, `RemitAbortError`,
`RemitExecutionError`, `RemitRateLimitError`, `RemitNotGroundedError`,
`RemitProtocolError`.

`RemitNotGroundedError` is worth knowing about: "nothing in the catalog answered
this" is a different sentence from "the policy refused", and a retry policy
built on the wrong one retries forever.

## Requirements

- **Node.js >= 18.17** — the SDK uses the built-in `fetch`, `AbortController`
  and `crypto.subtle`.
- Windows, macOS and Linux; x64 and arm64. The CLI uses only `node:fs`,
  `node:path` and `node:process` — no shelling out, no bash, no path separators
  assembled by hand.

Verified on Linux x64 / Node 22, and on Windows 11 / PowerShell / Node 24
during a real publish — which found two shell-portability bugs that Linux had
hidden (see `FAILURES #53`). Both are fixed by removing the shell from the build
and test scripts entirely. **macOS has not been run**; a CI matrix covering it
is committed but has not executed yet.

## Versioning

Semantic versioning. A breaking change to the public API requires a MAJOR bump.
The SDK targets protocol `/v1` explicitly and refuses to run against a different
protocol MAJOR rather than guessing.

`0.x` is deliberate: the SDK is real, the protocol behind it is a prototype, and
a `1.0` would claim a stability commitment that has not been earned.

## Limitations

Stated plainly, because a package that hides these is a package you find out
about later:

- **The REMIT reference deployment is a prototype** running in **Razorpay test
  mode**. No real money moves through it.
- **The audit chain has no external trust anchor.** Tamper-evident, not
  tamper-proof.
- **No identity provider.** The session is a real, signed identity boundary; it
  is not SSO, MFA or federated.
- **One host.** Multi-process correctness is tested; multi-*host* is a design,
  not an implementation.
- **The reference deployment is rate limited** and runs on a free instance.
- REMIT is **not affiliated with or endorsed by Razorpay.** It integrates with
  Razorpay's test-mode API, which is an integration, not a relationship.

## Links

- Source: <https://github.com/KenidoesCode/remit>
- SDK docs: <https://github.com/KenidoesCode/remit/tree/main/docs/sdk>
- Live demo: <https://remit-vvug.onrender.com>

MIT © Pranauv Shrinaath S. ([techuilaguy](https://github.com/KenidoesCode))
