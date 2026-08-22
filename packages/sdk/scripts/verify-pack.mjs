/**
 * Check what npm will ACTUALLY publish, not what package.json says.
 *
 * npm rewrites the manifest on its way into the tarball, and when it dislikes
 * something it "auto-corrects" it with one warn line buried in fifty lines of
 * notices:
 *
 *     npm warn publish "bin[remit]" script name dist/cli.js was invalid and removed
 *
 * That is the CLI being deleted from a package whose headline feature is a CLI.
 * `npm i -g remit-sdk` would have installed no `remit` command, the README
 * would have been wrong, and nothing would have failed loudly. FAILURES #54.
 *
 *   npm run verify:pack
 *
 * Two deliberate choices about HOW this checks:
 *
 * - It reads `npm pack --json`, which lists exactly the files npm would ship.
 *   No `tar` binary, no extraction. Windows has shipped bsdtar since 1803, but
 *   after FAILURES #53 the appetite for depending on shell tools is low.
 * - It forces `--dry-run=false`. Run inside `npm publish --dry-run`, the inner
 *   pack inherits dry-run through npm_config_dry_run, writes nothing, and the
 *   check silently has nothing to look at.
 */
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const pkgDir = join(dirname(fileURLToPath(import.meta.url)), "..");
const manifest = createRequire(import.meta.url)(join(pkgDir, "package.json"));

const failures = [];
function check(name, ok, detail = "") {
  console.log(`${ok ? "  ok  " : " FAIL "} ${name}${detail ? "  " + detail : ""}`);
  if (!ok) failures.push(name);
}

const npmCli = process.env["npm_execpath"];
if (!npmCli) {
  console.error("run this through npm so npm_execpath is set:\n  npm run verify:pack");
  process.exit(1);
}

const work = mkdtempSync(join(tmpdir(), "remit-pack-"));
let files = [];
try {
  const out = execFileSync(
    process.execPath,
    [npmCli, "pack", "--pack-destination", work, "--json", "--dry-run=false"],
    {
      cwd: pkgDir,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "inherit"],
      // Belt and braces: strip the inherited flag as well as overriding it.
      env: { ...process.env, npm_config_dry_run: "" },
    },
  );
  const start = out.indexOf("[");
  files = (JSON.parse(out.slice(start))[0].files ?? []).map((f) => f.path);
} finally {
  rmSync(work, { recursive: true, force: true });
}

check("npm reported a file list", files.length > 0, `${files.length} files`);

// --- the one that actually bit -------------------------------------------
const bin = manifest.bin ?? {};
const binNames = Object.keys(bin);
check("a bin is declared", binNames.length > 0, binNames.join(", "));

for (const name of binNames) {
  const target = String(bin[name]);
  // This is the rule npm enforces, written out rather than discovered again.
  check(
    `bin[${name}] has no "./" prefix`,
    !target.startsWith("./") && !target.startsWith("../"),
    target.startsWith("./") ? `"${target}" — npm strips or REMOVES this` : target,
  );
  check(`bin[${name}] uses forward slashes`, !target.includes("\\"), target);
  check(`bin[${name}] exists on disk`, existsSync(join(pkgDir, target)), target);
  check(`bin[${name}] is in the tarball`, files.includes(target), target);
}

// --- everything a consumer needs, and nothing they should not get ---------
for (const required of ["README.md", "LICENSE", "dist/index.js", "dist/index.cjs", "dist/index.d.ts"]) {
  check(`${required} ships`, files.includes(required));
}

const forbidden = files.filter((f) =>
  /(^|\/)\.env|^src\/|^test\/|^scripts\/|\.pem$|\.key$|node_modules/.test(f),
);
check("nothing that must not ship", forbidden.length === 0, forbidden.join(" "));

check(
  "zero runtime dependencies",
  Object.keys(manifest.dependencies ?? {}).length === 0,
  JSON.stringify(manifest.dependencies ?? {}),
);

if (failures.length) {
  console.error(`\n${failures.length} check(s) failed: ${failures.join(", ")}`);
  process.exit(1);
}
console.log("\npackage verified");
