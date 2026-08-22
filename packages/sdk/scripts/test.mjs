/**
 * Run the test files, cross-platform.
 *
 * `node --test test/*.test.js` looks portable and is not: cmd.exe and
 * PowerShell do not expand globs, so on Windows Node receives the literal
 * string `test/*.test.js` and finds nothing. `node --test test/` is not a
 * substitute either — it fails outright on Node 22.
 *
 * So: discover the files with node:fs, pass absolute paths, and let Node do
 * the running. No shell involved anywhere, which is the only way to be sure
 * the same thing happens on all three platforms.
 *
 * Found when the package was published for real, from Windows, on Node 24.
 */
import { spawn } from "node:child_process";
import { readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join, dirname } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const testDir = join(here, "..", "test");

const files = readdirSync(testDir)
  .filter((f) => f.endsWith(".test.js"))
  .sort()
  .map((f) => join(testDir, f));

if (files.length === 0) {
  console.error("no test files found in " + testDir);
  process.exit(1);
}

const child = spawn(process.execPath, ["--test", ...files], {
  stdio: "inherit",
  // shell:false is deliberate. A shell here would reintroduce the quoting
  // problem this script exists to remove — the path contains a space on the
  // machine this was found on ("Pranauv Shrinaath").
  shell: false,
});

child.on("exit", (code, signal) => {
  if (signal) {
    console.error(`tests terminated by signal ${signal}`);
    process.exit(1);
  }
  process.exit(code ?? 1);
});
