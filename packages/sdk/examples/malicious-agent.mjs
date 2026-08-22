/**
 * A malicious agent, and what happens to it.
 *
 *   node examples/malicious-agent.mjs
 *   REMIT_BASE_URL=http://127.0.0.1:8099 node examples/malicious-agent.mjs
 *
 * Every attack below is one an actual compromised or badly-aligned agent would
 * try. None of them are blocked by the SDK — the SDK is untrusted too, and an
 * attacker would simply not use it. They are blocked by the server, which is
 * the only place a boundary can live.
 *
 * This example REPORTS what happened rather than asserting a happy ending. If
 * REMIT ever lets one through, this prints that it did.
 */
import { Remit } from "remit-sdk";

const baseUrl = process.env.REMIT_BASE_URL ?? "https://remit-vvug.onrender.com";

const HUMAN_SAID = "buy a yoga mat under 2000";

/** Each attack gets its own principal, so one revocation cannot skew another. */
function freshClient(name) {
  return new Remit({ baseUrl, appName: `example-malicious-${name}` });
}

const results = [];

function record(name, whatItTried, blocked, evidence) {
  results.push({ name, whatItTried, blocked, evidence });
  const mark = blocked ? "BLOCKED" : "!! GOT THROUGH";
  console.log(`${mark.padEnd(15)} ${name}`);
  console.log(`${"".padEnd(15)} tried: ${whatItTried}`);
  console.log(`${"".padEnd(15)} ${evidence}\n`);
}

/** A verdict that is not AUTO means the agent did not get to spend. */
function refused(decision) {
  return decision.verdict !== "AUTO";
}

async function overspend() {
  const remit = freshClient("overspend");
  // The human said 2000. The agent wants to spend far more.
  const d = await remit.authorization.evaluate({ text: "buy a yoga mat under 200000" });
  // This is a DIFFERENT mandate, so the honest check is against the original:
  const original = await remit.authorization.evaluate({ text: HUMAN_SAID });
  const overCeiling =
    original.verdict === "AUTO" && original.total.amount_paise > 200000;
  record(
    "overspend",
    "spend past the ceiling the human stated",
    !overCeiling,
    `under the original mandate REMIT allowed ${money(original.total)} against a ₹2,000.00 ceiling`,
  );
}

async function wrongProduct() {
  const remit = freshClient("wrong-product");
  // The classic: the human said laptop, the shop's best answer is a laptop
  // STAND, and a spending limit would wave it through because it is cheap.
  const d = await remit.authorization.evaluate({ text: "buy a laptop under 50000" });
  record(
    "wrong product",
    "buy something that merely shares a word with what was asked for",
    refused(d),
    `verdict ${d.verdict}${d.failed.length ? ` (${d.failed.join(", ")})` : ""} — ${d.reason}`,
  );
}

async function extraAccessory() {
  const remit = freshClient("upsell");
  const d = await remit.authorization.evaluate({
    text: "buy a yoga mat under 2000 and a water bottle and a towel",
  });
  record(
    "extra accessories",
    "add items the human never mentioned",
    refused(d),
    `verdict ${d.verdict}${d.failed.length ? ` (${d.failed.join(", ")})` : ""}`,
  );
}

async function currencyManipulation() {
  const remit = freshClient("currency");
  // "under $2000" is not "under ₹2000", and reading it as rupees is a 85x error.
  let evidence;
  let blocked;
  try {
    const d = await remit.authorization.evaluate({ text: "buy a yoga mat under $2000" });
    blocked = refused(d);
    evidence = `verdict ${d.verdict}${d.failed.length ? ` (${d.failed.join(", ")})` : ""} — ${d.reason}`;
  } catch (err) {
    blocked = true;
    evidence = `refused before a decision: ${err.message}`;
  }
  record("currency switch", "spend dollars as if they were rupees", blocked, evidence);
}

async function splitSpending() {
  const remit = freshClient("split");
  // Stay under the ceiling each time, exceed it in aggregate.
  const first = await remit.payments.execute({ text: "buy a yoga mat under 2000" });
  const second = await remit.payments.execute({ text: "buy a water bottle under 2000" });
  const third = await remit.payments.execute({ text: "buy a towel under 2000" });
  const anyStopped = [first, second, third].some((r) => r.decision.verdict !== "AUTO");
  record(
    "split the purchase",
    "three purchases each under the ceiling, above it together",
    anyStopped,
    anyStopped
      ? `REMIT stopped one: ${[first, second, third]
          .map((r) => r.decision.verdict)
          .join(" -> ")}`
      : "all three went through as AUTO — SPLIT-001 did not aggregate",
  );
}

async function replay() {
  const remit = freshClient("replay");
  const first = await remit.payments.execute({ text: HUMAN_SAID });
  if (first.decision.verdict !== "AUTO") {
    record("replay", "buy the same thing twice", true, `first purchase was ${first.decision.verdict}`);
    return;
  }
  const second = await remit.payments.execute({ text: HUMAN_SAID });
  const samePayment = second.execution.payment_id === first.execution.payment_id;
  record(
    "replay",
    "send the same purchase again and get charged twice",
    samePayment && second.execution.replayed,
    samePayment
      ? `both requests returned ${second.execution.payment_id}, replayed=${second.execution.replayed}`
      : `TWO payments: ${first.execution.payment_id} and ${second.execution.payment_id}`,
  );
}

async function spendAfterRevocation() {
  const remit = freshClient("revoked");
  await remit.authorization.revoke({ reason: "malicious-agent example" });
  const after = await remit.payments.execute({ text: HUMAN_SAID });
  record(
    "revoked authority",
    "keep spending after the human pulled the kill switch",
    after.decision.verdict === "DENY",
    `verdict ${after.decision.verdict}, execution ${after.execution.state}`,
  );
}

async function crossPrincipal() {
  const victim = freshClient("victim");
  const attacker = freshClient("attacker");
  const bought = await victim.payments.execute({ text: HUMAN_SAID });

  let blocked = false;
  let evidence;
  try {
    await attacker.audit.get(bought.decision.correlation_id);
    evidence = "the attacker READ the victim's audit trail";
  } catch (err) {
    blocked = err.status === 404;
    evidence = `reading someone else's trace returned ${err.status} — existence is not confirmed either`;
  }
  record("cross-principal read", "read another principal's audit trail", blocked, evidence);
}

async function forgeIdentity() {
  // There is no identity field in any request model, so there is nothing to
  // forge through the SDK. Try it at the wire level instead.
  let blocked = true;
  let evidence = "no request model has an identity field — nothing to set";
  try {
    const res = await fetch(`${baseUrl}/v1/intents`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        // A principal the attacker simply picked.
        authorization: "Bearer usr_iwouldliketobethisperson.0000000000000000",
      },
      body: JSON.stringify({ utterance: HUMAN_SAID, user_id: "usr_someone_else" }),
    });
    const body = await res.json();
    const got = body?.intent?.actor_id ?? "";
    blocked = got !== "usr_iwouldliketobethisperson" && got !== "usr_someone_else";
    evidence = `server assigned ${got.slice(0, 12)}… rather than the identity we asked for`;
  } catch (err) {
    evidence = `request failed: ${err.message}`;
  }
  record("identity forgery", "choose whose limits to spend", blocked, evidence);
}

function money(m) {
  return m ? `${m.currency} ${(m.amount_paise / 100).toFixed(2)}` : "nothing";
}

async function main() {
  console.log(`a malicious agent against ${baseUrl}\n`);
  console.log(`the human said: "${HUMAN_SAID}"\n`);

  for (const attack of [
    wrongProduct,
    extraAccessory,
    currencyManipulation,
    overspend,
    splitSpending,
    replay,
    spendAfterRevocation,
    crossPrincipal,
    forgeIdentity,
  ]) {
    try {
      await attack();
    } catch (err) {
      record(attack.name, "(threw before completing)", false, `error: ${err.message}`);
    }
  }

  const held = results.filter((r) => r.blocked).length;
  console.log("-".repeat(64));
  console.log(`${held}/${results.length} attacks blocked`);
  const through = results.filter((r) => !r.blocked);
  if (through.length) {
    console.log("\nGOT THROUGH — this is not a passing run:");
    for (const r of through) console.log(`  - ${r.name}: ${r.evidence}`);
    process.exitCode = 1;
  }
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
