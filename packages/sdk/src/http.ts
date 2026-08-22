/**
 * The transport.
 *
 * Timeouts, bounded retries with exponential backoff and jitter, request ids,
 * abort support, and one rule that matters more than the rest:
 *
 * RETRYING A PAYMENT
 * ------------------
 * A client may only retry a financial action when the protocol guarantees the
 * retry cannot double-charge. REMIT's does, and not by accepting a key the
 * caller invents: idempotency is derived server-side from the MEANING of the
 * request —
 *
 *     H(tenant : user : semantic_hash | cart_signature | total | catalog_version)
 *
 * — under a UNIQUE constraint, which is the serialisation point. The same
 * human sentence producing the same basket at the same price is the same
 * purchase, so a retry collapses onto the first payment and comes back with
 * `execution.replayed === true`.
 *
 * That is a claim, so it is tested rather than asserted: see
 * `test/idempotency.test.js`, which fires the same execute twice and asserts
 * one payment id and `replayed`. If that test ever fails, retries on execute
 * must be turned off, not explained.
 *
 * There is deliberately NO `idempotencyKey` option. A key the caller chooses
 * would let two different purchases share one, and a key the SDK generates per
 * call would defeat the deduplication entirely — it would make every retry a
 * new purchase. Offering one would be a worse guarantee wearing a familiar name.
 */

import {
  RemitAbortError,
  RemitAuthenticationError,
  RemitError,
  RemitNetworkError,
  RemitRateLimitError,
  RemitTimeoutError,
} from "./errors.js";

export interface RetryPolicy {
  /** Attempts AFTER the first. 0 disables retrying. Default 2. */
  retries?: number;
  /** First backoff step in ms. Doubles each attempt. Default 250. */
  baseDelayMs?: number;
  /** Ceiling for a single backoff. Default 4000. */
  maxDelayMs?: number;
}

export interface TransportOptions {
  baseUrl: string;
  timeoutMs: number;
  retry: Required<RetryPolicy>;
  userAgent: string;
  /** Bearer session, if the caller has one. Never logged. */
  session?: string;
  fetchImpl?: typeof fetch;
  onCookie?: (raw: string) => void;
  cookieHeader?: () => string | undefined;
}

export interface RequestOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
  retry?: RetryPolicy;
  /** Set false for calls that must never be retried. */
  retryable?: boolean;
  headers?: Record<string, string>;
}

export interface RemitResponse<T> {
  data: T;
  status: number;
  requestId?: string;
  /** The exact response body. Receipt verification hashes characters, and a
   *  round trip through JSON.parse does not preserve them. */
  raw?: string;
}

const RETRYABLE_STATUS = new Set([408, 429, 502, 503, 504]);

/** Cryptographically boring; it only has to be unique enough to correlate logs. */
function newRequestId(): string {
  const bytes = new Uint8Array(12);
  (globalThis.crypto ?? require("node:crypto").webcrypto).getRandomValues(bytes);
  return "req_" + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new RemitAbortError("aborted"));
    const t = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(t);
        reject(new RemitAbortError("aborted"));
      },
      { once: true },
    );
  });
}

export class Transport {
  constructor(private readonly opts: TransportOptions) {}

  get baseUrl(): string {
    return this.opts.baseUrl;
  }

  async request<T>(
    method: "GET" | "POST",
    path: string,
    body?: unknown,
    options: RequestOptions = {},
  ): Promise<RemitResponse<T>> {
    const retryable = options.retryable !== false;
    const policy = { ...this.opts.retry, ...(options.retry ?? {}) };
    const attempts = retryable ? Math.max(0, policy.retries) + 1 : 1;
    const timeoutMs = options.timeoutMs ?? this.opts.timeoutMs;

    let lastError: unknown;
    for (let attempt = 0; attempt < attempts; attempt++) {
      try {
        return await this.once<T>(method, path, body, timeoutMs, options);
      } catch (err) {
        lastError = err;
        const last = attempt === attempts - 1;
        if (last || !this.shouldRetry(err)) throw err;

        // Honour the server's own Retry-After when it sends one; guessing a
        // shorter delay than the server asked for is how a rate limit becomes
        // a self-inflicted outage.
        const advised =
          err instanceof RemitRateLimitError && err.retryAfterSeconds != null
            ? err.retryAfterSeconds * 1000
            : 0;
        const backoff = Math.min(policy.baseDelayMs * 2 ** attempt, policy.maxDelayMs);
        const jitter = backoff * 0.25 * Math.random();
        await sleep(Math.max(advised, backoff + jitter), options.signal);
      }
    }
    throw lastError;
  }

  private shouldRetry(err: unknown): boolean {
    if (err instanceof RemitAbortError) return false;
    if (err instanceof RemitTimeoutError) return true;
    if (err instanceof RemitNetworkError) return true;
    if (err instanceof RemitRateLimitError) return true;
    if (err instanceof RemitError && err.status && RETRYABLE_STATUS.has(err.status)) return true;
    return false;
  }

  private async once<T>(
    method: "GET" | "POST",
    path: string,
    body: unknown,
    timeoutMs: number,
    options: RequestOptions,
  ): Promise<RemitResponse<T>> {
    const url = this.opts.baseUrl.replace(/\/+$/, "") + path;
    const requestId = newRequestId();
    const controller = new AbortController();
    const onAbort = () => controller.abort();
    options.signal?.addEventListener("abort", onAbort, { once: true });
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    let timedOut = false;
    const timeoutWatch = setTimeout(() => {
      timedOut = true;
    }, timeoutMs);

    const headers: Record<string, string> = {
      accept: "application/json",
      "user-agent": this.opts.userAgent,
      "x-request-id": requestId,
      ...(options.headers ?? {}),
    };
    if (body !== undefined) headers["content-type"] = "application/json";
    if (this.opts.session) headers["authorization"] = `Bearer ${this.opts.session}`;
    const cookie = this.opts.cookieHeader?.();
    if (cookie) headers["cookie"] = cookie;

    const doFetch = this.opts.fetchImpl ?? globalThis.fetch;
    if (typeof doFetch !== "function") {
      throw new RemitNetworkError(
        "no fetch available. REMIT SDK needs Node 18.17+ or a fetch polyfill.",
        { code: "no_fetch" },
      );
    }

    let res: Response;
    try {
      res = await doFetch(url, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
        redirect: "follow",
      });
    } catch (err) {
      if (options.signal?.aborted) throw new RemitAbortError("aborted", { cause: err });
      if (timedOut || controller.signal.aborted) {
        throw new RemitTimeoutError(`request timed out after ${timeoutMs}ms`, {
          code: "timeout",
          requestId,
          cause: err,
        });
      }
      throw new RemitNetworkError(`could not reach ${this.opts.baseUrl}`, {
        code: "network",
        requestId,
        cause: err,
      });
    } finally {
      clearTimeout(timer);
      clearTimeout(timeoutWatch);
      options.signal?.removeEventListener("abort", onAbort);
    }

    // The server hands a session to a first-time caller. Capture it so the
    // next request is the same principal — without this, every call is a new
    // identity with no exposure history, which is silent and wrong.
    const setCookie = res.headers.get("set-cookie");
    if (setCookie) this.opts.onCookie?.(setCookie);

    const serverRequestId = res.headers.get("x-request-id") ?? requestId;
    const text = await res.text();
    let data: unknown = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        if (!res.ok) {
          throw new RemitError(`server returned ${res.status} and not JSON`, {
            status: res.status,
            code: "bad_response",
            requestId: serverRequestId,
            detail: text.slice(0, 200),
          });
        }
        throw new RemitError("server returned a body that is not JSON", {
          status: res.status,
          code: "bad_response",
          requestId: serverRequestId,
          detail: text.slice(0, 200),
        });
      }
    }

    if (!res.ok) throw this.toError(res, data, serverRequestId);
    return { data: data as T, status: res.status, requestId: serverRequestId, raw: text };
  }

  private toError(res: Response, data: unknown, requestId: string): RemitError {
    const body = (data ?? {}) as Record<string, unknown>;
    const message =
      (typeof body["error"] === "string" && body["error"]) ||
      (typeof body["detail"] === "string" && body["detail"]) ||
      `request failed with ${res.status}`;
    const detail = typeof body["detail"] === "string" ? body["detail"] : undefined;

    if (res.status === 429) {
      const header = res.headers.get("retry-after");
      const secs = header && /^\d+$/.test(header) ? Number(header) : null;
      return new RemitRateLimitError(message, {
        status: 429,
        requestId,
        detail,
        retryAfterSeconds: secs,
      });
    }
    if (res.status === 401 || res.status === 403) {
      return new RemitAuthenticationError(message, {
        status: res.status,
        code: "unauthenticated",
        requestId,
        detail,
      });
    }
    return new RemitError(message, {
      status: res.status,
      code: typeof body["error"] === "string" ? String(body["error"]) : "http_error",
      requestId,
      detail,
    });
  }
}
