import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import assert from "node:assert/strict";

const host = process.env.OPENCODE_TEST_BINARY, python = process.env.VIBE_TEST_PYTHON;
if (!host || !python) throw new Error("Set OPENCODE_TEST_BINARY and VIBE_TEST_PYTHON to explicitly prepared host and uv-managed interpreter");
const root = mkdtempSync(join(tmpdir(), "vibe-login-contract-"));
try {
  const modeFile = join(root, "mode.txt"), evidence = join(root, "result.json");
  const env = {
    PATH: process.env.PATH ?? "", SystemRoot: process.env.SystemRoot ?? "C:\\Windows",
    USERPROFILE: root, HOME: root, APPDATA: join(root, "roaming"), LOCALAPPDATA: join(root, "local"), TEMP: root, TMP: root,
    XDG_DATA_HOME: join(root, "data"), XDG_CACHE_HOME: join(root, "cache"), XDG_STATE_HOME: join(root, "state"), XDG_CONFIG_HOME: join(root, "config"),
    OPENCODE_TEST_HOME: root, OPENCODE_DISABLE_AUTOUPDATE: "true", OPENCODE_DISABLE_MODELS_FETCH: "true",
    PYTHONPATH: root + (process.platform === "win32" ? ";" : ":") + resolve("../.."), VIBE_TEST_MODE_FILE: modeFile,
  };
  const version = Bun.spawnSync([host, "--version"], { env });
  assert.match(version.stdout.toString(), /\bv2\.2\.5\b/);
  // Public-session stand-in installed only in this isolated child interpreter.
  // No real browser, credentials, callback listener or external HTTP is used.
  writeFileSync(join(root, "sitecustomize.py"), `import asyncio, os, socket, webbrowser\nfrom datetime import datetime, timezone\nfrom types import SimpleNamespace\nfrom mvibe_auth import LoginHandle, LoginCompleted, LoginReusedCurrent, AuthFailure\nimport mvibe_auth.login_cli as cli\ndef deny(*a, **k): raise RuntimeError('offline fixture')\nsocket.create_connection = deny\nwebbrowser.open = deny\nclass Session:\n config = SimpleNamespace(audience='https://inference.example.test')\n def __init__(self, config):\n  self.mode = open(os.environ['VIBE_TEST_MODE_FILE']).read()\n async def start_login(self): return LoginHandle('synthetic', '', datetime.now(timezone.utc))\n async def wait_login(self, attempt):\n  if self.mode == 'cancelled':
   open(os.environ['VIBE_TEST_MODE_FILE'] + '.pid', 'w').write(str(os.getpid()))
   await asyncio.sleep(60)\n  return AuthFailure('rejected') if self.mode == 'failed' else LoginReusedCurrent() if self.mode == 'reused_current' else LoginCompleted()\n async def resolve(self, target): return None\n async def cancel_login(self, attempt): pass\n async def close(self): pass\ncli.AuthSession = Session\n`);
  const plugin = join(root, "plugins", "login");
  mkdirSync(plugin, { recursive: true });
  writeFileSync(join(plugin, "index.ts"), `export { default } from ${JSON.stringify(resolve("test/host-login-plugin.ts").replaceAll("\\", "/"))};`);
  for (const mode of ["completed", "reused_current", "failed", "cancelled"]) {
  writeFileSync(join(root, "opencode.json"), JSON.stringify({ plugins: [{ package: "./plugins/login", options: { mode, modeFile, evidence, audience: "https://inference.example.test", helperCommand: { executable: python, args: ["-m", "mvibe_auth", "bridge"] } } }] }));
  const child: Bun.Subprocess<"ignore", "pipe", "pipe"> = Bun.spawn([host, "models", "--standalone"], { env, cwd: root, stdin: "ignore", stdout: "pipe", stderr: "pipe" });
  const timer = setTimeout(() => child.kill(), 30_000);
  const [out, err, code]: [string, string, number] = await Promise.all([new Response(child.stdout).text(), new Response(child.stderr).text(), child.exited]);
  clearTimeout(timer);
  const raw = readFileSync(evidence, "utf8");
  assert.ok(raw.startsWith("{"), raw);
  assert.equal(code, 0, err + out);
  const result = JSON.parse(raw);
  assert.equal(result.results.length, 1);
  assert.deepEqual(result.value, { type: "key", key: "vibe-oidc-connected-v1" });
  }
  console.log("OpenCode 2.2.5 production registration: command completed/reused_current/failed/cancelled and marker-only credential PASS");
} finally { rmSync(root, { recursive: true, force: true }); }


