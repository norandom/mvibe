import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { checkPreparedDependencies, requireReferenceHostVersion } from "./tool-compatibility/preparation.js";

const root = resolve(import.meta.dir, "..");
const read = (path: string): string => readFileSync(resolve(root, path), "utf8");

describe("prepared tool compatibility dependencies", () => {
  test("exact fixture and type versions are declared, locked and installed", () => {
    const manifest = JSON.parse(read("package.json"));
    expect(manifest.devDependencies["@modelcontextprotocol/sdk"]).toBe("1.32.2");
    expect(manifest.devDependencies["@ai-sdk/provider"]).toBe("3.0.19");
    expect(manifest.dependencies).toEqual({
      "@ai-sdk/openai-compatible": "2.0.88", "@opencode/plugin": "2.2.5", "jsonc-parser": "3.3.1",
    });
    expect(manifest.devDependencies["@opencode/cli"]).toBe("2.2.5");
    expect(manifest.devDependencies.typescript).toBe("5.9.3");
    expect(() => checkPreparedDependencies(read, Bun.version)).not.toThrow();
  });

  test("missing installation gives an actionable preparation error", () => {
    expect(() => checkPreparedDependencies((path) => {
      if (path.startsWith("node_modules/")) throw new Error("private rejected contents");
      return read(path);
    }, Bun.version)).toThrow("Run bun install --frozen-lockfile explicitly before offline checks");
  });

  test("changed installed dependency cannot reuse earlier preparation", () => {
    expect(() => checkPreparedDependencies((path) => path.startsWith("node_modules/")
      ? '{"version":"0.0.0"}' : read(path), Bun.version)).toThrow("Prepared dependency version mismatch");
  });

  test("manifest/lock drift and Bun updates require preparation", () => {
    expect(() => checkPreparedDependencies((path) => path === "bun.lock"
      ? read(path).replace('"1.32.2"', '"1.32.3"') : read(path), Bun.version)).toThrow("Manifest/lock mismatch");
    expect(() => checkPreparedDependencies(read, "1.4.5")).toThrow("Prepare pinned Bun 1.4.4");
  });

  test("reference host requires exact version, not a matching major or prefix", () => {
    expect(requireReferenceHostVersion("opencode v2.2.5\n")).toBe("2.2.5");
    expect(requireReferenceHostVersion("2.2.5\n")).toBe("2.2.5");
    for (const version of ["v2.2.6", "v2.2.50", "v2.2.5-beta.1", "private rejected contents"])
      expect(() => requireReferenceHostVersion(version)).toThrow("Prepare OpenCode 2.2.5; other versions require separate compatibility evidence");
  });

  test("default unit configuration keeps process integration deliberately invoked", () => {
    const manifest = JSON.parse(read("package.json"));
    expect(manifest.scripts.test).toBe("bun test");
    expect(manifest.scripts["check:tool-runtimes"]).toBe("bun --no-install run test/tool-compatibility/check-prepared.ts");
    expect(read("bunfig.toml")).toContain('preload = ["./test/preload.ts"]');
    expect(read("test/preload.ts")).toContain("offline test blocked external effect");
  });
});
