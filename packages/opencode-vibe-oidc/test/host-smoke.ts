import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import assert from "node:assert/strict";

// No implicit fallback to the user's active service or profile.
const host = process.env.OPENCODE_TEST_BINARY;
if (!host) throw new Error("Set OPENCODE_TEST_BINARY to an explicitly prepared OpenCode 2.2.5 executable; see README.md");
const root = mkdtempSync(join(tmpdir(), "vibe-provider-setup-"));
const evidence = join(root, "events.txt");
const env: Record<string, string> = {
  PATH: process.env.PATH ?? "", SystemRoot: process.env.SystemRoot ?? "C:\\Windows",
  USERPROFILE: root, HOME: root, APPDATA: join(root, "roaming"), LOCALAPPDATA: join(root, "local"),
  TEMP: root, TMP: root, OPENCODE_TEST_HOME: root,
  XDG_DATA_HOME: join(root, "data"), XDG_CACHE_HOME: join(root, "cache"),
  XDG_STATE_HOME: join(root, "state"), XDG_CONFIG_HOME: join(root, "config"),
  OPENCODE_DISABLE_AUTOUPDATE: "true", OPENCODE_DISABLE_MODELS_FETCH: "true",
};
try {
  const version = Bun.spawnSync([host, "--version"], { env, cwd: root });
  assert.equal(version.exitCode, 0);
  assert.match(version.stdout.toString(), /\bv2\.2\.5\b/, "Unverified host: rerun compatibility preparation with a matching SDK");
  const plugin = join(root, "plugins", "synthetic");
  mkdirSync(plugin, { recursive: true });
  // Host imports the typed fixture directly; it resolves the locked package's dependencies.
  writeFileSync(join(plugin, "index.ts"), `export { default } from ${JSON.stringify(resolve("test/synthetic-plugin.ts").replaceAll("\\", "/"))};`);
  writeFileSync(join(root, "opencode.json"), JSON.stringify({ plugins: [{ package: "./plugins/synthetic", options: { evidence } }] }));
  const child = Bun.spawn([host, "models", "--standalone"], { env, cwd: root, stdout: "pipe", stderr: "pipe" });
  const timer = setTimeout(() => child.kill(), 30_000);
  const [out, err, code] = await Promise.all([new Response(child.stdout).text(), new Response(child.stderr).text(), child.exited]);
  clearTimeout(timer);
  assert.equal(code, 0, err);
  assert.match(out, /vibe-setup-synthetic\/setup-model/);
  const events = readFileSync(evidence, "utf8");
  for (const event of ["provider-transform", "integration-transform", "sdk-hook-registered", "setup-complete"]) assert.ok(events.includes(event), `Missing ${event}: ${events}`);
  console.log("OpenCode 2.2.5 / @opencode/plugin 2.2.5: synthetic plugin, transforms, scoped hook registration and model visibility PASS");
} finally {
  rmSync(root, { recursive: true, force: true });
}
