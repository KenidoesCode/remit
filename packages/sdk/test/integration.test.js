/**
 * Integration tests — against a REAL REMIT server.
 *
 * Set REMIT_TEST_URL to point at one. These skip rather than fail when it is
 * absent, because a suite that cannot run without a server is a suite nobody
 * runs; but they must never PASS without one, so every test asserts something
 * only a real server can produce.
 *
 *   python -m uvicorn remit.api:api --port 8099
 *   REMIT_TEST_URL=http://127.0.0.1:8099 node --test test/integration.test.js
 */
import assert from "node:assert/strict";
import test from "node:test";

import { Remit, verifyEvidence, RemitNotGroundedError } from "../dist/index.js";

const URL_ = process.env["REMIT_TEST_URL"];
const skip = URL_ ? false : "REMIT_TEST_URL is not set";

/** One client per test file run, so the principal accumulates history. */
const remit = URL_ ? new Remit({ baseUrl: URL_, appName: "remit-sdk-tests" }) : null;

const IN_ENVELOPE = "buy a yoga mat under 2000";

test("the server describes itself and speaks a compatible protocol", { skip }, async () => {
  const info = await remit.protocol();
  assert.equal(info.protocol, "remit");
  assert.match(info.version, /^1\./);
  // The routes the SDK actually calls must all be advertised.
  for (const route of [
    "POST /v1/intents",
    "POST /v1/evaluate",
    "POST /v1/execute",
    "POST /v1/step-up",
    "POST /v1/approve",
    "POST /v1/deny",
    "POST /v1/revoke",
  ]) {
    assert.ok(route in info.routes, `${route} is not advertised by the server`);
  }
});

test("health reports things it actually checked", { skip }, async () => {
  const h = await remit.health();
  assert.equal(h.reachable, true);
  assert.equal(h.compatible, true);
  assert.match(h.protocolVersion, /^1\./);
});

test("an intent compiles into a bounded authority", { skip }, async () => {
  const { intent, authority } = await remit.intents.create({ text: IN_ENVELOPE });
  assert.match(intent.intent_id, /^int_/);
  assert.equal(intent.utterance, IN_ENVELOPE);
  assert.equal(intent.ceiling.amount_paise, 200000);
  assert.equal(intent.ceiling.currency, "INR");
  assert.ok(intent.requested.length > 0);
  assert.equal(authority.revoked, false);
  assert.ok(authority.expires_at, "an authority with no expiry is not bounded");
});

test("the session is one identity across calls, not a new one each time", { skip }, async () => {
  const a = await remit.intents.create({ text: IN_ENVELOPE });
  const b = await remit.intents.create({ text: IN_ENVELOPE });
  assert.equal(a.intent.actor_id, b.intent.actor_id);
  assert.ok(remit.session, "no session was captured");
});

test("evaluate answers without moving money", { skip }, async () => {
  const d = await remit.authorization.evaluate({ text: IN_ENVELOPE });
  assert.ok(["AUTO", "STEP_UP", "DENY"].includes(d.verdict));
  assert.equal(d.sandboxed, true, "evaluate must run sandboxed");
  assert.ok(d.clauses.length > 0, "a decision with no clauses is not a decision");
  assert.equal(d.would_execute, d.verdict === "AUTO");
});

test("the catalog abstains rather than substituting", { skip }, async () => {
  await assert.rejects(
    () => remit.intents.create({ text: "buy a submarine under 900000" }),
    (err) => err instanceof RemitNotGroundedError,
  );
});

test("a stated ceiling binds the total", { skip }, async () => {
  const d = await remit.authorization.evaluate({ text: IN_ENVELOPE });
  if (d.verdict === "AUTO") {
    assert.ok(d.total.amount_paise <= 200000, "AUTO above the stated ceiling");
  }
});

test("execute produces a decision and an execution", { skip }, async () => {
  const res = await remit.payments.execute({ text: IN_ENVELOPE });
  assert.ok(res.decision.correlation_id);
  assert.ok(res.execution.state);
  if (res.decision.verdict === "AUTO") {
    assert.ok(res.execution.payment_id, "an AUTO that moved nothing");
  }
});

/**
 * The claim the whole retry policy rests on.
 *
 * If this ever fails, retries on execute must be turned OFF, not explained.
 */
test("the same purchase twice is one payment, and says so", { skip }, async () => {
  const first = await remit.payments.execute({ text: IN_ENVELOPE });
  if (first.decision.verdict !== "AUTO") {
    // Nothing was bought, so there is nothing to double-buy. Not a pass by
    // default: assert the reason rather than skipping silently.
    assert.notEqual(first.execution.payment_id, undefined);
    return;
  }
  const second = await remit.payments.execute({ text: IN_ENVELOPE });
  assert.equal(second.execution.payment_id, first.execution.payment_id,
    "the same sentence produced a SECOND payment — retries are not safe");
  assert.equal(second.execution.replayed, true,
    "the server did not flag the replay, so a client cannot tell");
});

test("a receipt verifies against hashes recomputed here, not the server's word", { skip }, async () => {
  const res = await remit.payments.execute({ text: IN_ENVELOPE });
  const v = await remit.receipts.verify(res.decision.correlation_id);

  const hashCheck = v.checks.find((c) => c.name === "hashes_recomputed");
  assert.ok(hashCheck, "no hash check ran");
  assert.equal(hashCheck.passed, true, hashCheck.detail);
  assert.match(hashCheck.detail, /recomputed \d+ event hash/);
  assert.equal(v.ok, true);
  assert.equal(v.no_external_trust_anchor, true, "the limitation must always be stated");
});

/**
 * Proof the verifier is not passing vacuously.
 *
 * A verifier that has never rejected anything is a verifier nobody has tested.
 */
test("a tampered payload fails verification", { skip }, async () => {
  const res = await remit.payments.execute({ text: IN_ENVELOPE });
  const { evidence, rawEvents } = await remit.audit.getVerifiable(res.decision.correlation_id);
  assert.ok(rawEvents.length > 0, "no raw events to tamper with");

  // Edit one payload, exactly as an operator quietly rewriting history would.
  const tampered = rawEvents.map((e, i) =>
    i === 0 ? { ...e, payload: { ...e.payload, tampered: true } } : e,
  );

  const good = await verifyEvidence(evidence, rawEvents);
  assert.equal(good.ok, true, "the untampered record should verify");

  const bad = await verifyEvidence(evidence, tampered);
  assert.equal(bad.ok, false, "TAMPERING WENT UNDETECTED");
  const check = bad.checks.find((c) => c.name === "hashes_recomputed");
  assert.equal(check.passed, false);
  assert.match(check.detail, /hash mismatch/);
});

test("verification refuses to pass when it could not run", { skip }, async () => {
  // An older server that does not return prev_hash must not produce ok:true.
  const v = await verifyEvidence({
    correlation_id: "cor_x",
    intent_id: "int_x",
    events: [{ seq: 1, ts: "t", kind: "K", payload: {}, hash: "abc" }],
    decision: { verdict: "AUTO" },
    authority_history: [],
    chain_intact: true,
    first_bad_seq: null,
  });
  assert.equal(v.ok, false, "a check that did not run must not count as passed");
  const check = v.checks.find((c) => c.name === "hashes_recomputed");
  assert.equal(check.passed, false);
  assert.match(check.detail, /no hash could be recomputed/);
});

test("audit is scoped to the caller", { skip }, async () => {
  const res = await remit.payments.execute({ text: IN_ENVELOPE });
  const mine = await remit.audit.get(res.decision.correlation_id);
  assert.equal(mine.correlation_id, res.decision.correlation_id);

  // A different principal must not be able to read it.
  const stranger = new Remit({ baseUrl: URL_, appName: "remit-sdk-tests-stranger" });
  await assert.rejects(
    () => stranger.audit.get(res.decision.correlation_id),
    (err) => err.status === 404,
  );
});

/**
 * Revocation last: it is a kill switch for this principal and everything after
 * it would be refused.
 */
test("revocation stops the next purchase", { skip }, async () => {
  const solo = new Remit({ baseUrl: URL_, appName: "remit-sdk-tests-revoke" });
  const before = await solo.authorization.evaluate({ text: IN_ENVELOPE });
  assert.ok(["AUTO", "STEP_UP"].includes(before.verdict), `unexpected ${before.verdict}`);

  const rv = await solo.authorization.revoke({ reason: "sdk integration test" });
  assert.ok(rv.revoked_at);

  const after = await solo.payments.execute({ text: IN_ENVELOPE });
  assert.equal(after.decision.verdict, "DENY", "a revoked principal was allowed to spend");
  assert.equal(after.execution.state, "BLOCKED");
});
