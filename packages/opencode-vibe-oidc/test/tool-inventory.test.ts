import { describe, expect, test } from "bun:test";
import vector from "./tool-compatibility/vectors/report-v1.json" with {type:"json"};
import { fixtureHash } from "./tool-compatibility/cases.js";
import { inventoryScope, reconcile, reconcileTurn } from "./tool-compatibility/inventory.js";

const h = "a".repeat(64);
const definition = {description:"Owned description",inputSchema:{type:"object",properties:{query:{type:"string"}},required:["query"],additionalProperties:false}};
const base = (): any => ({scope:structuredClone(vector.report.scopes[0]),sessionId:"session",requestId:"request-1",requestKind:"primary",registry:[{registryId:"read",declaredName:"read",definition:structuredClone(definition),provenance:{kind:"native",observationId:"registry-hook",configurationHash:h}}],mcp:[],offerings:[{registryId:"read",offeredName:"read_wire",definition:structuredClone(definition)}],absences:[],assignments:[]});
// Materialized structural contract vector; no executor or round-trip credit.
import { DEFAULT_CASE_MANIFEST, materializeCase } from "./tool-compatibility/cases.js";
const caseFor = (toolId: string) => {
  const manifest = DEFAULT_CASE_MANIFEST(), template = manifest.cases.find(c => c.spec.toolIds.includes("read"))!;
  const c = materializeCase(template,{recipeVersion:1,objective:template.objective,observation:{owned:true},calls:template.calls.map(id => ({toolId:id,nonce:"n".repeat(32),input:{owned:true},observation:{owned:true}}))},manifest.limits);
  return {...c,toolIds:[toolId],expectedCalls:c.expectedCalls.map(call => ({...call,toolId}))};
};
const mapped = (): any => {
  const input = base(), observed = reconcileTurn(input);
  input.assignments = observed.snapshot.offered.map(tool => ({scopeHash:input.scope.hash,tool,caseSpec:caseFor(tool.registryId)}));
  return input;
};

describe("pure per-turn inventory observations (no execution evidence)", () => {
  test("preserves explicit identity, description/constraints fingerprint and immutable observations", () => {
    const input = mapped(), result = reconcileTurn(input);
    expect(result.inventoryComplete).toBe(true);
    expect(result.snapshot.offered[0]).toEqual({registryId:"read",declaredName:"read",offeredName:"read_wire",origin:"native",originHash:h,schemaHash:fixtureHash(definition)});
    expect(result.provenance[0]?.observation.observationId).toBe("registry-hook");
    expect(Object.isFrozen(result.snapshot.offered[0])).toBe(true);
    input.registry[0].definition.description = "changed";
    expect(result.snapshot.registered[0]?.schemaHash).toBe(fixtureHash(definition));
    expect(JSON.stringify(result)).not.toContain('"PASS"');
  });
  test("description and input schema changes have concrete canonical hashes", () => {
    const a = base(), b = base(); b.registry[0].definition.inputSchema = {additionalProperties:false,required:["query"],properties:{query:{type:"string"}},type:"object"};
    expect(reconcileTurn(a).snapshot.registered[0]?.schemaHash).toBe(reconcileTurn(b).snapshot.registered[0]?.schemaHash);
    b.registry[0].definition.description = "new";
    expect(reconcileTurn(a).snapshot.registered[0]?.schemaHash).not.toBe(reconcileTurn(b).snapshot.registered[0]?.schemaHash);
  });
  test("every primary turn sees late tools and effective schema drift", () => {
    const a = mapped(), first = reconcileTurn(a), b = mapped(); b.requestId = "request-2";
    b.registry.push({...b.registry[0],registryId:"late",declaredName:"late"});
    b.offerings.push({...structuredClone(b.offerings[0]),registryId:"late",offeredName:"late"});
    b.offerings[0].definition.inputSchema.required = [];
    const second = reconcileTurn(b,first);
    expect(second.gaps).toContainEqual({code:"new_tool",toolId:"late"});
    expect(second.diff.missingCases).toContain("late");
    expect(second.diff.changedSchemas).toEqual(["read"]);
    expect(second.inventoryComplete).toBe(false);
  });
  test("never infers provenance or mappings from memory/native name prefixes", () => {
    const input = base(); input.registry[0].registryId = "claude_mem_search"; input.registry[0].provenance = {kind:"unknown",observationId:"registry-hook"};
    input.offerings[0].offeredName = "claude_mem_search"; delete input.offerings[0].registryId;
    const result = reconcileTurn(input);
    expect(result.snapshot.registered[0]?.origin).toBe("unknown");
    expect(result.gaps).toContainEqual({code:"unmapped_offering",offeredName:"claude_mem_search"});
    expect(result.gaps).toContainEqual({code:"unknown_origin",toolId:"claude_mem_search"});
    expect(result.effectiveDefinitions).toEqual([{offeredName:"claude_mem_search",schemaHash:fixtureHash(definition)}]);
  });
  test("negotiated MCP discovery preserves service namespace and actual origin", () => {
    const input = base(); input.registry = []; input.scope.negotiatedMcpVersions = ["2025-11-25"];
    const tool = {registryId:"mcp-search",declaredName:"search",namespace:"owned-memory",definition,provenance:{kind:"mcp",observationId:"tools-list",configurationHash:h}};
    input.mcp = [{serverHash:h,namespace:"owned-memory",protocolVersion:"2025-11-25",capabilitiesHash:h,observationId:"initialize",tools:[tool]}];
    input.offerings = [{registryId:"mcp-search",offeredName:"wire-search",namespace:"owned-memory",definition}];
    const result = reconcileTurn(input);
    expect(result.snapshot.offered[0]?.namespace).toBe("owned-memory");
    expect(result.snapshot.offered[0]?.origin).toBe("mcp");
    input.mcp[0].protocolVersion = "unnegotiated";
    expect(() => reconcileTurn(input)).toThrow("invalid_report");
  });
  test("missing offering alone remains unverified; classification needs scoped observations", () => {
    const input = base(); input.offerings = [];
    expect(reconcileTurn(input).snapshot.classifiedAbsent[0]?.offline).toEqual({status:"UNVERIFIED",reason:"not_exercised"});
    for (const [kind,status] of [["policy_disabled","DISABLED"],["missing_capability","UNSUPPORTED"]] as const) {
      input.absences = [{registryId:"read",scopeHash:input.scope.hash,kind,observationId:"policy-check"}];
      expect(reconcileTurn(input).snapshot.classifiedAbsent[0]?.offline.status).toBe(status);
    }
    input.absences[0].scopeHash = "b".repeat(64);
    expect(() => reconcileTurn(input)).toThrow("invalid_report");
  });
  test("private profile/service and fixture assignments cannot cross scopes", () => {
    const input = mapped(); input.scope.profileHash = "b".repeat(64); input.scope.hash = "c".repeat(64);
    expect(reconcileTurn(input).diff.missingCases).toEqual(["read"]);
    input.assignments[0].scopeHash = input.scope.hash;
    input.assignments[0].tool = {...input.assignments[0].tool,originHash:"d".repeat(64)};
    expect(reconcileTurn(input).diff.missingCases).toEqual(["read"]);
  });
  test("scope invalidates on every declared revalidation dimension", () => {
    const {hash:_,...metadata} = base().scope;
    const state = {policyHash:h,capabilitiesHash:h,hooksHash:h,profilePathsHash:h,authContractHash:h,definitionsHash:h};
    const scope = inventoryScope(metadata,state);
    for (const key of Object.keys(state)) expect(inventoryScope(metadata,{...state,[key]:"b".repeat(64)}).hash).not.toBe(scope.hash);
    for (const key of ["hostVersion","sdkVersion","model","protocolProfile","agent"]) expect(inventoryScope({...metadata,[key]:"changed"},state).hash).not.toBe(scope.hash);
    const input = mapped(), first = reconcileTurn(input); input.requestId = "request-2"; input.scope = scope;
    expect(reconcileTurn(input,first).gaps).toContainEqual({code:"scope_changed"});
  });
  for (const [key,value] of Object.entries({hostVersion:"2.2.6",profileHash:"b".repeat(64),hostSha256:"b".repeat(64),sdkVersion:"2.2.6",providerBuildHash:"b".repeat(64),agent:"other-agent",model:"other-model",protocolProfile:"other-protocol",negotiatedMcpVersions:["other-version"]})) test(`reused scope hash rejects changed ${key} before stale assignments or policy credit`, () => {
    const offered = mapped(), absent = base(); absent.offerings = [];
    absent.absences = [{registryId:"read",scopeHash:absent.scope.hash,kind:"policy_disabled",observationId:"policy-check"}];
    for (const input of [offered,absent]) {
      const first = reconcileTurn(input);
      expect(first.inventoryComplete).toBe(true);
        const next = structuredClone(input); next.requestId = "request-2"; next.scope[key] = value;
        expect(next.scope.hash).toBe(first.snapshot.scope.hash);
        expect(() => reconcileTurn(next,first)).toThrow("invalid_report");
    }
  });
  test("ambiguous identities/aliases, forged absence and unknown input fields fail closed", () => {
    const mutations = [(i:any) => i.offerings.push(i.offerings[0]), (i:any) => i.registry.push({...i.registry[0],namespace:"different"}), (i:any) => i.offerings[0].namespace = "different", (i:any) => i.absences.push({registryId:"read",scopeHash:i.scope.hash,kind:"policy_disabled",observationId:"p"}), (i:any) => i.secret = "PRIVATE", (i:any) => i.requestKind = "title"];
    for (const mutate of mutations) { const input = base(); mutate(input); expect(() => reconcileTurn(input)).toThrow("invalid_report"); }
    const input = base(); let invoked = false;
    Object.defineProperty(input.registry[0].definition,"description",{enumerable:true,get:() => {invoked = true; return "PRIVATE";}});
    expect(() => reconcileTurn(input)).toThrow("invalid_report"); expect(invoked).toBe(false);
  });
  test("contract interface accounts all tools without granting positive evidence", () => {
    const inventory = reconcileTurn(base()).snapshot;
    expect(reconcile(inventory,[]).missingCases).toEqual(["read"]);
    expect(reconcile(inventory,[caseFor("read")]).missingCases).toEqual([]);
  });
  test("effective alias, service and newly exposed registered tools invalidate earlier scope", () => {
    const input = mapped(), first = reconcileTurn(input);
    for (const mutate of [(i:any) => i.offerings[0].offeredName = "changed-alias", (i:any) => i.registry[0].provenance.configurationHash = "b".repeat(64)]) {
      const next = structuredClone(input); next.requestId = "request-2"; mutate(next);
      expect(reconcileTurn(next,first).gaps).toContainEqual({code:"scope_changed"});
    }
    const hidden = base(); hidden.offerings = [];
    expect(reconcileTurn({...input,requestId:"request-2"},reconcileTurn(hidden)).gaps).toContainEqual({code:"new_tool",toolId:"read"});
    const missing = base(); missing.registry[0].provenance.kind = "mcp";
    expect(reconcileTurn(missing).gaps).toContainEqual({code:"missing_mcp_discovery",toolId:"read"});
  });
});
