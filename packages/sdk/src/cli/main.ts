/**
 * The REMIT CLI.
 *
 * Cross-platform by construction: no shelling out, no bash, no path
 * concatenation with "/", no assumptions about which terminal is running it.
 * Everything goes through node:fs, node:path and node:process.
 *
 * Every command here maps onto a route that exists. There is no command that
 * pretends to do something the server cannot do.
 */

import { readFile, writeFile, access } from "node:fs/promises";
import { join, resolve } from "node:path";
import process from "node:process";

import { Remit } from "../client.js";
import { RemitError } from "../errors.js";
import { SDK_VERSION, SUPPORTED_PROTOCOL_MAJOR } from "../protocol.js";
import { bold, dim, heading, line, safe } from "./ui.js";

const DEFAULT_BASE_URL = "https://remit-vvug.onrender.com";

interface Args {
  _: string[];
  flags: Record<string, string | boolean>;
}

function parseArgs(argv: string[]): Args {
  const _: string[] = [];
  const flags: Record<string, string | boolean> = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i] as string;
    if (a.startsWith("--")) {
      const [k, v] = a.slice(2).split("=", 2);
      if (v !== undefined) flags[k as string] = v;
      else if (argv[i + 1] && !(argv[i + 1] as string).startsWith("-")) flags[k as string] = argv[++i] as string;
      else flags[k as string] = true;
    } else if (a.startsWith("-") && a.length > 1) {
      flags[a.slice(1)] = true;
    } else {
      _.push(a);
    }
  }
  return { _, flags };
}

function out(s: string): void {
  process.stdout.write(s + "\n");
}

function fail(s: string): void {
  process.stderr.write(s + "\n");
}

const HELP = `${bold("remit")} — the authorization boundary between agents and money

${bold("USAGE")}
  remit <command> [options]

${bold("COMMANDS")}
  doctor                    check Node, SDK, endpoint, protocol and identity
  init                      write a remit.config.json in this directory
  version                   print SDK and protocol versions
  protocol                  print what the server says it is
  session                   print a session id so one identity survives runs
  intent <text>             compile a sentence into a bounded authority
  evaluate <text>           would this be allowed? no money moves
  authorize <text>          alias for evaluate
  execute <text>            do it, if the policy allows
  revoke [--intent <id>]    cancel authority. forward only
  audit <correlation-id>    the record for one decision
  receipt verify <cid>      recompute every event hash and report

${bold("OPTIONS")}
  --url <baseUrl>           REMIT endpoint (env: REMIT_BASE_URL)
  --json                    machine-readable output
  --timeout <ms>            per-request timeout, default 30000
  -h, --help                this
  -v, --version             version

${bold("IDENTITY")}
  REMIT has no API key on purpose: a bearer key would let a caller choose whose
  limits to spend. Identity is a session this server signed. Set REMIT_SESSION
  to keep one identity across runs, or let the CLI take a fresh one each time.

${dim("docs: https://github.com/KenidoesCode/remit/tree/main/docs/sdk")}
`;

function client(args: Args): Remit {
  const baseUrl = (args.flags["url"] as string) || process.env["REMIT_BASE_URL"] || DEFAULT_BASE_URL;
  const timeout = args.flags["timeout"] ? Number(args.flags["timeout"]) : undefined;
  return new Remit({
    baseUrl,
    ...(Number.isFinite(timeout) ? { timeoutMs: timeout as number } : {}),
    appName: "remit-cli",
  });
}

function printJson(value: unknown): void {
  out(safe(value));
}

// --- commands -------------------------------------------------------------

async function cmdDoctor(args: Args): Promise<number> {
  const json = args.flags["json"] === true;
  const remit = client(args);
  const results: { name: string; ok: boolean; detail: string }[] = [];

  const nodeMajor = Number.parseInt(process.versions.node.split(".")[0] as string, 10);
  const nodeMinor = Number.parseInt(process.versions.node.split(".")[1] as string, 10);
  const nodeOk = nodeMajor > 18 || (nodeMajor === 18 && nodeMinor >= 17);
  results.push({
    name: "Node.js",
    ok: nodeOk,
    detail: nodeOk ? `v${process.versions.node}` : `v${process.versions.node} — REMIT SDK needs >=18.17.0`,
  });

  results.push({ name: "SDK", ok: true, detail: `remit-sdk ${SDK_VERSION}` });
  results.push({
    name: "fetch",
    ok: typeof globalThis.fetch === "function",
    detail: typeof globalThis.fetch === "function" ? "available" : "missing — upgrade Node",
  });

  const health = await remit.health();
  results.push({
    name: "API reachable",
    ok: health.reachable,
    detail: health.reachable ? remit.baseUrl : `${remit.baseUrl} — ${health.error ?? "unreachable"}`,
  });
  results.push({
    name: "Protocol compatible",
    ok: health.compatible,
    detail: health.protocolVersion
      ? `server ${health.protocolVersion}, SDK speaks ${SUPPORTED_PROTOCOL_MAJOR}.x`
      : "no version reported",
  });

  // Identity is only real once the server has issued or accepted a session, so
  // this makes a call rather than reporting on configuration.
  let identityOk = false;
  let identityDetail = "not established";
  if (health.reachable) {
    try {
      await remit.intents.create({ text: "buy a yoga mat under 2000" });
      identityOk = Boolean(remit.session);
      identityDetail = identityOk
        ? process.env["REMIT_SESSION"]
          ? "session from REMIT_SESSION"
          : "session issued by the server (set REMIT_SESSION to keep it)"
        : "server did not issue a session";
    } catch (err) {
      // A refusal still proves the authorization endpoint answered us.
      identityOk = Boolean(remit.session);
      identityDetail = identityOk
        ? "session established"
        : `no session: ${err instanceof Error ? err.message : String(err)}`;
    }
  }
  results.push({ name: "Identity", ok: identityOk, detail: identityDetail });

  const ok = results.every((r) => r.ok);

  if (json) {
    printJson({ ok, baseUrl: remit.baseUrl, sdkVersion: SDK_VERSION, checks: results });
    return ok ? 0 : 1;
  }

  out(heading("REMIT DOCTOR"));
  for (const r of results) out(line(r.ok ? "ok" : "fail", r.name, r.detail));
  out("");
  out(ok ? bold("REMIT IS READY.") : bold("REMIT IS NOT READY — see the failures above."));
  return ok ? 0 : 1;
}

async function cmdInit(args: Args): Promise<number> {
  const dir = process.cwd();
  const target = join(dir, "remit.config.json");
  try {
    await access(target);
    fail(`remit.config.json already exists at ${resolve(target)} — not overwriting.`);
    return 1;
  } catch {
    /* does not exist, good */
  }

  // No secrets in generated files, ever. The session is read from the
  // environment at run time and is deliberately absent here.
  const config = {
    $schema: "https://github.com/KenidoesCode/remit#remit-config",
    baseUrl: (args.flags["url"] as string) || DEFAULT_BASE_URL,
    timeoutMs: 30000,
    retry: { retries: 2 },
    _comment:
      "Your session is a credential and is NOT stored here. Set REMIT_SESSION " +
      "in the environment to keep one identity across runs.",
  };
  await writeFile(target, JSON.stringify(config, null, 2) + "\n", "utf8");
  out(`wrote ${resolve(target)}`);
  out("");
  out(dim("next:"));
  out("  export REMIT_SESSION=...   " + dim("(optional — keeps one identity across runs)"));
  out("  remit doctor");
  return 0;
}

async function cmdVersion(args: Args): Promise<number> {
  if (args.flags["json"] === true) {
    printJson({ sdk: SDK_VERSION, protocolMajor: SUPPORTED_PROTOCOL_MAJOR, node: process.versions.node });
    return 0;
  }
  out(`remit-sdk ${SDK_VERSION}`);
  out(`protocol  ${SUPPORTED_PROTOCOL_MAJOR}.x`);
  out(`node      v${process.versions.node}`);
  return 0;
}

async function cmdProtocol(args: Args): Promise<number> {
  const info = await client(args).protocol();
  if (args.flags["json"] === true) {
    printJson(info);
    return 0;
  }
  out(heading(`${info.protocol} ${info.version}`));
  out(info.thesis);
  out("");
  out(bold("ROUTES"));
  for (const [route, what] of Object.entries(info.routes)) out(`  ${route.padEnd(38)} ${dim(what)}`);
  out("");
  out(bold("IDENTITY"));
  out("  " + info.identity);
  out("");
  out(bold("NOTES"));
  for (const n of info.notes) out("  - " + n);
  return 0;
}

async function cmdIntent(args: Args): Promise<number> {
  const text = args._.slice(1).join(" ");
  if (!text) return usageError("remit intent <text>");
  const res = await client(args).intents.create({ text });
  if (args.flags["json"] === true) {
    printJson(res);
    return 0;
  }
  const i = res.intent;
  out(heading("INTENT"));
  out(`  id         ${i.intent_id}`);
  out(`  said       ${JSON.stringify(i.utterance)}`);
  out(`  requested  ${i.requested.join(", ") || dim("(nothing named)")}`);
  if (i.excluded.length) out(`  excluded   ${i.excluded.join(", ")}`);
  out(`  ceiling    ${money(i.ceiling)}`);
  out(`  category   ${i.category ?? dim("none")}`);
  out(`  expires    ${i.expires_at}`);
  out(`  reading by ${i.interpreter} (confidence ${i.confidence.toFixed(2)})`);
  out("");
  out(`  authority  ${res.authority.state}`);
  return 0;
}

async function cmdEvaluate(args: Args): Promise<number> {
  const text = args._.slice(1).join(" ");
  if (!text) return usageError("remit evaluate <text>");
  const d = await client(args).authorization.evaluate({ text });
  if (args.flags["json"] === true) {
    printJson(d);
    return d.verdict === "AUTO" ? 0 : 2;
  }
  printDecision(d.verdict, d.reason, d.failed, d.clauses.length, d.total, d.correlation_id);
  out(dim("  nothing moved: evaluate runs on a throwaway instance"));
  return d.verdict === "AUTO" ? 0 : 2;
}

async function cmdExecute(args: Args): Promise<number> {
  const text = args._.slice(1).join(" ");
  if (!text) return usageError("remit execute <text>");
  const res = await client(args).payments.execute({ text });
  if (args.flags["json"] === true) {
    printJson(res);
    return res.decision.verdict === "AUTO" ? 0 : 2;
  }
  const d = res.decision;
  printDecision(d.verdict, d.reason, d.failed, d.clauses.length, d.total, d.correlation_id);
  out("");
  out(bold("  EXECUTION"));
  out(`    state     ${res.execution.state}`);
  out(`    payment   ${res.execution.payment_id ?? dim("none")}`);
  out(`    order     ${res.execution.order_id ?? dim("none")}`);
  out(`    replayed  ${res.execution.replayed}`);
  if (res.execution.replayed) {
    out(dim("    this request matched a purchase that already happened — one purchase, one payment"));
  }
  return d.verdict === "AUTO" ? 0 : 2;
}

async function cmdRevoke(args: Args): Promise<number> {
  const intentId = args.flags["intent"] as string | undefined;
  const reason = (args.flags["reason"] as string) || undefined;
  const res = await client(args).authorization.revoke({
    scope: intentId ? "intent" : "principal",
    ...(intentId ? { intentId } : {}),
    ...(reason ? { reason } : {}),
  });
  if (args.flags["json"] === true) {
    printJson(res);
    return 0;
  }
  out(heading("REVOKED"));
  out(`  scope      ${res.scope}`);
  out(`  at         ${res.revoked_at}`);
  if (res.reason) out(`  reason     ${res.reason}`);
  out("");
  out(dim("  forward only. there is no un-revoke in the protocol."));
  return 0;
}

async function cmdAudit(args: Args): Promise<number> {
  const cid = args._[1];
  if (!cid) return usageError("remit audit <correlation-id>");
  const ev = await client(args).audit.get(cid);
  if (args.flags["json"] === true) {
    printJson(ev);
    return 0;
  }
  out(heading(`AUDIT ${cid}`));
  out(`  intent        ${ev.intent_id ?? dim("none")}`);
  out(`  chain intact  ${ev.chain_intact}`);
  out("");
  for (const e of ev.events) out(`  ${String(e.seq).padStart(5)}  ${e.ts}  ${e.kind}`);
  return 0;
}

async function cmdReceiptVerify(args: Args): Promise<number> {
  const cid = args._[2];
  if (!cid) return usageError("remit receipt verify <correlation-id>");
  const v = await client(args).receipts.verify(cid);
  if (args.flags["json"] === true) {
    printJson(v);
    return v.ok ? 0 : 1;
  }
  out(heading(`RECEIPT ${cid}`));
  for (const c of v.checks) out(line(c.passed ? "ok" : "fail", c.name, c.detail));
  out("");
  out(v.ok ? bold("RECEIPT VERIFIES.") : bold("RECEIPT DOES NOT VERIFY."));
  out("");
  out(dim("  Every event hash above was recomputed locally, not taken on trust."));
  out(dim("  The chain has NO external trust anchor: it is tamper-evident against"));
  out(dim("  partial edits and is not tamper-proof."));
  return v.ok ? 0 : 1;
}

async function cmdSession(args: Args): Promise<number> {
  /**
   * Print a session so the caller can keep ONE identity across runs:
   *
   *     export REMIT_SESSION=$(remit session)
   *
   * Every other command redacts anything session-shaped. This one does not,
   * because printing it is the entire request — and a credential tool that
   * refuses to hand you the credential just gets worked around with something
   * worse. The warning goes to stderr so it survives a pipe into a variable
   * without corrupting it.
   */
  const remit = client(args);
  if (process.env["REMIT_SESSION"]) {
    fail(dim("REMIT_SESSION is already set; printing the one you gave me."));
    out(process.env["REMIT_SESSION"] as string);
    return 0;
  }
  // A session only exists once the server has issued one, which needs a call.
  await remit.intents.create({ text: "buy a yoga mat under 2000" }).catch(() => undefined);
  const session = remit.session;
  if (!session) {
    fail("the server did not issue a session");
    return 1;
  }
  fail(dim("This is a credential. It can spend against this identity's limits."));
  fail(dim("Keep it out of shell history, source control and logs."));
  out(session);
  return 0;
}

// --- helpers --------------------------------------------------------------

function money(m: { amount_paise: number; currency: string } | null): string {
  if (!m) return dim("none stated");
  return `${m.currency} ${(m.amount_paise / 100).toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function printDecision(
  verdict: string,
  reason: string,
  failed: string[],
  clauseCount: number,
  total: { amount_paise: number; currency: string } | null,
  correlationId: string,
): void {
  out(heading("DECISION"));
  out(`  verdict    ${bold(verdict)}`);
  out(`  because    ${reason}`);
  if (failed.length) out(`  failed     ${failed.join(", ")}`);
  out(`  clauses    ${clauseCount} evaluated`);
  out(`  total      ${money(total)}`);
  out(`  trace      ${correlationId}`);
}

function usageError(usage: string): number {
  fail(`usage: ${usage}`);
  return 64; // EX_USAGE
}

// --- entry ----------------------------------------------------------------

export async function main(argv: string[] = process.argv.slice(2)): Promise<number> {
  const args = parseArgs(argv);
  const cmd = args._[0];

  // --version is checked BEFORE --help, because `remit --version` has no
  // subcommand and the help branch's `!cmd` swallowed it: the flag printed the
  // whole help text instead of a version. Found by running the globally
  // installed binary rather than by reading this function.
  if (args.flags["version"] || args.flags["v"]) return cmdVersion(args);

  if (args.flags["help"] || args.flags["h"] || !cmd || cmd === "help") {
    out(HELP);
    // No arguments at all is a usage error (64); asking for help is not.
    return args.flags["help"] || args.flags["h"] || cmd === "help" ? 0 : 64;
  }

  try {
    switch (cmd) {
      case "doctor":
        return await cmdDoctor(args);
      case "init":
        return await cmdInit(args);
      case "version":
        return await cmdVersion(args);
      case "protocol":
        return await cmdProtocol(args);
      case "session":
        return await cmdSession(args);
      case "intent":
        return await cmdIntent(args);
      case "evaluate":
      case "authorize":
        return await cmdEvaluate(args);
      case "execute":
        return await cmdExecute(args);
      case "revoke":
        return await cmdRevoke(args);
      case "audit":
        return await cmdAudit(args);
      case "receipt":
        if (args._[1] === "verify") return await cmdReceiptVerify(args);
        return usageError("remit receipt verify <correlation-id>");
      default:
        fail(`unknown command: ${cmd}`);
        fail(`try: remit --help`);
        return 64;
    }
  } catch (err) {
    if (err instanceof RemitError) {
      fail(safe(err.message));
      if (err.detail) fail(dim(safe(err.detail)));
      if (err.requestId) fail(dim(`request ${err.requestId}`));
      return 1;
    }
    fail(safe(err instanceof Error ? err.message : String(err)));
    return 1;
  }
}

// Only self-execute as a program, so the module stays importable in tests.
const invokedDirectly =
  process.argv[1] !== undefined &&
  (process.argv[1].endsWith("cli.js") || process.argv[1].endsWith("remit"));

if (invokedDirectly) {
  main().then(
    (code) => {
      process.exitCode = code;
    },
    (err) => {
      fail(String(err));
      process.exitCode = 1;
    },
  );
}
