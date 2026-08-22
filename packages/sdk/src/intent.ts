/** `remit.intents` — compiling a human sentence into a bounded authority. */

import { RemitNotGroundedError, RemitValidationError } from "./errors.js";
import type { Transport, RequestOptions } from "./http.js";
import type { IntentCreated } from "./types.js";

export class Intents {
  constructor(private readonly http: Transport) {}

  /**
   * Turn what a human said into an authority.
   *
   * The utterance is the evidence and the SDK does not paraphrase it. Note
   * what is NOT a parameter: who is asking. Identity comes from the session,
   * never from a field — a field a caller can set is a field a caller can set
   * to somebody else's.
   *
   * @throws {RemitNotGroundedError} when nothing in the catalog answers it.
   */
  async create(params: { text: string }, options?: RequestOptions): Promise<IntentCreated> {
    const text = params?.text;
    if (typeof text !== "string" || text.trim() === "") {
      throw new RemitValidationError("intents.create needs a non-empty `text`", {
        code: "invalid_argument",
      });
    }
    if (text.length > 2000) {
      throw new RemitValidationError("utterance exceeds the protocol limit of 2000 characters", {
        code: "invalid_argument",
      });
    }
    try {
      const res = await this.http.request<IntentCreated>(
        "POST",
        "/v1/intents",
        { utterance: text },
        options,
      );
      return res.data;
    } catch (err) {
      throw liftNotGrounded(err);
    }
  }
}

/** Turn the server's 422 `not_grounded` into the typed error for it. */
export function liftNotGrounded(err: unknown): unknown {
  const e = err as { status?: number; code?: string; message?: string; detail?: string };
  if (e?.status === 422 && (e.code === "not_grounded" || e.message === "not_grounded")) {
    return new RemitNotGroundedError(
      e.detail || "this catalog cannot answer that request",
      { status: 422, detail: e.detail, cause: err },
    );
  }
  return err;
}
