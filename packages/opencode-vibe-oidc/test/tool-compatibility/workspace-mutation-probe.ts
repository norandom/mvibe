/** Deliberate native mutation probe; profiles are owned synthetic metadata only. */
import { smokeOfflineHost } from "./runner.js";
try {
 const host=process.env.OPENCODE_TEST_BINARY,python=process.env.VIBE_TEST_PYTHON;
 if(!host||!python)throw new Error("HOST_RUNTIME");
 const result=await smokeOfflineHost({host,python,bun:process.execPath,startupMs:30_000,executionMs:60_000,cleanupMs:8_000,workspaceMutationProbe:true});
 console.log(JSON.stringify({...result,cleanup:"verified"}));
 if(!result.ready)process.exitCode=1;
} catch(e){console.error(e instanceof Error&&/^HOST_[A-Z_]+$/.test(e.message)?e.message:"HOST_WORKSPACE");process.exitCode=1;}
