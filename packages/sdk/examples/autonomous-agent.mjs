/**
 * An autonomous agent doing the honest thing.
 *
 *   node examples/autonomous-agent.mjs
 *   REMIT_BASE_URL=http://127.0.0.1:8099 node examples/autonomous-agent.mjs
 *
 * BRING YOUR OWN MODEL. `decideWhatToBuy` below is where a model goes — GPT,
 * Claude, Gemini, Llama on your own GPU, or the deterministic stand-in used
 * here so this example runs with no API key and no credentials.
 *
 * Whatever produces the action, it crosses the SAME boundary. That is the
 * whole design: the model is untrusted input, not a participant in the
 * decision.
 */
import { Remit, assertAllowed, RemitAuthorizationError } from "remit-sdk";

const remit = new Remit({
  baseUrl: process.env.REMIT_BASE_URL ?? "https://remit-vvug.onrender.com",
  appName: "example-autonomous-agent",
});

/**
 * YOUR MODEL GOES HERE.
 *
 * It returns a proposed action in natural language. It does not return a
 * verdict, an amount it has authorised, or a boolean called `allowed` — there
 * is nowhere in the protocol to put one, on purpose. A model that could hand
 * back `{"authorized": true}` is a model that authorises payments.
 */
async function decideWhatToBuy(humanSaid) {
  // Swap for: openai.chat.completions.create(...), anthropic.messages.create(...),
  // a local llama.cpp server, anything. The shape of this function is the
  // integration point.
  return humanSaid;
}

async function main() {
  const humanSaid = "buy a yoga mat under 2000";
  console.log(`human:  ${humanSaid}`);

  // 1. The human's words become a bounded authority.
  const { intent, authority } = await remit.intents.create({ text: humanSaid });
  console.log(`intent: ${intent.intent_id}`);
  console.log(`        ceiling ${money(intent.ceiling)}, expires ${authority.expires_at}`);

  // 2. The agent proposes.
  const proposal = await decideWhatToBuy(humanSaid);
  console.log(`agent:  ${proposal}`);

  // 3. Ask before doing. Nothing moves.
  const decision = await remit.authorization.evaluate({ text: proposal });
  console.log(`remit:  ${decision.verdict} — ${decision.reason}`);
  if (decision.failed.length) console.log(`        failed: ${decision.failed.join(", ")}`);

  if (decision.verdict === "DENY") {
    console.log("\nstopped. the agent does not get to overrule this.");
    return;
  }

  if (decision.verdict === "STEP_UP") {
    // A human has to say yes. Note what the agent CANNOT do here: approve it.
    // CAN_APPROVE excludes agents, so an agent that could approve the step-up
    // it triggered would not have been stopped by anything.
    const ask = await remit.authorization.stepUp({ text: proposal });
    console.log(`\na human is being asked: ${ask.asking?.why ?? "confirmation required"}`);
    console.log("this example stops here rather than pretending to be a person.");
    return;
  }

  // 4. AUTO — execute.
  const result = await remit.payments.execute({ text: proposal });
  console.log(`\npayment ${result.execution.payment_id} (${result.execution.state})`);
  console.log(`replayed: ${result.execution.replayed}`);

  // 5. The receipt, checked rather than trusted.
  const receipt = await remit.receipts.verify(result.decision.correlation_id);
  console.log(`\nreceipt verifies: ${receipt.ok}`);
  for (const c of receipt.checks) {
    console.log(`  ${c.passed ? "ok  " : "FAIL"} ${c.name} — ${c.detail}`);
  }
  console.log(
    "\nnote: the chain has no external trust anchor. tamper-evident, not tamper-proof.",
  );
}

function money(m) {
  return m ? `${m.currency} ${(m.amount_paise / 100).toFixed(2)}` : "none stated";
}

main().catch((err) => {
  if (err instanceof RemitAuthorizationError) {
    console.error(`refused: ${err.message} (${err.failed.join(", ")})`);
    process.exitCode = 2;
    return;
  }
  console.error(err.message);
  process.exitCode = 1;
});
