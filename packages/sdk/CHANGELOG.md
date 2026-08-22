# Changelog

All notable changes to `remit-sdk`. This project follows
[semantic versioning](https://semver.org/): a breaking change to the public API
requires a MAJOR bump, new capability a MINOR, and a fix a PATCH.

## [0.1.0] — 2026-08-22

First release. `0.x` on purpose: the SDK is real and the protocol behind it is
a prototype, and a `1.0` would claim a stability commitment that has not been
earned yet.

### Added
- `Remit` client with `intents`, `authorization`, `payments`, `audit` and
  `receipts` namespaces, mapped one-to-one onto `/v1` routes that exist.
- Typed errors, including `RemitNotGroundedError` — "nothing in the catalog
  answered this" is a different sentence from "the policy refused", and a
  client building a retry policy needs the right one.
- `receipts.verify()`, which **recomputes every event hash locally** rather
  than repeating the server's own claim about itself.
- `remit` CLI: `doctor`, `init`, `session`, `protocol`, `intent`, `evaluate`,
  `execute`, `revoke`, `audit`, `receipt verify`.
- ESM, CommonJS and TypeScript declarations. Zero runtime dependencies.

### Notes
- There is no `idempotencyKey` option, deliberately. REMIT derives idempotency
  server-side from the meaning of a request, so a caller-supplied key could
  only ever be weaker. See `docs/sdk/authorization.md`.
- There is no API key, deliberately. A bearer key would let a caller choose
  whose limits to spend.
