/**
 * remit-sdk — the authorization boundary between autonomous agents and
 * financial action.
 *
 * REMIT is not an agent and not a payment provider. Your agent can be as
 * intelligent as you like; REMIT decides what it is allowed to do.
 *
 *   import { Remit } from "remit-sdk";
 *
 *   const remit = new Remit({ baseUrl: "https://remit-vvug.onrender.com" });
 *   const { intent } = await remit.intents.create({ text: "buy a yoga mat under 2000" });
 *   const decision = await remit.authorization.evaluate({ text: intent.utterance });
 *   if (decision.verdict === "AUTO") {
 *     const result = await remit.payments.execute({ text: intent.utterance });
 *   }
 */

export { Remit, type RemitOptions } from "./client.js";
export { Intents } from "./intent.js";
export { Authorization, assertAllowed } from "./authorization.js";
export { Payments, type ExecuteParams } from "./execution.js";
export { Audit, Receipts, verifyEvidence, canonicalJson, recomputeHash } from "./audit.js";
export { RawNumber, parsePreservingNumbers, toPlain } from "./canonical.js";
export { isSpent, whyUnusable } from "./revocation.js";
export {
  SDK_VERSION,
  API_PREFIX,
  SUPPORTED_PROTOCOL_MAJOR,
  assertCompatible,
  serverIsAhead,
} from "./protocol.js";
export type { RetryPolicy, RequestOptions } from "./http.js";
export * from "./errors.js";
export type * from "./types.js";
