/** Explicit offline MCP client integration; excluded from ordinary pure tests. */
import { rejectCrossedMcpEvidence } from "./mcp-report.js";
import { smokeOfflineHost } from "./runner.js";
if(import.meta.main){try{
 const host=process.env.OPENCODE_TEST_BINARY,python=process.env.VIBE_TEST_PYTHON;if(!host||!python)throw new Error("HOST_RUNTIME");
 const result=await smokeOfflineHost({host,python,bun:process.execPath,startupMs:30000,executionMs:60000,cleanupMs:8000,mcpProbe:true});
 const rejectedArtifactMutations=result.ready?rejectCrossedMcpEvidence((result as any).mcp):0;
 console.log(JSON.stringify({...result,rejectedArtifactMutations,cleanup:"verified"}));if(!result.ready)process.exitCode=1;
}catch(e){console.error(e instanceof Error?e.message:"HOST_MCP");process.exitCode=1;}}
