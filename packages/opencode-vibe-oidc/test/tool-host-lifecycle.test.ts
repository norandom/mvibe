import { test, expect } from "bun:test";
import { resolve, join } from "node:path";
import { requireOwnedPath, requireLocation, offlineEnvironment, requireBudget, parseDebugPaths, validateDebugPaths, smokeOfflineHost, waitForLocationPlugins } from "./tool-compatibility/runner.js";

const activationError = () => Object.assign(new Error("Location plugins did not finish activating"), { _tag: "ServiceUnavailableError", service: "plugin" });
test("startup RPC retries cold plugin activation and preserves other errors", async () => {
  const controller = new AbortController();
  let calls = 0;
  expect(await waitForLocationPlugins(async () => { if (++calls === 1) throw activationError(); return "ready"; }, controller.signal)).toBe("ready");
  expect(calls).toBe(2);
  for (const error of [new Error("unrelated"), Object.assign(activationError(), { service: "other" }), Object.assign(activationError(), { _tag: "OtherError" }), Object.assign(activationError(), { message: "another plugin failure" })]) {
    calls = 0;
    await expect(waitForLocationPlugins(async () => { calls++; throw error; }, controller.signal)).rejects.toBe(error);
    expect(calls).toBe(1);
  }
});
test("cold activation retry stops at the existing startup abort budget", async () => {
  const controller = new AbortController();
  let calls = 0;
  await expect(waitForLocationPlugins(async () => { calls++; controller.abort(); throw activationError(); }, controller.signal)).rejects.toThrow("HOST_READINESS");
  expect(calls).toBe(1);
  await expect(waitForLocationPlugins(async () => { calls++; return "unused"; }, controller.signal)).rejects.toThrow("HOST_READINESS");
  expect(calls).toBe(1);
});

test("all private host directories are owned and inherited secrets/config are scrubbed", () => {
  const root = resolve("owned-root");
  const env = offlineEnvironment(root, { PATH: "safe", MISTRAL_API_KEY: "private", OPENCODE_CONFIG: "private", OPENCODE_DISABLE_MODELS_FETCH: "false", HOME: "private" });
  expect(env.MISTRAL_API_KEY).toBeUndefined(); expect(env.OPENCODE_CONFIG).toBeUndefined();
  expect(env.OPENCODE_DISABLE_MODELS_FETCH).toBe("true"); expect(env.HOME).toBe(root);
  for (const key of ["XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "TEMP", "TMP", "TMPDIR", "APPDATA", "LOCALAPPDATA"]) expect(requireOwnedPath(root, env[key]!)).toBe(env[key]!);
});
test("debug paths require every actual host key, owned destinations and bounded inputs", () => {
  const root = resolve("owned-root");
  const lines = ["home", "data", "cache", "config", "state", "tmp", "bin", "log", "repos", "db"].map(key => key + "       " + (key === "home" ? root : join(root, key)));
  const paths = parseDebugPaths(lines.join("\r\n"));
  validateDebugPaths(root, paths);
  expect(() => parseDebugPaths(lines.slice(1).join("\n"))).toThrow("HOST_ISOLATION");
  expect(() => parseDebugPaths([...lines, lines[0]].join("\n"))).toThrow("HOST_ISOLATION");
  expect(() => validateDebugPaths(root, { ...paths, tmp: resolve("private") })).toThrow("HOST_ISOLATION");
  expect(() => validateDebugPaths(root, {})).toThrow("HOST_ISOLATION");
  expect(() => validateDebugPaths(root, { data: 2 })).toThrow("HOST_ISOLATION");
  for (const value of [NaN, Infinity, 0, -1, 60_001, 100.5]) expect(() => requireBudget(value)).toThrow("HOST_BUDGET");
  requireBudget(100); requireBudget(60_000);
});
test("pre-aborted lifecycle creates no resources or subprocess", async () => {
  const controller = new AbortController(); controller.abort();
  await expect(smokeOfflineHost({ host: "unused", python: "unused", bun: "unused", startupMs: 100, executionMs: 100, cleanupMs: 100 }, controller.signal)).rejects.toThrow("HOST_CANCELLED");
});
test("reject sibling, traversal, relative paths and client location fallback", () => {
  const root = resolve("owned-root");
  expect(requireOwnedPath(root, join(root, "cwd"))).toBe(join(root, "cwd"));
  for (const target of [root, root + "-sibling", resolve(root, "..", "private"), "relative"]) expect(() => requireOwnedPath(root, target)).toThrow("HOST_ISOLATION");
  requireLocation(root, root);
  expect(() => requireLocation(root, resolve("private"))).toThrow("HOST_LOCATION");
});
