/**
 * `remit.payments` — the money.
 *
 * One method, because the protocol has one entry point to the rail. Everything
 * that can move money goes through the same journey the website uses; there is
 * no second code path, which is the only reason the guarantee a reviewer
 * verifies on the site is the guarantee an integrator gets.
 */

import { RemitValidationError } from "./errors.js";
import type { RequestOptions, Transport } from "./http.js";
import { liftNotGrounded } from "./intent.js";
import type { ExecutionResult } from "./types.js";

export interface ExecuteParams {
  /** The original human sentence. Required — authority is bound to the words. */
  text: string;
  intentId?: string;
  /** A token from a step-up, when the human has said yes. */
  approvalToken?: string;
  /**
   * What the agent may accept from the merchant.
   * `"in_envelope"` (default) accepts only what the authority already covers.
   */
  acceptOffers?: "in_envelope" | "none";
}

export class Payments {
  constructor(private readonly http: Transport) {}

  /**
   * Do it, if the policy allows.
   *
   * ON RETRIES AND DOUBLE CHARGING
   * ------------------------------
   * This is safe to retry, and not because the SDK is careful — because the
   * protocol makes it safe. Idempotency is derived server-side from the
   * meaning of the request under a UNIQUE constraint, so the same sentence
   * producing the same basket at the same price is the same purchase. A retry
   * collapses onto the first payment and returns `execution.replayed === true`.
   *
   * Always check `replayed`. It is part of the contract: it is how you know
   * the payment in front of you is the one you already made.
   *
   * There is no `idempotencyKey` option, on purpose. A key you choose could be
   * reused across two different purchases; a key the SDK generates per call
   * would make every retry a NEW purchase and defeat the deduplication
   * entirely. Either would be a weaker guarantee wearing a familiar name.
   */
  async execute(params: ExecuteParams, options?: RequestOptions): Promise<ExecutionResult> {
    if (typeof params?.text !== "string" || params.text.trim() === "") {
      throw new RemitValidationError(
        "payments.execute needs the original human sentence: an authority is " +
          "bound to the words that created it.",
        { code: "invalid_argument" },
      );
    }
    try {
      const res = await this.http.request<ExecutionResult>(
        "POST",
        "/v1/execute",
        {
          utterance: params.text,
          intent_id: params.intentId ?? null,
          approval_token: params.approvalToken ?? null,
          accept_offers: params.acceptOffers ?? "in_envelope",
        },
        options,
      );
      return res.data;
    } catch (err) {
      throw liftNotGrounded(err);
    }
  }
}
