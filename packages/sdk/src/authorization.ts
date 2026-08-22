/**
 * `remit.authorization` — asking whether an action is permitted.
 *
 * The important one is `evaluate()`: an agent that wants to know whether it MAY
 * do something should not have to risk a payment to find out. The server runs
 * it on a throwaway instance, so "this is only a question" cannot be answered
 * by doing the thing.
 */

import {
  RemitAuthorizationError,
  RemitError,
  RemitExpiredError,
  RemitRevokedError,
  RemitSemanticDriftError,
  RemitValidationError,
} from "./errors.js";
import type { RequestOptions, Transport } from "./http.js";
import { liftNotGrounded } from "./intent.js";
import type {
  AuthorizationState,
  Decision,
  Evaluation,
  ExecutionResult,
  StepUp,
} from "./types.js";

function requireUtterance(text: unknown, method: string): string {
  if (typeof text !== "string" || text.trim() === "") {
    throw new RemitValidationError(
      `${method} needs the original human sentence. An authority is bound to ` +
        `the words that created it; acting on an id alone would let a caller ` +
        `reuse somebody's mandate for a different request.`,
      { code: "invalid_argument" },
    );
  }
  return text;
}

export class Authorization {
  constructor(private readonly http: Transport) {}

  /**
   * Would this be allowed? No money moves, guaranteed server-side.
   *
   * Returns the decision rather than throwing on DENY — a refusal is a normal,
   * expected answer here and making callers use try/catch for it would be
   * wrong. Use `assertAllowed()` if you want the throwing shape.
   */
  async evaluate(
    params: { text: string; intentId?: string; ceilingPaise?: number },
    options?: RequestOptions,
  ): Promise<Evaluation> {
    const text = requireUtterance(params?.text, "authorization.evaluate");
    try {
      const res = await this.http.request<Evaluation>(
        "POST",
        "/v1/evaluate",
        {
          utterance: text,
          intent_id: params.intentId ?? null,
          ceiling_paise: params.ceilingPaise ?? null,
        },
        options,
      );
      return res.data;
    } catch (err) {
      throw liftNotGrounded(err);
    }
  }

  /** What is being asked of the human, in a shape a UI can render. */
  async stepUp(params: { text: string }, options?: RequestOptions): Promise<StepUp> {
    const text = requireUtterance(params?.text, "authorization.stepUp");
    try {
      const res = await this.http.request<StepUp>("POST", "/v1/step-up", { utterance: text }, options);
      return res.data;
    } catch (err) {
      throw liftNotGrounded(err);
    }
  }

  /**
   * Redeem a human's yes.
   *
   * The token is single-use and bound to the person, the intent, the basket,
   * the amount, the merchant and an expiry. Presenting it for a different
   * basket does not work, which is the point of it being a token rather than
   * a boolean.
   */
  async approve(
    params: { text: string; approvalToken: string; intentId?: string },
    options?: RequestOptions,
  ): Promise<ExecutionResult> {
    const text = requireUtterance(params?.text, "authorization.approve");
    if (!params?.approvalToken) {
      throw new RemitValidationError("authorization.approve needs `approvalToken`", {
        code: "invalid_argument",
      });
    }
    const res = await this.http.request<ExecutionResult>(
      "POST",
      "/v1/approve",
      { utterance: text, approval_token: params.approvalToken, intent_id: params.intentId ?? null },
      // An approve redeems a token and can move money. Same idempotency
      // guarantee as execute, so the same retry rules apply.
      options,
    );
    return res.data;
  }

  /** Decline a step-up. */
  async deny(
    params: { text: string; intentId?: string },
    options?: RequestOptions,
  ): Promise<{ decision: Decision; authority_state: string | null }> {
    const text = requireUtterance(params?.text, "authorization.deny");
    const res = await this.http.request<{ decision: Decision; authority_state: string | null }>(
      "POST",
      "/v1/deny",
      { utterance: text, approval_token: "", intent_id: params.intentId ?? null },
      options,
    );
    return res.data;
  }

  /** Current authority state and its history. */
  async get(intentId: string, options?: RequestOptions): Promise<AuthorizationState> {
    if (!intentId) {
      throw new RemitValidationError("authorization.get needs an intent id", {
        code: "invalid_argument",
      });
    }
    const res = await this.http.request<AuthorizationState>(
      "GET",
      `/v1/authorization/${encodeURIComponent(intentId)}`,
      undefined,
      options,
    );
    return res.data;
  }

  /**
   * Cancel an authority. Forward only — a revocation does not come back, and
   * the SDK does not offer an un-revoke because the protocol does not have one.
   *
   * `scope: "principal"` is the kill switch: it stops everything for you.
   */
  async revoke(
    params: { scope?: "intent" | "principal"; intentId?: string; reason?: string } = {},
    options?: RequestOptions,
  ): Promise<import("./types.js").Revocation> {
    const scope = params.scope ?? "principal";
    if (scope === "intent" && !params.intentId) {
      throw new RemitValidationError('revoke({scope:"intent"}) needs `intentId`', {
        code: "invalid_argument",
      });
    }
    const res = await this.http.request<import("./types.js").Revocation>(
      "POST",
      "/v1/revoke",
      { scope, intent_id: params.intentId ?? null, reason: params.reason ?? null },
      // Revocation is the safety control. Retrying it is safe (the server
      // treats it as idempotent) and failing to revoke is the worse outcome,
      // so this deliberately gets MORE retries than anything else.
      { ...options, retry: { retries: 4, ...(options?.retry ?? {}) } },
    );
    return res.data;
  }
}

/**
 * Throw unless the decision is AUTO.
 *
 * Kept as a free function rather than a method so the throwing style is opt-in.
 * The SDK's default is to hand you the decision, because REMIT refusing is the
 * product working, not an exception.
 */
export function assertAllowed(decision: Decision): void {
  if (decision.verdict === "AUTO") return;

  const failed = decision.failed ?? [];
  const base = {
    status: 200,
    detail: decision.reason,
    correlationId: decision.correlation_id,
  };

  if (failed.includes("REVOKED") || failed.includes("AUTH-003")) {
    throw new RemitRevokedError(decision.reason, { ...base, code: "revoked" });
  }
  if (failed.includes("AUTH-002")) {
    throw new RemitExpiredError(decision.reason, { ...base, code: "expired" });
  }
  if (failed.some((c) => c.startsWith("DRIFT"))) {
    throw new RemitSemanticDriftError(decision.reason, { ...base, drift: decision.drift });
  }
  throw new RemitAuthorizationError(decision.reason, {
    ...base,
    verdict: decision.verdict,
    failed,
    clauses: decision.clauses,
    correlationId: decision.correlation_id,
  });
}

export { RemitError };
