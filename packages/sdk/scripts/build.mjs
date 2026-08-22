/**
 * Build: ESM + CJS + type declarations.
 *
 * esbuild for the JavaScript, tsc for the .d.ts. Two tools because they are
 * good at different jobs and a bundler that also emits types tends to emit
 * types that are subtly not what tsc would say.
 */
import { rm, mkdir, writeFile } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import * as esbuild from "esbuild";

const OUT = "dist";
await rm(OUT, { recursive: true, force: true });
await mkdir(OUT, { recursive: true });

const shared = {
  bundle: true,
  platform: "node",
  target: "node18.17",
  sourcemap: true,
  logLevel: "info",
  // Nothing external: the SDK has zero runtime dependencies, which is the
  // point. A supply chain is a thing you inherit.
  external: [],
};

await esbuild.build({
  ...shared,
  entryPoints: ["src/index.ts"],
  outfile: `${OUT}/index.js`,
  format: "esm",
});

await esbuild.build({
  ...shared,
  entryPoints: ["src/index.ts"],
  outfile: `${OUT}/index.cjs`,
  format: "cjs",
});

await esbuild.build({
  ...shared,
  entryPoints: ["src/cli/main.ts"],
  outfile: `${OUT}/cli.js`,
  format: "esm",
  banner: { js: "#!/usr/bin/env node" },
});

execFileSync("npx", ["tsc", "--emitDeclarationOnly", "--outDir", OUT], {
  stdio: "inherit",
  shell: process.platform === "win32",
});

// tsc emits dist/index.d.ts for ESM; CJS consumers resolve types through
// "exports", which points at the same file. Write the CJS declaration shim so
// `require("remit-sdk")` type-checks under node16/nodenext resolution too.
await writeFile(`${OUT}/index.d.cts`, 'export * from "./index.js";\n', "utf8");

console.log("build ok");
