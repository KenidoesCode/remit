# Security model

The one sentence:

> **The SDK is untrusted. The agent is untrusted. The model is untrusted. The
> client is untrusted. The server verifies authority.**

## What is trusted

| Component | Trusted? | Why |
|---|---|---|
| Your application | **no** | it runs on a machine you may not control |
| Your AI agent | **no** | it can be prompted, jailbroken, or simply wrong |
| The model | **no** | its output is data, not instruction |
| This SDK | **no** | an attacker would not use it |
| The merchant catalog | **no** | prices and names are inputs, not facts |
| The network | **no** | TLS is assumed, nothing else is |
| **The REMIT server** | **yes** | it is the only place a boundary can live |

The SDK being untrusted is the important row. It is a convenience over an HTTP
protocol, and **every guarantee in this document holds for a caller using
`curl`**. If a guarantee only held for SDK users, an attacker would simply not
use the SDK.

## What the SDK cannot do

By construction, not by policy:

- **It cannot mark an action authorized.** No request model has a verdict field,
  an `authorized` boolean or an amount-already-approved. There is nowhere to
  put one.
- **It cannot choose an identity.** No request model has a `user_id`. Identity
  is the session signature.
- **It cannot set a tenant.** Same reason. A tenant a caller can set is a tenant
  a caller can set to somebody else's.
- **It cannot approve on the human's behalf.** `CAN_APPROVE = {HUMAN}`.
- **It cannot skip the policy engine.** `/v1` is a projection over the same
  journey the website uses. There is no second code path — asserted by a test
  that greps `v1.py` for `authorize(`, `create_order` and `compute_drift(`.

## The model cannot authorize money

REMIT's decider is a **pure function**: no I/O, no network, no clock, no text
input. It reads a compiled envelope, not a sentence.

A model is asked what the human *meant*. If it returns
`{"verdict": "AUTO", "authorized": true}`, the server strips **13
authorization-shaped fields** and records that they were sent. The attack
`model_self_authorises` exists to prove it.

This is why putting an LLM judge in front of an LLM agent is not defence in
depth: it puts two persuadable systems in series. REMIT's decider is not
persuadable because it cannot read.

## What the SDK does locally

Small, and deliberately so — anything that mattered would belong on the server:

- **Argument validation**, so obvious mistakes fail fast without a round trip.
- **Credential hygiene**: sessions are never logged; error `toJSON()` is an
  allow-list; the CLI redacts session-shaped strings from all output.
- **Receipt verification**: every event hash is recomputed locally rather than
  taking the server's `chain_intact` at face value.
- **Protocol version checking**, refusing a different MAJOR rather than guessing.

## What this does not defend against

Named, because a security document that only lists strengths is marketing:

- **No external trust anchor.** The audit chain is hash-linked. An operator who
  controls the whole chain can rewrite it from any point and re-link every hash
  consistently, and local verification would pass. Tamper-**evident** against
  partial edits; **not tamper-proof**.
- **No identity provider.** A signed session is a real identity boundary. It is
  not SSO, not MFA, not federated.
- **A stolen session spends.** It is a bearer credential over TLS. Mitigation is
  revocation, which is why revocation is the most-retried call in the SDK.
- **No secret management.** Environment variables. No vault, no rotation
  schedule, no envelope encryption.
- **One host.** Multi-process correctness is tested. Multi-*host* is a design.
- **The reference deployment is a prototype** on a free instance, rate limited,
  in **Razorpay test mode**. No real money moves.

## Reporting a vulnerability

See [SECURITY.md](../../SECURITY.md).
