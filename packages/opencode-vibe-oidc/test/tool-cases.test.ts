import { describe, expect, test } from "bun:test";
import source from "./tool-compatibility/manifests/default.json" with { type: "json" };
import { DEFAULT_CASE_MANIFEST, caseApplicability, fixtureHash, materializeCase, parseCaseManifest, validateCaseSpec } from "./tool-compatibility/cases.js";

const sample = (): any => structuredClone(source);
const material = (template: ReturnType<typeof DEFAULT_CASE_MANIFEST>["cases"][number]) => ({
  recipeVersion:1 as const, objective:template.objective, observation:{state:"owned-state"},
  calls:template.calls.map((toolId, i) => ({toolId, nonce:`${"n".repeat(32)}${i}`, input:{path:`owned/file-${i}`, nonce:`${"n".repeat(32)}${i}`}, observation:{bytes:`fixture-${i}`}})),
});

describe("authoritative owned case recipes (no execution credit)", () => {
  test("default manifest is immutable, bounded and covers observed IDs and later fixture families", () => {
    const manifest = DEFAULT_CASE_MANIFEST();
    expect(Object.isFrozen(manifest.cases[0]!.spec.prerequisites)).toBe(true);
    const observed = ['read','write','edit','patch','glob','grep','shell','webfetch','websearch','question','skill','subagent','opencode_models','opencode_session_move','opencode_session_rename','opencode_worktree_create','opencode_worktree_list','opencode_worktree_remove','opencode_list_mcp_resources','opencode_read_mcp_resource','experimental_image_generate','claude_mem_get_observations','claude_mem_search','claude_mem_timeline'];
    for (const id of observed) expect(manifest.cases.some(c => c.spec.toolIds.includes(id))).toBe(true);
    expect(new Set(manifest.cases.map(c => c.spec.fixtureKind)).size).toBe(9);
    for (const outcome of ['success','denied','error','cancelled','unsupported']) expect(manifest.cases.some(c => c.spec.expectedOutcome === outcome)).toBe(true);
    for (const template of manifest.cases) {
      const spec = materializeCase(template, material(template), manifest.limits);
      expect(spec.positiveRequired).toBe(spec.expectedOutcome === 'success');
      expect(Object.isFrozen(spec.expectedCalls)).toBe(true);
    }
  });
  test("hashes bind actual per-run fixture inputs, observations, objective and nonce", () => {
    const manifest = DEFAULT_CASE_MANIFEST(), template = manifest.cases[0]!;
    const m = material(template), first = materializeCase(template,m,manifest.limits);
    expect(first.expectedCalls[0]!.inputHash).toBe(fixtureHash(m.calls[0]!.input));
    expect(first.expectedCalls[0]!.fixtureNonceHash).toBe(fixtureHash(m.calls[0]!.nonce));
    expect(first.expectedObservationHash).toBe(fixtureHash(m.observation));
    const changed = structuredClone(m); changed.calls[0]!.input.path = 'owned/other';
    expect(materializeCase(template,changed,manifest.limits).expectedCalls[0]!.inputHash).not.toBe(first.expectedCalls[0]!.inputHash);
    expect(fixtureHash({b:2,a:1})).toBe(fixtureHash({a:1,b:2}));
    expect(() => materializeCase(template,{...m,objective:'other'},manifest.limits)).toThrow('invalid_report');
    expect(() => materializeCase(template,{...m,calls:[]},manifest.limits)).toThrow('invalid_report');
  });
  test("missing prerequisites never imply disabled status or successful execution", () => {
    const template = DEFAULT_CASE_MANIFEST().cases[0]!;
    expect(caseApplicability(template,{})).toEqual({applicable:false,missing:template.spec.prerequisites,outcome:{status:'UNVERIFIED',reason:'missing_capability'}});
    const states = Object.fromEntries(template.spec.prerequisites.map(p=>[p,'available' as const]));
    expect(caseApplicability(template,states).outcome).toEqual({status:'UNVERIFIED',reason:'not_exercised'});
    for (const [state,status] of [['disabled','DISABLED'],['unsupported','UNSUPPORTED'],['unverified','UNVERIFIED']] as const) {
      expect(caseApplicability(template,{...states,[template.spec.prerequisites[0]!]:state}).outcome.status).toBe(status);
    }
  });
  const mutations: Record<string,(m:any)=>void> = {
    'unknown manifest field':m=>{m.private='PRIVATE';},
    'unsupported schema':m=>{m.schemaVersion=2;},
    'unsupported recipe':m=>{m.cases[0].recipeVersion=2;},
    'duplicate case':m=>{m.cases.push(structuredClone(m.cases[0]));},
    'empty tool set':m=>{m.cases[0].spec.toolIds=[];},
    'unmapped expected call':m=>{m.cases[0].calls=['other'];},
    'absent expected call':m=>{m.cases[0].calls=[];},
    'duplicate prerequisite':m=>{m.cases[0].spec.prerequisites.push(m.cases[0].spec.prerequisites[0]);},
    'negative claimed positive':m=>{m.cases[0].spec.expectedOutcome='error';},
    'success without positive requirement':m=>{m.cases[0].spec.positiveRequired=false;},
    'success without continuation':m=>{m.cases[0].spec.requiredObservations=['definition'];},
    'success without executor':m=>{m.cases[0].spec.executorExpectation='must_not_start';},
    'negative timeout':m=>{m.cases[0].spec.timeoutMs=-1;},
    'over run budget':m=>{m.cases[0].spec.timeoutMs=m.limits.runMs+1;},
    'zero startup budget':m=>{m.limits.startupMs=0;},
    'call budget':m=>{m.limits.toolCallsPerCase=1;},
    'event budget':m=>{m.limits.eventsPerCase=2;},
    'trace limit':m=>{m.limits.traceBytes=999999999;},
    'startup exceeds run':m=>{m.limits.startupMs=m.limits.runMs+1;},
    'aggregate execution exceeds run':m=>{m.limits.runMs=20000;},
    'success with negative observation':m=>{m.cases[0].spec.requiredObservations.push('denied');},
    'insufficient parallel events':m=>{m.limits.eventsPerCase=8;},
    'cancellation on success':m=>{m.cases[0].spec.cancellation={host:true,executor:true,provider:true,mcp:false,completionRace:'fail_case'};},
    'cancel without provider abort':m=>{m.cases.find((c:any)=>c.spec.expectedOutcome==='cancelled').spec.cancellation.provider=false;},
    'cancel without state':m=>{const c=m.cases.find((c:any)=>c.spec.expectedOutcome==='cancelled');c.spec.requiredObservations=['definition','model_call','executor_started'];},
    'cancellation budget exceeds case':m=>{m.cases.find((c:any)=>c.spec.expectedOutcome==='cancelled').spec.timeoutMs=1000;},
    'denial with executor':m=>{m.cases.find((c:any)=>c.spec.expectedOutcome==='denied').spec.executorExpectation='must_complete';},
    'invalid request without error':m=>{m.cases.find((c:any)=>c.spec.expectedOutcome==='error').spec.requiredObservations=['definition'];},
    'unsupported with executor':m=>{m.cases.find((c:any)=>c.spec.expectedOutcome==='unsupported').spec.executorExpectation='observe_if_started';},
  };
  for (const [name,mutate] of Object.entries(mutations)) test(`rejects ${name} without rejected contents`, () => {
    const m=sample();mutate(m);expect(()=>parseCaseManifest(m)).toThrow(/^invalid_report$/);
  });
  test("untrusted/non-JSON material and duplicate call nonces are rejected", () => {
    const manifest=DEFAULT_CASE_MANIFEST(), template=manifest.cases.find(c=>c.calls.length===2)!;
    const m=material(template);m.calls[1]!.nonce=m.calls[0]!.nonce;
    expect(()=>materializeCase(template,m,manifest.limits)).toThrow('invalid_report');
    for (const value of [NaN,undefined,()=>1,{get secret(){throw new Error('PRIVATE');}},new Date(),new Proxy({}, {ownKeys(){throw new Error('PRIVATE');}})]) expect(()=>fixtureHash(value)).toThrow(/^invalid_report$/);
    const c=materializeCase(manifest.cases[0]!,material(manifest.cases[0]!),manifest.limits);
    expect(()=>validateCaseSpec({...c,headers:'PRIVATE'},manifest.limits)).toThrow(/^invalid_report$/);
    expect(()=>validateCaseSpec(c,{...manifest.limits,runMs:NaN})).toThrow(/^invalid_report$/);
    expect(()=>fixtureHash(Object.defineProperty({},'hidden',{value:'PRIVATE'}))).toThrow(/^invalid_report$/);
  });
});
