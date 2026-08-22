/**
 * `remit.audit` and `remit.receipts` — why something happened, and whether the
 * record can be trusted to say so.
 *
 * WHAT VERIFICATION HERE ACTUALLY MEANS
 * -------------------------------------
 * `receipts.verify()` recomputes every event hash itself:
 *
 *     sha256( prev_hash + canonical({kind, trace_id, ts, payload}) )
 *
 * where `canonical` is the server's exact JSON encoding — sorted keys, no
 * whitespace, non-ASCII escaped. If a payload was edited after the fact, the
 * recomputed hash will not match and this returns `ok: false`, whatever the
 * server said about itself.
 *
 * WHAT IT DOES NOT MEAN
 * ---------------------
 * The chain is hash-linked with NO EXTERNAL TRUST ANCHOR. An operator who
 * controls the whole chain can rewrite it from any point and re-link every
 * hash consistently, and this check would pass. It is tamper-EVIDENT against
 * partial edits and it is not tamper-proof, so `no_external_trust_anchor` is
 * on every result rather than in a footnote.
 */

import { RawNumber, parsePreservingNumbers, toPlain } from "./canonical.js";
import { RemitValidationError } from "./errors.js";
import type { RequestOptions, Transport } from "./http.js";
import type { AuditEvent, Evidence, ReceiptVerification, Verdict } from "./types.js";

/**
 * Python's `json.dumps(obj, sort_keys=True, separators=(",", ":"))`, which is
 * what the server hashes.
 *
 * `JSON.stringify` is NOT equivalent, in two ways that both change the bytes:
 * it does not sort keys, and it does not escape non-ASCII. A payload with "₹"
 * in it hashes differently under the two, so this is hand-rolled rather than
 * assumed.
 */
export function canonicalJson(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  // A number that arrived over the wire is emitted as the exact characters it
  // arrived as. Python writes a float 0.0 as "0.0"; JSON.parse turns that into
  // the JS number 0 and the literal is gone, so re-encoding gives "0", the
  // bytes differ and every hash over it fails. See src/canonical.ts.
  if (value instanceof RawNumber) return value.literal;
  if (typeof value === "number") {
    if (Number.isNaN(value)) return "NaN";
    if (!Number.isFinite(value)) return value > 0 ? "Infinity" : "-Infinity";
    return String(value);
  }
  if (typeof value === "string") return quote(value);
  if (Array.isArray(value)) return "[" + value.map(canonicalJson).join(",") + "]";
  if (typeof value === "object") {
    const obj = value as Record<string, unknown>;
    const keys = Object.keys(obj).sort();
    return "{" + keys.map((k) => quote(k) + ":" + canonicalJson(obj[k])).join(",") + "}";
  }
  // Python's default=str stringifies anything else.
  return quote(String(value));
}

const ESCAPES: Record<string, string> = {
  '"': '\\"',
  "\\": "\\\\",
  "\n": "\\n",
  "\r": "\\r",
  "\t": "\\t",
  "\b": "\\b",
  "\f": "\\f",
};

function quote(s: string): string {
  let out = '"';
  for (const ch of s) {
    const code = ch.codePointAt(0) as number;
    const esc = ESCAPES[ch];
    if (esc) {
      out += esc;
    } else if (code < 0x20) {
      out += "\\u" + code.toString(16).padStart(4, "0");
    } else if (code < 0x7f) {
      out += ch;
    } else if (code <= 0xffff) {
      // ensure_ascii=True
      out += "\\u" + code.toString(16).padStart(4, "0");
    } else {
      // astral plane: Python emits a surrogate pair, lowercase hex
      const v = code - 0x10000;
      const hi = 0xd800 + (v >> 10);
      const lo = 0xdc00 + (v & 0x3ff);
      out += "\\u" + hi.toString(16).padStart(4, "0") + "\\u" + lo.toString(16).padStart(4, "0");
    }
  }
  return out + '"';
}

async function sha256Hex(input: string): Promise<string> {
  const subtle = (globalThis.crypto as Crypto | undefined)?.subtle;
  const bytes = new TextEncoder().encode(input);
  if (subtle) {
    const digest = await subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
  }
  const { createHash } = await import("node:crypto");
  return createHash("sha256").update(input, "utf8").digest("hex");
}

/** Recompute one event's hash exactly as the server did. */
export async function recomputeHash(event: AuditEvent & { trace_id?: string; prev_hash?: string }): Promise<string> {
  const body = canonicalJson({
    kind: event.kind,
    trace_id: event.trace_id,
    ts: event.ts,
    payload: event.payload,
  });
  return sha256Hex((event.prev_hash ?? "") + body);
}

export class Audit {
  constructor(private readonly http: Transport) {}

  /**
   * The evidence AND the exact bytes it arrived as.
   *
   * Verification needs the wire text, because the hash is over characters and
   * a round trip through JSON.parse does not preserve them.
   */
  async getVerifiable(
    correlationId: string,
    options?: RequestOptions,
  ): Promise<{ evidence: Evidence; rawEvents: unknown[] }> {
    if (!correlationId) {
      throw new RemitValidationError("audit.get needs a correlation id", { code: "invalid_argument" });
    }
    const res = await this.http.request<Evidence>(
      "GET",
      `/v1/audit/${encodeURIComponent(correlationId)}`,
      undefined,
      options,
    );
    let rawEvents: unknown[] = [];
    if (res.raw) {
      try {
        const parsed = parsePreservingNumbers(res.raw) as { events?: unknown[] };
        rawEvents = Array.isArray(parsed?.events) ? parsed.events : [];
      } catch {
        rawEvents = [];
      }
    }
    return { evidence: res.data, rawEvents };
  }

  /** The record for one correlation id. Scoped to you — an audit trail carries
   *  the sentence somebody typed. */
  async get(correlationId: string, options?: RequestOptions): Promise<Evidence> {
    if (!correlationId) {
      throw new RemitValidationError("audit.get needs a correlation id", { code: "invalid_argument" });
    }
    const res = await this.http.request<Evidence>(
      "GET",
      `/v1/audit/${encodeURIComponent(correlationId)}`,
      undefined,
      options,
    );
    return res.data;
  }
}

export class Receipts {
  constructor(private readonly audit: Audit) {}

  /**
   * Fetch the evidence and check it, rather than trusting it.
   *
   * Never returns `ok: true` on the strength of the server's own
   * `chain_intact` flag: every event hash is recomputed locally first.
   */
  async verify(correlationId: string, options?: RequestOptions): Promise<ReceiptVerification> {
    const { evidence, rawEvents } = await this.audit.getVerifiable(correlationId, options);
    return verifyEvidence(evidence, rawEvents);
  }
}

/** The pure half, so it can be tested without a server. */
export async function verifyEvidence(
  evidence: Evidence,
  rawEvents?: unknown[],
): Promise<ReceiptVerification> {
  const checks: { name: string; passed: boolean; detail: string }[] = [];
  const events = (evidence.events ?? []) as (AuditEvent & { trace_id?: string; prev_hash?: string })[];

  checks.push({
    name: "has_events",
    passed: events.length > 0,
    detail: `${events.length} event(s) in the record`,
  });

  const ordered = events.every((e, i) => i === 0 || e.seq > (events[i - 1] as AuditEvent).seq);
  checks.push({
    name: "sequence_monotonic",
    passed: ordered,
    detail: ordered ? "sequence numbers increase" : "sequence numbers are out of order",
  });

  const scoped = events.every((e) => !e.trace_id || e.trace_id === evidence.correlation_id);
  checks.push({
    name: "trace_scoped",
    passed: scoped,
    detail: scoped
      ? "every event belongs to this correlation id"
      : "an event belongs to a different trace",
  });

  // The real one.
  let hashesOk = true;
  let hashDetail = "no event carried a hash to check";
  // Prefer the literal-preserving copies when the caller supplied them.
  const source = (rawEvents && rawEvents.length === events.length ? rawEvents : events) as (AuditEvent & {
    trace_id?: string;
    prev_hash?: string;
  })[];
  const checkable = source.filter((e) => e.hash && e.prev_hash !== undefined && e.trace_id);
  if (checkable.length > 0) {
    const bad: number[] = [];
    for (const e of checkable) {
      const recomputed = await recomputeHash(e);
      if (recomputed !== e.hash) bad.push(Number(toPlain(e.seq)));
    }
    hashesOk = bad.length === 0;
    hashDetail = hashesOk
      ? `recomputed ${checkable.length} event hash(es); all matched`
      : `hash mismatch at seq ${bad.join(", ")} — the payload does not match its hash`;
  } else {
    // Do not silently pass a check that did not run.
    hashesOk = false;
    hashDetail =
      "the server did not return prev_hash/trace_id, so no hash could be recomputed. " +
      "Upgrade the REMIT server, or treat this receipt as unverified.";
  }
  checks.push({ name: "hashes_recomputed", passed: hashesOk, detail: hashDetail });

  checks.push({
    name: "server_reports_chain_intact",
    passed: evidence.chain_intact === true,
    detail:
      evidence.chain_intact === true
        ? "the server's own chain check passed"
        : `the server reports a break at seq ${evidence.first_bad_seq}`,
  });

  const decision = (evidence.decision ?? {}) as Record<string, unknown>;
  const verdict = (typeof decision["verdict"] === "string" ? decision["verdict"] : null) as Verdict | null;
  checks.push({
    name: "decision_present",
    passed: verdict !== null,
    detail: verdict ? `verdict ${verdict}` : "the record carries no decision",
  });

  return {
    ok: checks.every((c) => c.passed),
    chain_intact: evidence.chain_intact === true,
    first_bad_seq: evidence.first_bad_seq ?? null,
    verdict,
    checks,
    no_external_trust_anchor: true,
    evidence,
  };
}
