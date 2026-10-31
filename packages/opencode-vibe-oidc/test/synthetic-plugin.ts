// Test-only fixture: no Vibe authentication or production provider implementation.
import { Plugin, Provider, Model } from "@opencode/plugin";
import { createOpenAICompatible } from "@ai-sdk/openai-compatible";
import { appendFileSync } from "node:fs";

export default Plugin.define({
  id: "vibe-setup-synthetic",
  async setup(ctx) {
    const evidence = ctx.options.evidence;
    if (typeof evidence !== "string") throw new Error("Synthetic setup requires an evidence file");
    const record = (event: string) => appendFileSync(evidence, event + "\n");
    const providerID = Provider.ID.make("vibe-setup-synthetic");
    const provider = await ctx.provider.transform(editor => {
      editor.add({
        info: { ...Provider.Info.empty(providerID), name: "Synthetic setup only", activation: "enabled", package: "@ai-sdk/openai-compatible" },
        models: [{ ...Model.Info.default(providerID, Model.ID.make("setup-model")), name: "Synthetic setup model" }],
      });
      record("provider-transform");
    });
    const integration = await ctx.integration.transform(editor => {
      editor.method.update({ integrationID: "vibe-setup-synthetic", method: {
        id: "synthetic-marker", type: "command", label: "Synthetic marker only",
        command: ["bun", "-e", "process.stdout.write('synthetic-nonsecret-marker')"],
      } });
      record("integration-transform");
    });
    const sdk = await ctx.aisdk.hook("sdk", input => {
      input.sdk = createOpenAICompatible({ name: "synthetic", baseURL: "http://127.0.0.1:1/v1", fetch: Object.assign(async () => { throw new Error("Synthetic setup forbids inference"); }, { preconnect: () => {} }) });
      record("sdk-hook");
    }, { providerID });
    record("sdk-hook-registered");
    record("setup-complete");
    return async () => {
      await sdk.dispose();
      await integration.dispose();
      await provider.dispose();
      record("cleanup-complete");
    };
  },
});
