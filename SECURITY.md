# Security policy

## Status

REMIT is a **prototype**. The reference deployment at
`https://remit-vvug.onrender.com` runs in **Razorpay test mode** and moves no
real money. Do not put real funds behind it.

## Reporting a vulnerability

Open a **private security advisory** on GitHub:
<https://github.com/KenidoesCode/remit/security/advisories/new>

Please include what you expected, what happened, and the smallest reproduction
you have. If it moved money in test mode, the correlation id is the most useful
single thing you can send.

No bounty is offered — this is a student project, and pretending otherwise would
be its own kind of dishonesty.

## Known limitations

These are documented rather than fixed, and are not eligible as findings:

- **No external trust anchor** on the audit chain. Tamper-evident, not
  tamper-proof.
- **No identity provider.** Sessions are signed; they are not SSO or MFA.
- **A stolen session can spend** until revoked. It is a bearer credential.
- **Secrets are environment variables.** No vault, no rotation schedule.
- **One host.** Multi-host correctness is a design, not an implementation.
- **Rate limiting is in-process.** No WAF, no DDoS protection.

Full detail: `docs/THREAT_MODEL.md`, `docs/SECURITY_REPORT.md`,
`docs/sdk/security-model.md`, `docs/PRODUCTION_GAPS.md`.

## Scope

In scope: authorization bypass, identity forgery, cross-tenant leakage, double
execution, approval-token reuse, revocation bypass, audit tampering that local
verification does not catch.

Out of scope: rate limits on a free instance, the absence of an IdP, anything in
the list above.
