# Authentication

## There is no API key, and that is the design

A bearer API key is a credential that says *spend as whoever this belongs to*.
Hand one to an agent and the agent chooses whose limits it is spending. That is
the exact bug `FAILURES #32` was about, and adding a key to the SDK would put it
back one layer up.

So REMIT authenticates with **a session it signed itself**:

```
usr_<random>.<hmac-sha256(pid, server_secret)[:32]>
```

A caller cannot mint one, because a principal id they typed will not carry the
server's signature. The check is constant-time.

## Two ways to hold a session

### Let the SDK take one

```ts
const remit = new Remit({ baseUrl });
await remit.intents.create({ text: "buy a yoga mat under 2000" });
remit.session;   // the server issued one and the SDK captured it
```

The identity lives as long as the client object. Good for scripts and for
anything where accumulated history does not matter.

### Bring one

```bash
export REMIT_SESSION="$(remit session)"
```

```ts
const remit = new Remit({ baseUrl, session: process.env.REMIT_SESSION });
```

Use this when you need **one identity across processes** — exposure limits,
velocity checks, revocation and audit scoping all accumulate against a
principal, and a fresh identity per process has none of them.

The SDK sends it as `Authorization: Bearer <session>`.

> **A note on how this got fixed.** `/v1` documented Bearer support from the day
> it was written and nothing implemented it: the middleware only ever read the
> cookie. A headless client presenting a perfectly valid session was silently
> handed a **brand new principal on every call** — not an auth error, just an
> identity with zero exposure, no revocation history and an audit trail scoped
> to nobody. Building this SDK is what found it. `FAILURES #51`.

## Treat a session as a credential

The SDK never logs it. The CLI redacts anything session-shaped from **every**
output, including error bodies, via an allow-list rather than a deny-list.
`remit init` deliberately does not write it into the generated config file.

`remit session` is the one command that prints it, because that is the entire
request — and a credential tool that refuses to hand you the credential just
gets worked around with something worse. The warning goes to stderr so that
`export REMIT_SESSION=$(remit session)` still works.

## Rotating

Delete the value and take a new one. There is no revoke-this-session endpoint;
what there is, and what actually matters, is `revoke({ scope: "principal" })`,
which stops the identity from spending regardless of who holds the string.

## In production

The reference deployment mints sessions on demand, which is right for a demo and
wrong for production. A real deployment binds identity to the merchant's own
identity provider. That seam is `principal_from_upstream()` in the server and is
**deliberately unwritten** — writing it without an IdP behind it would be
theatre. See [security model](./security-model.md).
