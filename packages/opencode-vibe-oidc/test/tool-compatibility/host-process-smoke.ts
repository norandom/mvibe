/** Deliberate real-process command, excluded from ordinary bun test discovery. */
import { smokeOfflineHost } from "./runner.js";
try {
  const host = process.env.OPENCODE_TEST_BINARY, python = process.env.VIBE_TEST_PYTHON;
  if (!host || !python) throw new Error("HOST_RUNTIME");
  const options = { host, python, bun: process.execPath, startupMs: 30_000, executionMs: 10_000, cleanupMs: 8_000 };
  if (process.argv.includes("--cancel")) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 100);
    try {
      await smokeOfflineHost(options, controller.signal);
      throw new Error("HOST_CANCEL_EXPECTED");
    } catch (error) {
      if (!(error instanceof Error) || error.message !== "HOST_READINESS") throw error;
      console.log(JSON.stringify({ cancellation: "observed", cleanup: "verified", live: "unverified" }));
    } finally { clearTimeout(timer); }
  } else {
    const result = await smokeOfflineHost(options);
    console.log(JSON.stringify({ ...result, cleanup: "verified" }));
  }
} catch (error) { console.error(error instanceof Error && /^HOST_[A-Z_]+$/.test(error.message) ? error.message : "HOST_FAILURE"); process.exitCode = 1; }
