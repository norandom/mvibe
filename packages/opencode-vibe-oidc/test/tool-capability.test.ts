import { test, expect } from "bun:test";
import { capabilityDecision, expectedNativeRead, parseCapabilityReport, type CapabilityEvent } from "./tool-compatibility/capability.js";
const events = (denied = false): CapabilityEvent[] => ["context", "transport_request", "transport_chunk", "input_started", "transport_end", "input_ended", "dispatch", denied ? "permission_deny" : "permission_allow", denied ? "error" : "completed", "context", "transport_request", "continuation", "transport_chunk", "transport_end", "final", "state_unchanged"].map(kind => ({ kind: kind as CapabilityEvent["kind"], case: denied ? "denied" : "allowed", link: denied ? "owned-denied" : "owned-allowed" }));
test("capability requires actual completed allowed and host-denied continued chains", () => {
  expect(capabilityDecision([...events(), ...events(true)])).toBe("observed");
});
test("missing dispatch, completion, denial, transport end or continuation is unsupported", () => {
  for (const kind of ["dispatch", "completed", "permission_deny", "transport_end", "continuation"] as const)
    expect(capabilityDecision([...events(), ...events(true)].filter(e => e.kind !== kind))).toBe("hook_unavailable");
});
test("crossed call links and denial execution cannot earn capability", () => {
  const crossed = [...events(), ...events(true)]; crossed[8] = {...crossed[8]!, link: "other-call"};
  expect(capabilityDecision(crossed)).toBe("hook_unavailable");
  expect(capabilityDecision([...events(), ...events(true), {kind:"completed", case:"denied", link:"owned-call"}])).toBe("hook_unavailable");
});
test("pinned read expectation comes only from owned fixture inputs", () => {
  expect(expectedNativeRead("C:\\owned\\read.txt", "12345678-1234-1234-1234-123456789abc")).toBe("Read file C:\\owned\\read.txt, lines 1-1\n1: 12345678-1234-1234-1234-123456789abc");
  expect(()=>expectedNativeRead("private\npath", "not-a-nonce")).toThrow("HOST_FIXTURE");
});
// Parser vectors are deliberately not execution evidence.
function report(){const h="a".repeat(64);return {ready:true,status:"observed",code:null,stage:"decision",inventory:[{id:"read",name:"read",schemaHash:h}],offered:["allowed","allowed","denied","denied"].map(c=>({case:c,agent:"build",definitions:[{name:"read",schemaHash:h}]})),permissions:[{case:"allowed",action:"read",effect:"allow",owned:true}],counts:{sdk:1,language:1,httpRequest:0,httpResponse:0,requests:4,chunks:68,ends:4,auxiliary:0},events:[...events(),...events(true)],failureCode:"none",links:["allowed","denied"].map(c=>({case:c,sessionHash:h,upstreamCallHash:h,hostCallHash:h,expectedResultHash:h,actualResultHash:h})),profileHash:h,providerBuildHash:h,transport:"fixture-owned-before-provider",denialSource:"execute.after.error.error.Permission.BlockedError",auxiliary:"unverified",live:"unverified",allToolCompatibility:"unverified"};}
test("capability boundary accepts bounded hashes and rejects raw capture/forged joins",()=>{
  expect(parseCapabilityReport(report()).ready).toBe(true);
  expect(()=>parseCapabilityReport({...report(),rawRequest:"private"})).toThrow("HOST_CAPABILITY_REPORT");
  const crossed=report();crossed.links[0]!.hostCallHash="b".repeat(64);
  expect(()=>parseCapabilityReport(crossed)).toThrow("HOST_CAPABILITY_REPORT");
  expect(()=>parseCapabilityReport({...report(),events:[]})).toThrow("HOST_CAPABILITY_REPORT");
});
test("stream buffering and permission-before-dispatch cannot pass the observed ordering decision",()=>{
  const input=events(), start=input.find(e=>e.kind==="input_started")!;
  const buffered=input.filter(e=>e!==start);buffered.push(start);
  expect(capabilityDecision([...buffered,...events(true)])).toBe("hook_unavailable");
  const permission=input.find(e=>e.kind==="permission_allow")!;
  expect(capabilityDecision([permission,...input.filter(e=>e!==permission),...events(true)])).toBe("hook_unavailable");
});
