import { test, expect } from "bun:test";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import type { Plugin } from "@opencode/plugin";

import { createOpenAICompatible } from "@ai-sdk/openai-compatible";
import fixture from "./synthetic-plugin.js";

test("adapter model uses the V3 contract required by the V2 host", () => {
  const model = createOpenAICompatible({ name: "setup", baseURL: "http://127.0.0.1:1" }).chatModel("synthetic");
  expect(model.specificationVersion).toBe("v3");
});

test("synthetic setup returns cleanup disposing each registration", async () => {
  const root = mkdtempSync(join(tmpdir(), "vibe-sdk-contract-"));
  const disposed: string[] = [];
  const registration = (name: string) => Promise.resolve({ dispose: async () => { disposed.push(name); } });
  const context = {
    options: { evidence: join(root, "events") },
    provider: { transform: () => registration("provider") },
    integration: { transform: () => registration("integration") },
    aisdk: { hook: (_name: string, _callback: unknown, options: { providerID: string }) => {
      expect(options.providerID).toBe("vibe-setup-synthetic");
      return registration("sdk");
    } },
  } as unknown as Plugin.Context;
  try {
    const cleanup = await fixture.setup(context);
    expect(typeof cleanup).toBe("function");
    if (cleanup) await cleanup();
    expect(disposed).toEqual(["sdk", "integration", "provider"]);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

