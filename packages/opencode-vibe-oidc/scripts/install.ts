import { existsSync, lstatSync, readFileSync, mkdirSync, openSync, writeFileSync, fsyncSync, closeSync, renameSync, unlinkSync, realpathSync } from "node:fs";
import { dirname, join, resolve, isAbsolute } from "node:path";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";

class InstallerError extends Error {}

interface InstallOptions { packagePath: string; projectPath: string; uvPath: string }
function object(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }
export function selectConfigPath(directory: string): string {
  return join(directory, existsSync(join(directory, "opencode.jsonc")) ? "opencode.jsonc" : existsSync(join(directory, "opencode.json")) ? "opencode.json" : "opencode.jsonc");
}

/** Parse fully before touching disk; edit only owned JSON paths, retaining surrounding comments. */
export async function installConfig(configPath: string, options: InstallOptions): Promise<{ changed: boolean; backup?: string }> {
  const { parse, modify, applyEdits } = await import("jsonc-parser");
  const present = existsSync(configPath);
  if (present && (!lstatSync(configPath).isFile() || lstatSync(configPath).isSymbolicLink())) throw new InstallerError("Config must be a regular file, not a symbolic link.");
  const original = present ? readFileSync(configPath, "utf8") : "{}\n";
  const errors: import("jsonc-parser").ParseError[] = [];
  const config: unknown = parse(original, errors, { allowTrailingComma: true, disallowComments: false });
  if (errors.length || !object(config)) throw new InstallerError("Invalid OpenCode JSON/JSONC config; no changes made.");
  if (config.plugins !== undefined && !Array.isArray(config.plugins)) throw new InstallerError("OpenCode plugins must be an array; no changes made.");
  const plugins: unknown[] = config.plugins ?? [];
  if (plugins.some(p => typeof p !== "string" && (!object(p) || typeof p.package !== "string" || (p.options !== undefined && !object(p.options))))) throw new InstallerError("Invalid plugin entry; no changes made.");
  let edited = original;
  const formattingOptions = { insertSpaces: true, tabSize: 2, eol: original.includes("\r\n") ? "\r\n" : "\n" };
  const edit = (path: (string | number)[], value: unknown) => { edited = applyEdits(edited, modify(edited, path, value, { formattingOptions })); };
  const matches = plugins.flatMap((p, i) => (typeof p === "string" ? p : object(p) ? p.package : undefined) === options.packagePath ? [i] : []);
  const helperCommand = { executable: options.uvPath, args: ["run", "--offline", "--no-sync", "--project", options.projectPath, "-m", "mvibe_auth", "bridge"] };
  const index = matches[0] ?? plugins.length;
  if (matches.length === 0 || typeof plugins[index] === "string") {
    edit(["plugins", index], { package: options.packagePath, options: { audience: "https://api.mistral.ai", helperCommand } });
  } else {
    const entry = plugins[index] as Record<string, unknown>;
    if (entry.options === undefined) edit(["plugins", index, "options"], {});
    const previous = object(entry.options) ? entry.options : {};
    if (previous.audience !== "https://api.mistral.ai") edit(["plugins", index, "options", "audience"], "https://api.mistral.ai");
    if (JSON.stringify(previous.helperCommand) !== JSON.stringify(helperCommand)) edit(["plugins", index, "options", "helperCommand"], helperCommand);
  }
  for (const duplicate of matches.slice(1).reverse()) edit(["plugins", duplicate], undefined);
  if (edited === original && present) return { changed: false };
  mkdirSync(dirname(configPath), { recursive: true, mode: 0o700 });
  const temporary = join(dirname(configPath), `.mvibe-install-${randomUUID()}.tmp`);
  let backup: string | undefined;
  try {
    const fd = openSync(temporary, "wx", present ? lstatSync(configPath).mode & 0o777 : 0o600);
    try { writeFileSync(fd, edited, "utf8"); fsyncSync(fd); } finally { closeSync(fd); }
    if (present) {
      if (readFileSync(configPath, "utf8") !== original) throw new InstallerError("Config changed during installation; retry.");
      backup = `${configPath}.backup-${new Date().toISOString().replace(/[:.]/g, "-")}-${randomUUID()}`;
      writeFileSync(backup, original, { encoding: "utf8", mode: 0o600, flag: "wx" });
    } else if (existsSync(configPath)) throw new InstallerError("Config appeared during installation; retry.");
    renameSync(temporary, configPath);
  } finally { if (existsSync(temporary)) unlinkSync(temporary); }
  return backup === undefined ? { changed: true } : { changed: true, backup };
}

const help = `Install the direct Mistral-Vibe provider into OpenCode 2.
Options: --config-path FILE --uv EXECUTABLE --bun EXECUTABLE --opencode EXECUTABLE
         --skip-prepare (use an already prepared installation) --help
Prerequisites: native uv, Bun 1.4.4, OpenCode 2. Preparation may download locked dependencies.
No login, proxy, tool installation or application restart is performed.`;

export interface InstallerRuntime {
  which(requested: string): string | null;
  run(command: string[], cwd: string): { exitCode: number; stdout: string };
}
const nativeRuntime: InstallerRuntime = {
  which: requested => Bun.which(requested),
  run: (command, cwd) => { const result = Bun.spawnSync(command, { cwd, stdout: "pipe", stderr: "pipe", windowsHide: true }); return { exitCode: result.exitCode, stdout: result.stdout.toString() }; },
};
export async function runInstaller(args: string[], runtime: InstallerRuntime = nativeRuntime): Promise<void> {
  if (args.includes("--help")) { console.log(help); return; }
  const flags = new Map<string, string>(); let skip = false;
  for (let i = 0; i < args.length; i++) {
    const key = args[i]!;
    if (key === "--skip-prepare") { skip = true; continue; }
    if (!["--config-path", "--uv", "--bun", "--opencode"].includes(key) || !args[i + 1] || args[i + 1]!.startsWith("--")) throw new InstallerError("Unknown or incomplete installer option. Use --help.");
    flags.set(key, args[++i]!);
  }
  const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
  const packagePath = join(root, "packages", "opencode-vibe-oidc");
  function executable(flag: string, fallback: string): string {
    const requested = flags.get(flag) ?? fallback;
    const found = runtime.which(requested);
    if (!found || /[\\/]WindowsApps[\\/]/i.test(found)) throw new InstallerError(`Missing native ${fallback} executable; install it explicitly or supply ${flag}.`);
    return realpathSync(found);
  }
  const uv = executable("--uv", "uv"), bun = executable("--bun", "bun"), opencode = executable("--opencode", "opencode");
  const uvHeader = readFileSync(uv).subarray(0, 4).toString("hex");
  if (!/^(4d5a|7f454c46|feedface|feedfacf|cefaedfe|cffaedfe|cafebabe)/.test(uvHeader)) throw new InstallerError("uv must be a native executable, not a script or Python alias.");
  function run(command: string[], cwd = root): string {
    const result = runtime.run(command, cwd);
    if (result.exitCode !== 0) throw new InstallerError(`Installer prerequisite/preparation failed: ${command[0]?.split(/[\\/]/).pop()} ${command[1] ?? ""}. No configuration was written.`);
    return result.stdout.trim();
  }
  if (!/^uv\s+\d/.test(run([uv, "--version"]))) throw new InstallerError("Expected native uv executable.");
  if (run([bun, "--version"]) !== "1.4.4") throw new InstallerError("Bun 1.4.4 is required for the locked plugin build.");
  if (!/^(?:opencode\s+v?)?2\.\d+\.\d+(?:\s|$|[-+])/i.test(run([opencode, "--version"]))) throw new InstallerError("OpenCode major version 2 is required.");
  let configPath = flags.get("--config-path");
  if (configPath) configPath = resolve(configPath);
  else {
    const configDir = run([opencode, "debug", "paths", "config"]);
    if (!isAbsolute(configDir) || /[\r\n]/.test(configDir)) throw new InstallerError("OpenCode returned an invalid config directory; use --config-path.");
    configPath = selectConfigPath(configDir);
  }
  if (!skip) {
    const release = existsSync(join(root, "release.json"));
    if (release) run([uv, "sync", "--locked", "--python", "3.12", "--no-dev", "--no-build"]);
    else {
      run([uv, "sync", "--locked", "--python", "3.12"]);
      run([bun, "install", "--frozen-lockfile", "--ignore-scripts"], packagePath);
      run([bun, "run", "typecheck"], packagePath);
      run([bun, "run", "build"], packagePath);
    }
  }
  if (!existsSync(join(packagePath, "dist", "index.js"))) throw new InstallerError("Built plugin missing; run preparation without --skip-prepare.");
  run([uv, "run", "--offline", "--no-sync", "--project", root, "python", "-c", "import sys, mvibe_auth; assert sys.version_info >= (3, 12)"]);
  const result = await installConfig(configPath, { packagePath, projectPath: root, uvPath: uv });
  console.log(result.changed ? "Installed Mistral-Vibe direct provider. Configuration backup created when replacing an existing file." : "Mistral-Vibe direct provider is already installed; configuration unchanged.");
  console.log("Restart OpenCode to load the plugin. Connect Mistral-Vibe, then open /models. CLI: opencode auth login vibe-oidc --method vibe-browser --standalone");
}
if (import.meta.main) runInstaller(process.argv.slice(2)).catch((error: unknown) => { console.error(error instanceof InstallerError ? error.message : "Installation failed. Check native uv, Bun 1.4.4, OpenCode 2, prepared dependencies and valid writable JSON/JSONC configuration. No config values were logged."); process.exitCode = 1; });
