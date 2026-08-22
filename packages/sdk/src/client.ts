/**
 * The client.
 *
 * ON AUTHENTICATION, AND WHY THERE IS NO API KEY
 * ----------------------------------------------
 * REMIT deliberately has no API key, and the SDK does not invent one. A bearer
 * key is a credential that says "spend as whoever this belongs to", which is
 * precisely the bug that let a caller choose whose limits to spend.
 *
 * Identity is a session this server signed. Two ways to hold one:
 *
 *   1. Let the SDK get one. On the first call the server mints a principal and
 *      returns it; the SDK stores it in memory and sends it on every later
 *      call. Nothing to configure, and the identity lasts as long as the client
 *      object does.
 *
 *   2. Bring one, via `session` or `REMIT_SESSION`, to keep the same principal
 *      across processes — which you need if you want exposure limits,
 *      revocation and audit history to accumulate against one identity.
 *
 * A session is a credential. It is never logged, never printed by the CLI, and
 * never written to a config file by `remit init`.
 */

import { Audit, Receipts } from "./audit.js";
import { Authorization } from "./authorization.js";
import { RemitValidationError } from "./errors.js";
import { Transport, type RetryPolicy } from "./http.js";
import { Intents } from "./intent.js";
import { Payments } from "./execution.js";
import { assertCompatible, SDK_VERSION } from "./protocol.js";
import type { ProtocolInfo } from "./types.js";

export interface RemitOptions {
  /** e.g. https://remit-vvug.onrender.com — no trailing path. */
  baseUrl?: string;
  /** A session this server signed. Falls back to `REMIT_SESSION`. */
  session?: string;
  /** Per-request timeout in ms. Default 30000. */
  timeoutMs?: number;
  retry?: RetryPolicy;
  /** Injectable for tests. Defaults to global fetch. */
  fetch?: typeof fetch;
  /** Appended to the SDK's User-Agent, so your traffic is identifiable. */
  appName?: string;
}

const DEFAULT_BASE_URL = "https://remit-vvug.onrender.com";
const COOKIE_NAME = "remit_session";

export class Remit {
  readonly intents: Intents;
  readonly authorization: Authorization;
  readonly payments: Payments;
  readonly audit: Audit;
  readonly receipts: Receipts;

  private readonly transport: Transport;
  /** The session in memory. Never serialised anywhere by this SDK. */
  private sessionCookie?: string;

  constructor(options: RemitOptions = {}) {
    const env = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process?.env ?? {};
    const baseUrl = (options.baseUrl ?? env["REMIT_BASE_URL"] ?? DEFAULT_BASE_URL).replace(/\/+$/, "");

    if (!/^https?:\/\//i.test(baseUrl)) {
      throw new RemitValidationError(`baseUrl must be an http(s) URL, got: ${baseUrl}`, {
        code: "invalid_argument",
      });
    }
    if (/^http:\/\//i.test(baseUrl) && !/^http:\/\/(localhost|127\.0\.0\.1|\[::1\])/i.test(baseUrl)) {
      // Not fatal — someone may be running REMIT inside a private network —
      // but a session travelling in clear text should be said out loud.
      // eslint-disable-next-line no-console
      console.warn(
        `[remit-sdk] baseUrl is plain http (${baseUrl}). Your session will travel unencrypted.`,
      );
    }

    this.sessionCookie = options.session ?? env["REMIT_SESSION"];

    this.transport = new Transport({
      baseUrl,
      timeoutMs: options.timeoutMs ?? 30_000,
      retry: {
        retries: options.retry?.retries ?? 2,
        baseDelayMs: options.retry?.baseDelayMs ?? 250,
        maxDelayMs: options.retry?.maxDelayMs ?? 4_000,
      },
      userAgent: `remit-sdk/${SDK_VERSION} node/${process.versions?.node ?? "?"}` +
        (options.appName ? ` ${options.appName}` : ""),
      fetchImpl: options.fetch,
      session: undefined, // resolved per request below
      cookieHeader: () => (this.sessionCookie ? `${COOKIE_NAME}=${this.sessionCookie}` : undefined),
      onCookie: (raw) => {
        const m = /(?:^|,\s*)remit_session=([^;]+)/.exec(raw);
        if (m?.[1]) this.sessionCookie = m[1];
      },
    });

    this.intents = new Intents(this.transport);
    this.authorization = new Authorization(this.transport);
    this.payments = new Payments(this.transport);
    this.audit = new Audit(this.transport);
    this.receipts = new Receipts(this.audit);
  }

  get baseUrl(): string {
    return this.transport.baseUrl;
  }

  /**
   * The session currently in use, or undefined before the first call.
   *
   * Exposed so a long-running service can persist ONE identity across
   * restarts. It is a credential: store it the way you store a credential.
   */
  get session(): string | undefined {
    return this.sessionCookie;
  }

  /** What the server says it is. Also the cheapest reachability check. */
  async protocol(): Promise<ProtocolInfo> {
    const res = await this.transport.request<ProtocolInfo>("GET", "/v1/", undefined, {});
    return res.data;
  }

  /**
   * Reachable, compatible, and identified?
   *
   * Every field is something that was actually checked. Nothing here is
   * inferred from a successful import.
   */
  async health(): Promise<{
    reachable: boolean;
    protocolVersion: string | null;
    compatible: boolean;
    authenticated: boolean;
    baseUrl: string;
    sdkVersion: string;
    error?: string;
  }> {
    const base = {
      reachable: false,
      protocolVersion: null as string | null,
      compatible: false,
      authenticated: false,
      baseUrl: this.baseUrl,
      sdkVersion: SDK_VERSION,
    };
    let info: ProtocolInfo;
    try {
      info = await this.protocol();
    } catch (err) {
      return { ...base, error: err instanceof Error ? err.message : String(err) };
    }
    let compatible = true;
    try {
      assertCompatible(info.version);
    } catch {
      compatible = false;
    }
    return {
      ...base,
      reachable: true,
      protocolVersion: info.version,
      compatible,
      // A session exists only once the server has issued or accepted one.
      authenticated: Boolean(this.sessionCookie),
    };
  }
}
