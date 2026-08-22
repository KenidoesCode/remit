# REMIT SDK

The developer interface to the REMIT authorization protocol.

```bash
npm install remit-sdk       # library
npm i -g remit-sdk          # the `remit` CLI
```

> The npm package is **`remit-sdk`**. `remit` is taken on npm by an unrelated
> library, and printing an install command that does not work would be a strange
> way to open documentation about not overstating things. The CLI binary is
> still `remit`.

## Where things are

| | |
|---|---|
| [Installation](./installation.md) | requirements, platforms, what has actually been tested |
| [Quickstart](./quickstart.md) | ten minutes, nothing to sign up for |
| [Authentication](./authentication.md) | why there is no API key |
| [Authorization](./authorization.md) | verdicts, clauses, step-up, idempotency |
| [Revocation](./revocation.md) | the kill switch, and why it is forward only |
| [Audit and receipts](./audit.md) | verifying rather than trusting |
| [Errors](./errors.md) | every typed error and whether to retry |
| [Model independence](./model-independence.md) | bring your own model |
| [Security model](./security-model.md) | what is trusted, and what is not |
| [Threat model](./threat-model.md) | each threat and the layer that handles it |
| [Deployment](./deployment.md) | running REMIT yourself, and what is missing |

## The shape of it

```
APPLICATION -> AI AGENT -> REMIT SDK -> AUTHORIZATION PROTOCOL
                                            |
                                        AUTHORITY -> POLICY -> EXECUTION
                                            |
                                    PAYMENT PROVIDER -> AUDIT RECEIPT
```

> **No financial action may execute unless it is consistent with a valid
> authorization envelope.**

The SDK is a client of that protocol. It is **untrusted**, like the agent and
the model, and every guarantee holds equally for someone using `curl`.

## Source

`packages/sdk/` in this repository. Zero runtime dependencies.
