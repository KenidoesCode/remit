/**
 * Typed errors.
 *
 * Every error carries enough for a developer to know what happened and what to
 * do next, and never carries a credential. `RemitError.toJSON()` is what you
 * may log; it is built from an allow-list rather than by deleting known-bad
 * keys, because a deny-list is one new field away from leaking.
 */

import type { Clause, Verdict } from "./types.js";

export interface RemitErrorInit {
  status?: number;
  code?: string;
  requestId?: string;
  detail?: string;
  cause?: unknown;
}

/** Base class. Catch this to catch everything the SDK throws deliberately. */
export class RemitError extends Error {
  readonly status?: number;
  readonly code: string;
  readonly requestId?: string;
  readonly detail?: string;

  constructor(message: string, init: RemitErrorInit = {}) {
    super(message, init.cause !== undefined ? { cause: init.cause } : undefined);
    this.name = new.target.name;
    this.status = init.status;
    this.code = init.code ?? "remit_error";
    this.requestId = init.requestId;
    this.detail = init.detail;
    Error.captureStackTrace?.(this, new.target);
  }

  /** Safe to log. Allow-list, not deny-list. */
  toJSON(): Record<string, unknown> {
    return {
      name: this.name,
      code: this.code,
      message: this.message,
      status: this.status,
      requestId: this.requestId,
      detail: this.detail,
    };
  }
}

/** No usable session, or the session presented was not one this server signed. */
export class RemitAuthenticationError extends RemitError {}

/**
 * REMIT refused. This is not a bug — it is the product working.
 *
 * `verdict` is DENY or STEP_UP, and `failed` names the clauses. A STEP_UP is
 * not a failure either: it means a human has to say yes.
 */
export class RemitAuthorizationError extends RemitError {
  readonly verdict: Verdict | null;
  readonly failed: string[];
  readonly clauses: Clause[];
  readonly correlationId?: string;

  constructor(
    message: string,
    init: RemitErrorInit & {
      verdict?: Verdict | null;
      failed?: string[];
      clauses?: Clause[];
      correlationId?: string;
    } = {},
  ) {
    super(message, { code: "authorization_refused", ...init });
    this.verdict = init.verdict ?? null;
    this.failed = init.failed ?? [];
    this.clauses = init.clauses ?? [];
    this.correlationId = init.correlationId;
  }

  override toJSON(): Record<string, unknown> {
    return {
      ...super.toJSON(),
      verdict: this.verdict,
      failed: this.failed,
      correlationId: this.correlationId,
    };
  }
}

/** The SDK rejected your arguments before sending anything. */
export class RemitValidationError extends RemitError {}

/** The authority was cancelled. Forward only — it does not come back. */
export class RemitRevokedError extends RemitError {}

/** The envelope timed out. Authority is bounded in time by design. */
export class RemitExpiredError extends RemitError {}

/** The cart drifted too far from what the human authorised. */
export class RemitSemanticDriftError extends RemitError {
  readonly drift: number | null;
  constructor(message: string, init: RemitErrorInit & { drift?: number | null } = {}) {
    super(message, { code: "semantic_drift", ...init });
    this.drift = init.drift ?? null;
  }
}

/** A policy clause refused, outside the AUTO/STEP_UP/DENY envelope. */
export class RemitPolicyError extends RemitError {}

/** The request never got an answer: DNS, TCP, TLS, timeout, abort. */
export class RemitNetworkError extends RemitError {}

/** The request timed out client-side. */
export class RemitTimeoutError extends RemitNetworkError {}

/** The call was aborted through an AbortSignal. */
export class RemitAbortError extends RemitNetworkError {}

/** Execution reached the rail and did not complete. */
export class RemitExecutionError extends RemitError {}

/** 429. `retryAfterSeconds` comes from the server when it sends one. */
export class RemitRateLimitError extends RemitError {
  readonly retryAfterSeconds: number | null;
  constructor(message: string, init: RemitErrorInit & { retryAfterSeconds?: number | null } = {}) {
    super(message, { code: "rate_limited", ...init });
    this.retryAfterSeconds = init.retryAfterSeconds ?? null;
  }
}

/**
 * Nothing in the catalog answered the request, so the policy engine was never
 * reached.
 *
 * Deliberately NOT an authorization error. "Refused" and "never asked" are
 * different sentences, and a client building a retry policy needs the right
 * one. The server draws this distinction too — 422 `no_decision` vs a DENY.
 */
export class RemitNotGroundedError extends RemitError {
  readonly stockedHint: string[];
  constructor(message: string, init: RemitErrorInit & { stockedHint?: string[] } = {}) {
    super(message, { code: "not_grounded", ...init });
    this.stockedHint = init.stockedHint ?? [];
  }
}

/** The server speaks a protocol major version this SDK does not. */
export class RemitProtocolError extends RemitError {}
