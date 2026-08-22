/**
 * Build: ESM + CJS + type declarations.
 *
 * esbuild for the JavaScript, tsc for the .d.ts. Two tools because they are
 * good at different jobs and a bundler that also emits types tends to emit
 * types that are subtly not what tsc would say.
 */
import { rm, mkdir, writeFile } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
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

// Resolve TypeScript's own entry point and run it with THIS node.
//
// `npx tsc` needs a shell on Windows, and a shell means quoting — which breaks
// the moment the checkout lives somewhere with a space in the path, e.g.
// C:\Users\Pranauv Shrinaath\... . Resolving the module and spawning
// process.execPath avoids npx, avoids a shell, and uses the exact typescript
// that was installed rather than whatever npx decides to fetch.
const require_ = createRequire(import.meta.url);
let tscBin;
try {
  tscBin = require_.resolve("typescript/bin/tsc");
} catch {
  console.error(
    "typescript is not installed. Run `npm install` in packages/sdk first —\n" +
    "this repository ships source, not node_modules.",
  );
  process.exit(1);
}
execFileSync(process.execPath, [tscBin, "--emitDeclarationOnly", "--outDir", OUT], {
  stdio: "inherit",
});

// tsc emits dist/index.d.ts for ESM; CJS consumers resolve types through
// "exports", which points at the same file. Write the CJS declaration shim so
// `require("remit-sdk")` type-checks under node16/nodenext resolution too.
await writeFile(`${OUT}/index.d.cts`, 'export * from "./index.js";\n', "utf8");

console.log("build ok");
