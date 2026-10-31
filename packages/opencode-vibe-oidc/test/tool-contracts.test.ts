import { describe, expect, test } from "bun:test";
import vector from "./tool-compatibility/vectors/report-v1.json" with { type: "json" };
import { parseRunReport } from "./tool-compatibility/contracts.js";
import * as contracts from "./tool-compatibility/contracts.js";

const sample = () => structuredClone(vector.report);
describe("version 1 evidence contracts (synthetic; no execution evidence)", () => {
  test("valid vector parses into an independent deeply immutable value", () => {
    const source = sample();
    const result = parseRunReport(source);
    expect(result as unknown).toEqual(source);
    expect(result).not.toBe(source);
    expect(Object.isFrozen(result)).toBe(true);
    const callEvent = result.events[1]!;
    expect("call" in callEvent && Object.isFrozen(callEvent.call)).toBe(true);
    source.runId = "changed";
    expect(result.runId).toBe("synthetic-run");
  });
  const mutations: Record<string, (r: any) => void> = {
    "unknown report field": r => { r.secret = "PRIVATE"; },
    "unknown nested field": r => { r.events[0].headers = "PRIVATE"; },
    "raw message": r => { r.events[1].call.hostCallId = "https://login.invalid/?secret=PRIVATE"; },
    "bad hash": r => { r.scopes[0].hash = "PRIVATE"; },
    "stale version": r => { r.schemaVersion = 2; },
    "bad reason": r => { r.rows[0].live.reason = "PRIVATE"; },
    "invalid count": r => { r.events[0].sequence = -1; },
    "duplicate event": r => { r.events[1].sequence = 1; },
    "wrong run": r => { r.events[0].runId = "other"; },
    "wrong scope": r => { r.events[0].scopeHash = "b".repeat(64); },
    "wrong call": r => { r.events[3].call.hostCallId = "other"; },
    "wrong request": r => { r.events[4].requestId = "other"; },
    "wrong tool": r => { r.events[3].toolId = "other"; },
    "unknown proof": r => { r.rows[0].offline.proofIds = ["other"]; },
    "missing event": r => { r.events.pop(); },
    "oversized identifier": r => { r.runId = "x".repeat(257); },
    "wrong outcome shape": r => { r.rows[0].offline.reason = "not_exercised"; },
    "missing nonce": r => { r.events[1].call.link = "unique_fixture_nonce"; },
    "absolute private path": r => { r.scopes[0].agent = "C:/Users/PRIVATE"; },
    "cross mode PASS": r => { r.mode = "live"; },
    "cross case": r => { r.events[3].caseId = "other"; },
    "cross session": r => { r.events[3].sessionId = "other"; },
    "cross chain": r => { r.events[3].chainId = "other"; },
    "cross input hash": r => { r.events[3].call.inputHash = "b".repeat(64); },
    "cross nonce": r => { r.events[3].call.fixtureNonceHash = "b".repeat(64); },
    "wire ID mismatch": r => { r.events[4].wireCallId = "other"; },
    "origin mismatch": r => { r.events[0].originHash = "b".repeat(64); },
    "proof case mismatch": r => { r.caseResults[0].caseId = "other"; },
    "event bound": r => { r.events = Array(contracts.CONTRACT_LIMITS.events + 1).fill(r.events[0]); },
    "nonfinite sequence": r => { r.events[0].sequence = Infinity; },
    "negative classified row": r => { r.inventories[0].classifiedAbsent = [{...r.rows[0],tool:{...r.rows[0].tool,registryId:'unknown'}}]; },
    "row origin contradiction": r => { r.rows[0].tool.origin = 'mcp'; },
    "row declared name contradiction": r => { r.rows[0].tool.declaredName = 'other'; },
    "row offered name contradiction": r => { r.rows[0].tool.offeredName = 'other'; },
    "row namespace contradiction": r => { r.rows[0].tool.namespace = 'other'; },
    "inventory declared name contradiction": r => { r.inventories[0].offered[0].declaredName = 'other'; },
    "inventory offered name contradiction": r => { r.inventories[0].offered[0].offeredName = 'other'; },
    "inventory namespace contradiction": r => { r.inventories[0].offered[0].namespace = 'other'; },
  };
  for (const [name, mutate] of Object.entries(mutations)) test(name, () => {
    const report = sample(); mutate(report);
    expect(() => parseRunReport(report)).toThrow(/^invalid_report$/);
  });
  test("scope object property ordering is irrelevant", () => {
    const source = sample();
    source.inventories[0]!.scope = Object.fromEntries(Object.entries(source.scopes[0]!).reverse()) as typeof source.scopes[0];
    expect(() => parseRunReport(source)).not.toThrow();
  });
  test("registered identities can precede offering and still have unverified rows", () => {
    const source: any = sample();
    delete source.inventories[0].registered[0].offeredName;
    const tool = {...source.inventories[0].registered[0],registryId:'pending',declaredName:'pending'};
    const row = {tool,scopeHash:source.scopes[0].hash,offline:{status:'UNVERIFIED',reason:'not_exercised'},live:{status:'UNVERIFIED',reason:'not_exercised'}};
    source.inventories[0].registered.push(tool);
    source.inventories[0].classifiedAbsent.push(row);
    source.rows.push(row);
    expect(() => parseRunReport(source)).not.toThrow();
    source.inventories[0].classifiedAbsent[0].tool = {...tool,origin:'mcp'};
    expect(() => parseRunReport(source)).toThrow(/^invalid_report$/);
  });
  test("non JSON inputs fail without inspecting or leaking them", () => {
    for (const value of [null, undefined, 1, "PRIVATE", { get runId() { throw Error("PRIVATE"); } }])
      expect(() => parseRunReport(value)).toThrow(/^invalid_report$/);
  });
  test("array holes, extra fields and accessors are rejected with fixed errors", () => {
    const sparse = sample(); delete sparse.events[0];
    const extra = sample(); Object.assign(extra.events, {privateContent:'PRIVATE'});
    const accessor = sample(); Object.defineProperty(accessor.events, '0', {get() {throw Error('PRIVATE');}});
    for (const source of [sparse,extra,accessor]) expect(() => parseRunReport(source)).toThrow(/^invalid_report$/);
  });
});

describe("standalone immutable contract schemas", () => {
  const r = vector.report;
  const valid: [string, (v: unknown) => unknown, unknown][] = [
    ["tool", contracts.parseToolIdentity, r.rows[0]!.tool],
    ["scope", contracts.parseScopeFingerprint, r.scopes[0]],
    ["outcome", contracts.parseToolOutcome, r.rows[0]!.live],
    ["coverage", contracts.parseCoverageRow, r.rows[0]],
    ["run", contracts.parseRunSpec, vector.runSpec],
    ["case", contracts.parseCaseSpec, vector.caseSpec],
    ["cancellation case", contracts.parseCaseSpec, vector.cancelledCase],
    ["inventory", contracts.parseInventorySnapshot, r.inventories[0]],
    ["diff", contracts.parseInventoryDiff, {missingCases:[],changedSchemas:[]}],
    ["call", contracts.parseCallCorrelation, r.events[1]!.call],
    ["event", contracts.parseTraceEvent, r.events[1]],
    ["proof", contracts.parseRoundTripProof, r.proofs[0]],
    ["cancel proof", contracts.parseCancelProof, vector.cancelProof],
    ["case result", contracts.parseCaseResult, r.caseResults[0]],
    ["claim", contracts.parseScopedClaim, {scopeHash:r.scopes[0]!.hash,mode:'offline',supported:['read'],complete:false}],
    ["gate", contracts.parseGateResult, {claims:[],errors:['incomplete_evidence']}],
    ["protocol profile", contracts.parseToolProtocolProfile, vector.profile],
  ];
  for (const [name, parse, value] of valid) {
    test(`${name}: valid, frozen, unknown fields rejected`, () => {
      expect(Object.isFrozen(parse(value))).toBe(true);
      expect(() => parse({...value as object, privateContent:'PRIVATE'})).toThrow(/^invalid_report$/);
    });
  }
  test("positive budgets and path constraints", () => {
    for (const ms of [0,-1,1.5,Infinity,contracts.CONTRACT_LIMITS.milliseconds+1])
      expect(() => contracts.parseRunSpec({...vector.runSpec,limits:{...vector.runSpec.limits,startupMs:ms}})).toThrow(/^invalid_report$/);
    for (const path of ['../private','/private','C:/private','https://private','a/../b','a\\b','a\nprivate'])
      expect(() => contracts.parseToolProtocolProfile({...vector.profile,evidenceRefs:[path]})).toThrow(/^invalid_report$/);
  });
  test("cancellation variants retain separate host/executor/provider/MCP observations", () => {
    const source: any = sample();
    const base = source.events[0];
    for (const [i,kind] of ['host_cancelled','executor_released','provider_aborted','fixture_cancelled','state_observed'].entries()) {
      const e = {...base,sequence:7+i,kind};
      if (kind !== 'state_observed') { delete e.toolId; delete e.toolSchemaHash; }
      source.events.push(e);
    }
    source.caseResults.push({...source.caseResults[0],proofId:undefined,observedOutcome:'cancelled',cancellation:vector.cancelProof,eventReferences:[7,8,9,10,11]});
    delete source.caseResults[1].proofId;
    expect(() => parseRunReport(source)).not.toThrow();
    source.caseResults[1].cancellation = {...vector.cancelProof,mcpEvent:9};
    expect(() => parseRunReport(source)).toThrow(/^invalid_report$/);
  });
  test("contradictory supplied cancellation calls and request contexts are rejected", () => {
    const source: any = sample();
    const base = source.events[0];
    for (const [i,kind] of ['host_cancelled','executor_released','provider_aborted','fixture_cancelled','state_observed'].entries()) {
      const e = {...base,sequence:7+i,kind};
      if (kind !== 'state_observed') { delete e.toolId; delete e.toolSchemaHash; e.call = {...source.events[1].call}; }
      source.events.push(e);
    }
    source.caseResults.push({...source.caseResults[0],observedOutcome:'cancelled',cancellation:vector.cancelProof,eventReferences:[2,7,8,9,10,11]});
    delete source.caseResults[1].proofId;
    expect(() => parseRunReport(source)).not.toThrow();
    for (const [field,value] of Object.entries({hostMessageId:'other',hostCallId:'other',upstreamCallId:'other',inputHash:'b'.repeat(64),fixtureNonceHash:'b'.repeat(64)})) {
      const changed = structuredClone(source); changed.events[7].call[field] = value;
      expect(() => parseRunReport(changed)).toThrow(/^invalid_report$/);
    }
    for (const field of ['requestId','requestKind']) {
      const changed = structuredClone(source); changed.events[7][field] = field === 'requestKind' ? 'title' : 'other';
      expect(() => parseRunReport(changed)).toThrow(/^invalid_report$/);
    }
    const wrongKnownCall = structuredClone(source);
    for (const e of wrongKnownCall.events.slice(6,10)) e.call.hostCallId = 'other';
    expect(() => parseRunReport(wrongKnownCall)).toThrow(/^invalid_report$/);
    const wrongKnownRequest = structuredClone(source);
    for (const e of wrongKnownRequest.events.slice(6,10)) e.requestId = 'other';
    expect(() => parseRunReport(wrongKnownRequest)).toThrow(/^invalid_report$/);
  });
});
