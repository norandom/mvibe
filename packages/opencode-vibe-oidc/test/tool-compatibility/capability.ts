/** Probe observations are capability evidence, never a compatibility PASS. */
export type CapabilityEvent = { kind: "context" | "http_request" | "http_response" | "transport_request" | "transport_chunk" | "transport_end" | "input_started" | "input_ended" | "state_unchanged" | "dispatch" | "permission_allow" | "permission_deny" | "completed" | "error" | "continuation" | "final"; case: "allowed" | "denied"; link: string };
/** Exact pinned native formatter from reference executable's ReadTool (fR).
 * Expected content is built from trusted fixture input before execution. */
export function expectedNativeRead(path: string, nonce: string): string {
  if (!/^[a-f0-9-]{36}$/.test(nonce) || path.length > 512 || /[\r\n]/.test(path)) throw new Error("HOST_FIXTURE");
  return `Read file ${path}, lines 1-1\n1: ${nonce}`;
}
export function capabilityDecision(events: readonly CapabilityEvent[]): "observed" | "hook_unavailable" {
  if (events.length > 256) return "hook_unavailable";
  for (const c of ["allowed", "denied"] as const) {
    const chain = events.filter(e => e.case === c);
    const kinds = chain.map(e => e.kind);
    const required = ["context", "transport_request", "transport_chunk", "transport_end", "input_started", "input_ended", "state_unchanged", "dispatch", c === "allowed" ? "permission_allow" : "permission_deny", c === "allowed" ? "completed" : "error", "continuation", "final"] as const;
    if (required.some(k => !kinds.includes(k)) || new Set(chain.map(e => e.link)).size !== 1 || !chain[0]?.link || (c === "denied" && kinds.includes("completed"))) return "hook_unavailable";
    if (kinds.indexOf("dispatch") >= kinds.indexOf(c === "allowed" ? "completed" : "error") || kinds.indexOf("continuation") <= kinds.indexOf(c === "allowed" ? "completed" : "error")) return "hook_unavailable";
    if (kinds.indexOf("input_started") >= kinds.indexOf("transport_end") || kinds.indexOf("dispatch") >= kinds.indexOf(c === "allowed" ? "permission_allow" : "permission_deny") || kinds.indexOf("continuation") >= kinds.indexOf("final") || kinds.filter(k=>k==="transport_request").length!==2 || kinds.filter(k=>k==="transport_end").length!==2) return "hook_unavailable";
  }
  return "observed";
}

/** Deliberately separate from full RoundTripProof/report acceptance (task 3.4). */
export function parseCapabilityReport(value: unknown): Record<string, unknown> {
  const fail = (): never => {throw new Error("HOST_CAPABILITY_REPORT");};
  const object = (v:unknown,keys:string[],optional:string[]=[]) => {
    if(!v || typeof v!=="object" || Array.isArray(v) || keys.some(k=>!Object.hasOwn(v,k)) || Object.keys(v).some(k=>!keys.includes(k)&&!optional.includes(k)))fail();
    return v as Record<string,unknown>;
  };
  const array = (v:unknown,max:number):unknown[] => {if(!Array.isArray(v)||v.length>max)return fail();return v;};
  const name = (v:unknown) => {if(typeof v!=="string"||!/^[a-zA-Z0-9_.:/-]{1,160}$/.test(v))fail();};
  const hash = (v:unknown) => {if(typeof v!=="string"||! /^[a-f0-9]{64}$/.test(v))fail();};
  const kind = (v:unknown) => {if(v!=="allowed"&&v!=="denied")fail();};
  try {
    const r=object(value,["ready","status","code","stage","inventory","offered","permissions","counts","events","failureCode","links","transport","denialSource","auxiliary","live","allToolCompatibility","profileHash","providerBuildHash"]);
    if(JSON.stringify(r).length>65536 || typeof r.ready!=="boolean" || r.status!==(r.ready?"observed":"unsupported") || r.code!==(r.ready?null:"hook_unavailable") || !["setup","connect","model","inventory","allowed","denied","decision"].includes(String(r.stage)) || typeof r.failureCode!=="string" || !/^(none|HOST_[A-Z_]+)$/.test(r.failureCode))fail();
    for(const key of ["profileHash","providerBuildHash"])hash(r[key]);
    if(r.transport!=="fixture-owned-before-provider" || r.denialSource!=="execute.after.error.error.Permission.BlockedError" || [r.auxiliary,r.live,r.allToolCompatibility].some(v=>v!=="unverified"))fail();
    const registry=array(r.inventory,256);
    registry.forEach(v=>{const e=object(v,["id","name","schemaHash"],["permission","namespace"]);name(e.id);name(e.name);hash(e.schemaHash);for(const k of ["permission","namespace"])if(e[k]!==undefined)name(e[k]);});
    const offerings=array(r.offered,8);
    offerings.forEach(v=>{const e=object(v,["case","agent","definitions"]);kind(e.case);name(e.agent);array(e.definitions,256).forEach(v=>{const d=object(v,["name","schemaHash"]);name(d.name);hash(d.schemaHash);});});
    array(r.permissions,16).forEach(v=>{const e=object(v,["case","action","effect","owned"]);kind(e.case);if(e.action!=="read"||!["allow","deny","ask"].includes(String(e.effect))||e.owned!==true)fail();});
    const counts=object(r.counts,["sdk","language","httpRequest","httpResponse","requests","chunks","ends","auxiliary"]);
    Object.values(counts).forEach(v=>{if(!Number.isSafeInteger(v)||Number(v)<0||Number(v)>1024)fail();});
    const events=array(r.events,256);
    const kinds=["context","http_request","http_response","transport_request","transport_chunk","transport_end","input_started","input_ended","state_unchanged","dispatch","permission_allow","permission_deny","completed","error","continuation","final"];
    events.forEach(v=>{const e=object(v,["case","kind","link"]);kind(e.case);if(!kinds.includes(String(e.kind))||e.link!=="owned-"+e.case)fail();});
    const links=array(r.links,2);
    links.forEach(v=>{const e=object(v,["case","sessionHash","upstreamCallHash","expectedResultHash"],["hostCallHash","actualResultHash"]);kind(e.case);for(const[k,v]of Object.entries(e))if(k!=="case")hash(v);});
    if(r.ready){
      if(r.failureCode!=="none" || r.stage!=="decision" || capabilityDecision(events as CapabilityEvent[])!=="observed" || counts.requests!==4 || counts.ends!==4 || Number(counts.sdk)<1 || Number(counts.language)<1 || counts.auxiliary!==0 || links.length!==2 || new Set(links.map(v=>(v as Record<string,unknown>).case)).size!==2)fail();
      links.forEach(v=>{const e=v as Record<string,unknown>;if(e.upstreamCallHash!==e.hostCallHash||e.expectedResultHash!==e.actualResultHash)fail();});
      if(offerings.length!==4 || registry.filter(v=>(v as Record<string,unknown>).id==="read").length!==1)fail();
    }
    return r;
  }catch{return fail();}
}
