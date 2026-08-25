# Limitations

Stated plainly, because a system that overstates what it is cannot be trusted
about what it measures.

## Payments

- **UPI Reserve Pay / SBMD is not available in Razorpay test mode.** REMIT does
  not simulate a mandate and call it real. The authorisation model here is
  REMIT's own intent envelope, which is a *different* thing from a UPI mandate
  and is described as such everywhere.
- **No money moves.** The default gateway is an offline fake. The live path uses
  Razorpay test mode and refuses any key that does not begin with `rzp_test_`.
- **Razorpay documents idempotency keys for RazorpayX payouts only.** Core
  Orders/Payments dedupe via the Order `receipt` field, capped at 40 characters,
  which is what this uses.
- Settlements, refunds, disputes and Route are **not** implemented. They are real
  products with real semantics and a half-version would be worse than none.

## The audit chain

- A hash chain with a **single writer** proves *ordering* and detects tampering
  after the fact. It is **not** non-repudiation: an operator who controls the
  whole chain can rewrite it from any point and re-link. Real non-repudiation
  needs an external witness, and that is not implemented.

## The AI layer

- The default intent compiler is **deterministic, not an LLM**. That is a
  choice: the whole evaluation must be reproducible on any machine with no API
  key. An LLM compiler exists behind the same Protocol and degrades to the rule
  compiler on any failure — always toward *more* friction, never more autonomy.
- **Calibration is fitted on a few hundred labels.** The isotonic map is a step
  function and could overfit. Dev ECE is the only check on it.
- No fine-tuning, no retrieval, no vector store. None of them would improve a
  60-SKU closed-world catalog, and exact matching is auditable in a way that
  embeddings are not.

## The evaluation

- **Synthetic corpus, written by the author.** Believe the shape, not the
  numbers.
- **The adversarial bucket is my imagination.** Prompt injection is measured
  against payloads I wrote.
- Revenue figures are the merchant value of *simulated* baskets in a *fictional*
  catalog. They are internally consistent and comparable across arms; they are
  not a forecast for anyone's business.

## Product scope

- One buyer, one currency (INR), one country's assumptions.
- No accounts, no auth, no multi-tenant isolation. The Control screen would be a
  data leak in any real deployment.
- The UI is a single-page vanilla application, not the React/WebGL build
  originally specified. See DECISIONS.md ADR-018 for why, and what it costs.
