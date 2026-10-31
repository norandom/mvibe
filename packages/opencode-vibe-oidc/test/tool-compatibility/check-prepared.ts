/** Explicit offline preflight only. Host/MCP execution belongs to the runner. */
import { readFileSync, statSync } from "node:fs";
import { isAbsolute, resolve } from "node:path";
import { checkPreparedDependencies, requireReferenceHostVersion } from "./preparation.js";

const root = resolve(import.meta.dir, "../..");
try {
  checkPreparedDependencies((path) => readFileSync(resolve(root, path), "utf8"), Bun.version);
  const host = process.env.OPENCODE_TEST_BINARY;
  if (!host || !isAbsolute(host))
    throw new Error("Set OPENCODE_TEST_BINARY to an absolute prepared OpenCode 2.2.5 executable; use prepare:host explicitly first");
  try { if (!statSync(host).isFile()) throw new Error(); }
  catch { throw new Error("Prepared host is missing; run prepare:host explicitly with OPENCODE_TEST_SOURCE first"); }
  const result = Bun.spawnSync([host, "--version"], {
    cwd: root,
    env: { SystemRoot: process.env.SystemRoot ?? "C:\\Windows", OPENCODE_DISABLE_AUTOUPDATE: "true" },
    timeout: 10_000,
    stdout: "pipe", stderr: "pipe",
  });
  if (result.exitCode !== 0) throw new Error("Prepared host version check failed; prepare a runnable native OpenCode 2.2.5 executable");
  const version = requireReferenceHostVersion(result.stdout.toString());
  console.log(`Prepared tool runtimes PASS: Bun ${Bun.version}, OpenCode ${version}, locked fixture/type dependencies. Tool execution and live compatibility remain unverified.`);
} catch (error) {
  console.error(error instanceof Error ? error.message : "Prepared runtime check failed; prepare the documented runtimes explicitly");
  process.exitCode = 1;
}
