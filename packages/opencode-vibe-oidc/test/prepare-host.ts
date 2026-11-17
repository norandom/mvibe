import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { resolve } from "node:path";

const source = process.env.OPENCODE_TEST_SOURCE;
if (!source) throw new Error("Set OPENCODE_TEST_SOURCE to a prepared OpenCode 2.3.0 executable; see README.md");
const version = Bun.spawnSync([source, "--version"], { env: {}, timeout: 10_000 });
if (version.exitCode !== 0 || !/\bv2\.3\.0\b/.test(version.stdout.toString())) throw new Error("Expected verified OpenCode v2.3.0; updated hosts need a separate compatibility check");
mkdirSync(".host", { recursive: true });
const target = resolve(process.platform === "win32" ? ".host/opencode.exe" : ".host/opencode");
copyFileSync(source, target);
writeFileSync(".host/provenance.json", JSON.stringify({ version: version.stdout.toString().trim(), sha256: createHash("sha256").update(readFileSync(target)).digest("hex") }, null, 2));
console.log(`Prepared isolated test binary: ${target}`);
