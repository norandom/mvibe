// Real host command/credential contract fixture, using production registration.
import { Plugin } from "@opencode/plugin";
import { setupVibe } from "../src/index.js";
import { writeFileSync, existsSync, readFileSync, unlinkSync } from "node:fs";
import assert from "node:assert/strict";

export default Plugin.define({ id: "vibe-login-contract-test", async setup(ctx) {
  const cleanup = await setupVibe(ctx);
  const modeFile = ctx.options.modeFile, evidence = ctx.options.evidence;
  if (typeof modeFile !== "string" || typeof evidence !== "string") throw new Error("Isolated fixture configuration required");
  const run = async () => {
    const results: string[] = [];
    for (const mode of [String(ctx.options.mode)]) {
      writeFileSync(modeFile, mode);
      const pidFile: string = modeFile + ".pid";
      if (existsSync(pidFile)) unlinkSync(pidFile);
      const attempt = await ctx.integration.command.connect({ integrationID: "vibe-oidc", methodID: "vibe-browser", label: mode });
      const input = { integrationID: "vibe-oidc", attemptID: attempt.data.attemptID };
      if (mode === "cancelled") {
        for (let i = 0; i < 100 && !existsSync(pidFile); i++) await new Promise(resolve => setTimeout(resolve, 25));
        assert.ok(existsSync(pidFile), "Python command must be active before cancellation");
        const pid = Number(readFileSync(pidFile, "utf8"));
        await ctx.integration.command.cancel(input);
        await assert.rejects(ctx.integration.command.status(input), error => String(error).includes("AttemptNotFound"));
        let alive = true;
        for (let i = 0; i < 100 && alive; i++) {
          try { process.kill(pid, 0); } catch { alive = false; }
          if (alive) await new Promise(resolve => setTimeout(resolve, 25));
        }
        if (alive) { process.kill(pid, "SIGKILL"); throw new Error("Host cancellation left Python authentication runtime alive"); }
        results.push("cancelled:removed");
        continue;
      }
      let status = "pending";
      let detail = "";
      for (let i = 0; i < 100 && status === "pending"; i++) {
        await new Promise(resolve => setTimeout(resolve, 50));
        const current = (await ctx.integration.command.status(input)).data;
        status = current.status;
        detail = JSON.stringify(current);
      }
      assert.equal(status, mode === "failed" ? "failed" : "complete", detail);
      results.push(mode + ":" + status);
    }
    const connection = await ctx.integration.connection.active("vibe-oidc");
    assert.ok(connection);
    const value = await ctx.integration.connection.resolve(connection);
    assert.deepEqual(value, { type: "key", key: "vibe-oidc-connected-v1" });
    writeFileSync(evidence, JSON.stringify({ results, value }));
  };
  try { await run(); }
  catch (error) { writeFileSync(evidence, String(error)); throw error; }
  return cleanup;
} });
