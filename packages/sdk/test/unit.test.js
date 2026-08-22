/**
 * Unit tests — no server, no network.
 *
 * These cover the parts of the SDK that are decisions rather than plumbing:
 * argument validation that refuses before sending anything, error mapping,
 * credential hygiene, and the canonical encoder that receipt verification
 * stands on.
 */
import assert from "node:assert/strict";
import test from "node:test";

import {
  Remit,
  RemitAuthorizationError,
  RemitError,
  RemitNotGroundedError,
  RemitRateLimitError,
  RemitValidationError,
  RemitRevokedError,
  RemitExpiredError,
  RemitSemanticDriftError,
  assertAllowed,
  assertCompatible,
  canonicalJson,
  isSpent,
  whyUnusable,
  parsePreservingNumbers,
  RawNumber,
  SDK_VERSION,
} from "../dist/index.js";

const BASE = "http://127.0.0.1:9";

test("the client refuses a baseUrl that is not http(s)", () => {
  assert.throws(() => new Remit({ baseUrl: "ftp://example.com" }), RemitValidationError);
});

test("intents.create refuses an empty utterance before sending anything", async () => {
  let called = false;
  const remit = new Remit({
    baseUrl: BASE,
    fetch: async () => {
      called = true;
      throw new Error("should not have been called");
    },
  });
  await assert.rejects(() => remit.intents.create({ text: "   " }), RemitValidationError);
  assert.equal(called, false, "a validation failure must not reach the network");
});

test("intents.create refuses an utterance longer than the protocol allows", async () => {
  const remit = new Remit({ baseUrl: BASE, fetch: async () => { throw new Error("no"); } });
  await assert.rejects(() => remit.intents.create({ text: "x".repeat(2001) }), RemitValidationError);
});

test("execute demands the original sentence, not just an id", async () => {
  const remit = new Remit({ baseUrl: BASE, fetch: async () => { throw new Error("no"); } });
  await assert.rejects(
    () => remit.payments.execute({ intentId: "int_123" }),
    (err) => err instanceof RemitValidationError && /bound to the words/.test(err.message),
  );
});

test('revoke({scope:"intent"}) demands an intent id', async () => {
  const remit = new Remit({ baseUrl: BASE, fetch: async () => { throw new Error("no"); } });
  await assert.rejects(
    () => remit.authorization.revoke({ scope: "intent" }),
    RemitValidationError,
  );
});

// --- errors ---------------------------------------------------------------

test("an error is loggable and carries no session", () => {
  const err = new RemitError("nope", { status: 500, requestId: "req_1", detail: "detail" });
  const json = err.toJSON();
  assert.deepEqual(Object.keys(json).sort(), [
    "code", "detail", "message", "name", "requestId", "status",
  ]);
  assert.equal(JSON.stringify(json).includes("usr_"), false);
});

test("assertAllowed passes AUTO and classifies every refusal", () => {
  const base = { reason: "because", clauses: [], drift: null, total: null,
    correlation_id: "cor_1", latency_ms: 1, protocol_version: "1.0", authority_state: null };

  assert.doesNotThrow(() => assertAllowed({ ...base, verdict: "AUTO", failed: [] }));

  assert.throws(
    () => assertAllowed({ ...base, verdict: "DENY", failed: ["AUTH-003"] }),
    RemitRevokedError,
  );
  assert.throws(
    () => assertAllowed({ ...base, verdict: "DENY", failed: ["AUTH-002"] }),
    RemitExpiredError,
  );
  assert.throws(
    () => assertAllowed({ ...base, verdict: "DENY", failed: ["DRIFT-001"] }),
    RemitSemanticDriftError,
  );
  assert.throws(
    () => assertAllowed({ ...base, verdict: "STEP_UP", failed: ["MATCH-001"] }),
    (err) => err instanceof RemitAuthorizationError && err.verdict === "STEP_UP",
  );
});

test("a 429 becomes a rate limit error carrying retry-after", async () => {
  const remit = new Remit({
    baseUrl: BASE,
    retry: { retries: 0 },
    fetch: async () =>
      new Response(JSON.stringify({ error: "too many requests" }), {
        status: 429,
        headers: { "content-type": "application/json", "retry-after": "7" },
      }),
  });
  await assert.rejects(
    () => remit.intents.create({ text: "buy a yoga mat under 2000" }),
    (err) => err instanceof RemitRateLimitError && err.retryAfterSeconds === 7,
  );
});

test("a 422 not_grounded becomes the typed error, not an authorization error", async () => {
  const remit = new Remit({
    baseUrl: BASE,
    retry: { retries: 0 },
    fetch: async () =>
      new Response(
        JSON.stringify({ error: "not_grounded", detail: "this catalog cannot answer that" }),
        { status: 422, headers: { "content-type": "application/json" } },
      ),
  });
  await assert.rejects(
    () => remit.intents.create({ text: "buy a submarine" }),
    (err) => err instanceof RemitNotGroundedError && !(err instanceof RemitAuthorizationError),
  );
});

// --- retries --------------------------------------------------------------

test("a 503 is retried and then succeeds", async () => {
  let calls = 0;
  const remit = new Remit({
    baseUrl: BASE,
    retry: { retries: 2, baseDelayMs: 1, maxDelayMs: 2 },
    fetch: async () => {
      calls++;
      if (calls < 3) return new Response("{}", { status: 503 });
      return new Response(JSON.stringify({ intent: { intent_id: "int_1" }, authority: {} }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    },
  });
  const res = await remit.intents.create({ text: "buy a yoga mat under 2000" });
  assert.equal(calls, 3);
  assert.equal(res.intent.intent_id, "int_1");
});

test("a 400 is NOT retried — retrying a rejected request just rejects again", async () => {
  let calls = 0;
  const remit = new Remit({
    baseUrl: BASE,
    retry: { retries: 3, baseDelayMs: 1 },
    fetch: async () => {
      calls++;
      return new Response(JSON.stringify({ error: "utterance is required" }), {
        status: 400,
        headers: { "content-type": "application/json" },
      });
    },
  });
  await assert.rejects(() => remit.intents.create({ text: "hello" }));
  assert.equal(calls, 1);
});

test("an abort signal stops the request and is not retried", async () => {
  const controller = new AbortController();
  const remit = new Remit({
    baseUrl: BASE,
    retry: { retries: 3, baseDelayMs: 1 },
    fetch: async (_url, init) => {
      controller.abort();
      const err = new Error("aborted");
      err.name = "AbortError";
      throw err;
    },
  });
  await assert.rejects(
    () => remit.intents.create({ text: "buy a yoga mat under 2000" }, { signal: controller.signal }),
    (err) => /abort/i.test(err.message),
  );
});

// --- protocol -------------------------------------------------------------

test("a differing protocol MAJOR is refused and a differing minor is not", () => {
  assert.doesNotThrow(() => assertCompatible("1.0"));
  assert.doesNotThrow(() => assertCompatible("1.7"));
  assert.throws(() => assertCompatible("2.0"), /speaks 1\.x/);
  assert.throws(() => assertCompatible("banana"), /unreadable/);
});

// --- canonical encoding ---------------------------------------------------

test("canonicalJson sorts keys and emits no whitespace", () => {
  assert.equal(canonicalJson({ b: 1, a: 2 }), '{"a":2,"b":1}');
});

test("canonicalJson escapes non-ASCII the way Python's ensure_ascii does", () => {
  // JSON.stringify would leave the rupee sign as a literal character and the
  // bytes would differ from what the server hashed.
  assert.equal(canonicalJson("₹"), '"\\u20b9"');
  assert.notEqual(canonicalJson("₹"), JSON.stringify("₹"));
});

test("number literals survive a round trip, which is what makes hashing work", () => {
  const parsed = parsePreservingNumbers('{"drift":0.0,"n":3,"e":1.5e3}');
  assert.ok(parsed.drift instanceof RawNumber);
  // The whole point: 0.0 must not become 0.
  assert.equal(canonicalJson(parsed), '{"drift":0.0,"e":1.5e3,"n":3}');
  assert.notEqual(canonicalJson(parsed), JSON.stringify(JSON.parse('{"drift":0.0,"e":1.5e3,"n":3}')));
});

test("the parser handles escapes, nesting and empty containers", () => {
  const src = '{"a":[],"b":{},"c":"x\\"y","d":[1,[2,{"e":null}]],"f":true}';
  const got = parsePreservingNumbers(src);
  assert.deepEqual(got.a, []);
  assert.deepEqual(got.b, {});
  assert.equal(got.c, 'x"y');
  assert.equal(got.f, true);
});

// --- revocation helpers ---------------------------------------------------

test("isSpent knows revoked and expired apart from healthy", () => {
  const future = new Date(Date.now() + 60_000).toISOString();
  const past = new Date(Date.now() - 60_000).toISOString();

  assert.equal(isSpent({ revoked: false, expires_at: future }), false);
  assert.equal(isSpent({ revoked: true, expires_at: future }), true);
  assert.equal(isSpent({ revoked: false, expires_at: past }), true);

  assert.equal(whyUnusable({ revoked: false, expires_at: future }), null);
  assert.match(whyUnusable({ revoked: true, expires_at: future, revoked_at: past }), /revoked/);
  assert.match(whyUnusable({ revoked: false, expires_at: past }), /expired/);
});

test("the SDK version is a real semver and matches the package", async () => {
  const { readFile } = await import("node:fs/promises");
  const pkg = JSON.parse(await readFile(new URL("../package.json", import.meta.url), "utf8"));
  assert.match(SDK_VERSION, /^\d+\.\d+\.\d+$/);
  assert.equal(SDK_VERSION, pkg.version, "SDK_VERSION drifted from package.json");
});
