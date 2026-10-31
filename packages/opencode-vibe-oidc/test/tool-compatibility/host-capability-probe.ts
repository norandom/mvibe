/** Explicit prepared-runtime process probe; ordinary unit tests never launch it. */
import { smokeOfflineHost } from "./runner.js";
try {
  const host = process.env.OPENCODE_TEST_BINARY, python = process.env.VIBE_TEST_PYTHON;
  if (!host || !python) throw new Error("HOST_RUNTIME");
  const result = await smokeOfflineHost({host, python, bun:process.execPath, startupMs:30_000, executionMs:50_000, cleanupMs:8_000, capabilityProbe:true});
  console.log(JSON.stringify({...result, cleanup:"verified"}));
  if (!(result.capability as {ready?:boolean})?.ready) process.exitCode = 1;
} catch { console.error("HOST_CAPABILITY"); process.exitCode = 1; }
