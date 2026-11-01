/** Explicit subprocess smoke: copied wrappers invoke only a recording installer in a temporary directory. */
import assert from "node:assert/strict";
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const dir = mkdtempSync(join(tmpdir(), "vibe-wrapper-smoke-"));
try {
  const scripts = join(dir, "packages", "opencode-vibe-oidc", "scripts"), record = join(dir, "args.json");
  mkdirSync(scripts, { recursive: true });
  writeFileSync(join(scripts, "install.ts"), 'import {writeFileSync} from "node:fs"; writeFileSync(process.env.INSTALL_RECORD!,JSON.stringify(process.argv.slice(2)));');
  if (process.platform === "win32") {
    const wrapper = join(dir, "install.ps1");
    copyFileSync(new URL("../../../install.ps1", import.meta.url), wrapper);
    const shell = Bun.which("powershell.exe");
    assert(shell, "PowerShell is required for the Windows wrapper smoke");
    const run = (args: string[]) => Bun.spawnSync([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", wrapper, "-Bun", process.execPath, ...args], { env: { ...process.env, INSTALL_RECORD: record }, stdout: "pipe", stderr: "pipe", windowsHide: true });
    const first = run(["-Target", "Desktop", "-AuthSource", "Existing-Vibe", "-Auto", "-Model", "vibe-oidc/mistral-large-4", "-Restart", "-Connect", "-SkipPrepare"]);
    assert.equal(first.exitCode, 0, first.stderr.toString());
    const args: string[] = JSON.parse(readFileSync(record, "utf8"));
    for (const value of ["--target", "desktop", "--auth-source", "existing-vibe", "--yes", "--model", "vibe-oidc/mistral-large-4", "--restart", "--connect", "--skip-prepare"]) assert(args.includes(value), value);
    rmSync(record);
    assert.equal(run(["-Target", "Both", "-Yes"]).exitCode, 0);
    assert(JSON.parse(readFileSync(record, "utf8")).includes("both"));
    rmSync(record);
    const conflict = run(["-Interactive", "-Auto"]);
    assert.notEqual(conflict.exitCode, 0);
    assert(conflict.stderr.toString().includes("cannot be combined"));
    assert(!existsSync(record));
    console.log("PASS PowerShell wrapper forwarding, case normalization, Auto/Yes and conflicting modes (recording installer only).");
  } else {
    const wrapper = join(dir, "install.sh");
    copyFileSync(new URL("../../../install.sh", import.meta.url), wrapper);
    const result = Bun.spawnSync(["bash", wrapper, "--bun", process.execPath, "--target", "both", "--yes", "--auth-source", "existing-vibe", "--model", "vibe-oidc/mistral-large-4", "--restart", "--connect", "--skip-prepare"], { env: { ...process.env, INSTALL_RECORD: record }, stdout: "pipe", stderr: "pipe" });
    assert.equal(result.exitCode, 0, result.stderr.toString());
    const args: string[] = JSON.parse(readFileSync(record, "utf8"));
    for (const value of ["--target", "both", "--yes", "--auth-source", "existing-vibe", "--model", "vibe-oidc/mistral-large-4", "--restart", "--connect", "--skip-prepare"]) assert(args.includes(value), value);
    console.log("PASS shell wrapper forwarding (recording installer only).");
  }
} finally { rmSync(dir, { recursive: true, force: true }); }
