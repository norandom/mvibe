import { expect, test } from "bun:test";
import { createModelCatalog, discoverModels, metadataFromHostCatalog } from "../src/models.js";
import type { CatalogTimer, ModelCatalogOptions } from "../src/models.js";
import { createProviderFetch } from "../src/provider.js";
import { ControlledHTTP } from "./preload.js";

const base = "https://inference.example.test/v1";
const metadata = {
  name: "Synthetic", capabilities: { tools: false, input: ["text"], output: ["text"] },
  limit: { context: 8192, output: 1024 }, time: { released: 1234 }, status: "active",
  cost: [{ input: 1, output: 2, cache: { read: 0, write: 0 } }],
};

test("trusted Mistral catalog supplies metadata only, with explicit overrides taking precedence", async () => {
  const host = new Map([["available", { ...metadata, capabilities: { ...metadata.capabilities, headers: { authorization: "NESTED_PRIVATE" } }, id: "available", modelID: "available", providerID: "mistral", headers: { authorization: "PRIVATE" }, settings: { baseURL: "https://untrusted.invalid" } }]]);
  const combined = metadataFromHostCatalog(host, { available: { name: "Operator name" } });
  const result = await discoverModels(fixture({ data: [{ id: "available" }, { id: "unknown" }] }).fetch, base, combined);
  expect(result.models.map(model => String(model.id))).toEqual(["available"]);
  expect(result.models[0]).toMatchObject({ name: "Operator name", limit: metadata.limit });
  expect(JSON.stringify(combined)).not.toContain("PRIVATE");
  expect(JSON.stringify(combined)).not.toContain("untrusted.invalid");
});

test("catalog metadata rejects other providers and malformed operator configuration", async () => {
  const host = new Map([["available", { ...metadata, id: "available", modelID: "available", providerID: "other" }]]);
  expect(metadataFromHostCatalog(host, {})).toEqual({});
  expect(metadataFromHostCatalog(new Map(), "invalid")).toBe("invalid");
});
function fixture(body: unknown, status = 200) {
  const http = new ControlledHTTP(); let resolves = 0;
  http.queue("GET", base + "/models", Response.json(body, { status }));
  const fetch = createProviderFetch({ request: async (_method, params) => {
    resolves++; return { target: (params as { target_url: string }).target_url, headers: { Authorization: "Bearer synthetic" }, expires_at: Date.now() / 1000 + 60, revision: String(resolves) };
  } } as Parameters<typeof createProviderFetch>[0], "https://inference.example.test", http.fetch);
  return { fetch, http, resolves: () => resolves };
}
test("authenticated models use current snapshot and explicit metadata only for upstream IDs", async () => {
  const f = fixture({ data: [{ id: "available" }] });
  const result = await discoverModels(f.fetch, base, { available: metadata, invented: metadata });
  expect(result.models.map(model => String(model.id))).toEqual(["available"]);
  expect(result.models[0]).toMatchObject({ capabilities: { tools: false }, limit: metadata.limit, cost: metadata.cost, providerID: "vibe-oidc", modelID: "available" });
  expect(f.resolves()).toBe(1); expect(f.http.requests[0]!.headers.get("authorization")).toBe("Bearer synthetic");
  expect(f.http.requests[0]!.redirect).toBe("manual");
});
test("curated thinking variants attach to known models only and never from operator metadata", async () => {
  const f = fixture({ data: [{ id: "mistral-large-4" }, { id: "mistral-medium-latest" }, { id: "mistral-large-latest" }, { id: "available" }] });
  const result = await discoverModels(f.fetch, base, { "mistral-large-4": metadata, "mistral-medium-latest": metadata, "mistral-large-latest": metadata, available: metadata });
  const byId = new Map(result.models.map(model => [String(model.id), model]));
  expect(byId.get("mistral-large-4")!.variants.map(variant => String(variant.id))).toEqual(["low", "high", "max"]);
  expect(byId.get("mistral-large-4")!.variants.map(variant => variant.settings)).toEqual([{ reasoningEffort: "low" }, { reasoningEffort: "high" }, { reasoningEffort: "max" }]);
  expect(byId.get("mistral-medium-latest")!.variants.map(variant => String(variant.id))).toEqual(["high", "off"]);
  expect(byId.get("mistral-medium-latest")!.variants[1]!.settings).toEqual({ reasoningEffort: "none" });
  expect(byId.get("mistral-large-latest")!.variants).toEqual([]);
  expect(byId.get("available")!.variants).toEqual([]);
  const injected = await discoverModels(fixture({ data: [{ id: "mistral-large-4" }] }).fetch, base, { "mistral-large-4": { ...metadata, variants: [{ id: "invented", settings: { reasoningEffort: "max" } }] } });
  expect(injected.models[0]!.variants.map(variant => String(variant.id))).toEqual(["low", "high", "max"]);
});
test("verified upstream metadata maps without SDK defaults; partial inventory survives omissions", async () => {
  const f = fixture({ data: [{ id: "complete", ...metadata }, { id: "incomplete" }] });
  const result = await discoverModels(f.fetch, base);
  expect(result.models.map(model => String(model.id))).toEqual(["complete"]);
  expect(result.diagnostics.join(" ")).toContain("modelMetadata");
});
test("empty inventory remains empty", async () => {
  expect((await discoverModels(fixture({ data: [] }).fetch, base)).models).toEqual([]);
});
test("malformed upstream JSON is sanitized and never echoed", async () => {
  const result = await discoverModels(async () => new Response("SECRET malformed JSON"), base);
  expect(result.models).toEqual([]); expect(result.diagnostics.join()).toContain("uv-managed"); expect(result.diagnostics.join()).not.toContain("SECRET");
});
test.each([401, 403, 302, 500])("upstream %s produces actionable sanitized diagnostics without parsing body", async status => {
  const result = await discoverModels(fixture({ secret: "SECRET upstream" }, status).fetch, base);
  expect(result.models).toEqual([]); expect(result.diagnostics.length).toBeGreaterThan(0);
  expect(result.diagnostics.join()).not.toContain("SECRET");
});
test.each([null, {}, { data: {} }, { data: [{ id: "m", ...metadata, limit: { context: 0, output: 4 } }] }, { data: [{ id: "m", ...metadata, cost: [] }] }])("malformed or insufficient metadata is safely omitted %j", async body => {
  const result = await discoverModels(fixture(body).fetch, base);
  expect(result.models).toEqual([]); expect(result.diagnostics.length).toBeGreaterThan(0);
});
test.each([{ limit: { context: Infinity, output: 20 } }, { capabilities: { tools: "yes", input: ["text"], output: ["text"] } }, { cost: [{ input: -1, output: 2, cache: { read: 0, write: 0 } }] }])("invalid explicit override cannot replace valid upstream values %j", async override => {
  const result = await discoverModels(fixture({ data: [{ id: "m", ...metadata }] }).fetch, base, { m: override });
  expect(result.models).toEqual([]); expect(result.diagnostics.length).toBeGreaterThan(0);
});
test("operator fields cannot inject destinations, IDs or credentials", async () => {
  const result = await discoverModels(fixture({ data: [{ id: "m" }] }).fetch, base, { m: { ...metadata, id: "invented", headers: { authorization: "SECRET" }, settings: { apiKey: "SECRET" } } });
  expect(result.models.map(model => String(model.id))).toEqual(["m"]);
  expect(JSON.stringify(result)).not.toContain("SECRET"); expect(result.models[0]).not.toHaveProperty("headers");
});

function catalogFixture(options: ModelCatalogOptions = {}) {
  type Input = Parameters<typeof createModelCatalog>[0];
  let connection: Awaited<ReturnType<Input["active"]>>;
  let signedIn = true, configMatch = true, statuses = 0, reloads = 0, broken = false;
  const messages: string[] = [], requests: Request[] = [], statusSignals: AbortSignal[] = [];
  let respond: Input["fetch"] = async request => { requests.push(new Request(request)); return Response.json({ data: [{ id: "m", ...metadata }] }); };
  const catalog = createModelCatalog({
    active: async () => connection, reload: async () => { reloads++; }, baseURL: base, metadata: {}, report: message => messages.push(message),
    bridge: { request: async (_method: "status", _params: Record<string, never>, signal?: AbortSignal) => { statuses++; if (signal) statusSignals.push(signal); if (broken) throw new Error("SECRET helper failure"); return { signed_in: signedIn, config_match: configMatch }; } } as Input["bridge"],
    fetch: (request, init) => respond(request, init),
    ...options,
  });
  return { catalog, messages, requests, connect: (id = "one") => { connection = { type: "credential", id, method: "key", label: "Vibe" }; }, disconnect: () => { connection = undefined; }, signedOut: () => { signedIn = false; }, mismatch: () => { configMatch = false; }, failHelper: (value = true) => { broken = value; }, respond: (fn: Input["fetch"]) => { respond = fn; }, statusSignals: () => statusSignals, statuses: () => statuses, reloads: () => reloads };
}

/** Manual deadline clock mirroring the catalog timer seam; no real timer ever fires. */
type FakeClock = CatalogTimer & { fire: () => boolean; pending: () => number; clearedCount: () => number; delays: number[] };
function fakeClock(): FakeClock {
  const armed: Array<{ fire: () => void; handle: ReturnType<typeof setTimeout> }> = [];
  const delays: number[] = [];
  let cleared = 0, seq = 0;
  return {
    timer: (callback, ms) => { delays.push(ms); const handle = seq++ as unknown as ReturnType<typeof setTimeout>; armed.push({ fire: callback, handle }); return handle; },
    clearTimer: handle => { const index = armed.findIndex(entry => entry.handle === handle); if (index >= 0) armed.splice(index, 1); cleared++; },
    fire: () => { const entry = armed.shift(); if (entry === undefined) return false; entry.fire(); return true; },
    pending: () => armed.length,
    clearedCount: () => cleared,
    delays,
  };
}

test("late metadata remaps only current authenticated inventory without refetch and clears on disconnect", async () => {
  type Input = Parameters<typeof createModelCatalog>[0];
  let connection: Awaited<ReturnType<Input["active"]>> = { type: "credential", id: "one", method: "key", label: "Vibe" };
  let configured: unknown = {};
  let requests = 0;
  const catalog = createModelCatalog({
    active: async () => connection, reload: async () => {}, baseURL: base,
    metadata: () => configured, report: () => {},
    bridge: { request: async () => ({ signed_in: true, config_match: true }) } as Input["bridge"],
    fetch: async () => { requests++; return Response.json({ data: [{ id: "available", headers: { authorization: "PRIVATE" } }] }); },
  });
  await catalog.refresh();
  expect(catalog.snapshot.models).toEqual([]);
  configured = { available: metadata, invented: metadata };
  expect(catalog.snapshot.models.map(model => String(model.id))).toEqual(["available"]);
  expect(catalog.snapshot.sourceConnection).toMatchObject({ id: "one" });
  expect(JSON.stringify(catalog.snapshot)).not.toContain("PRIVATE");
  configured = { available: { ...metadata, limit: { context: 0, output: 4 } } };
  expect(catalog.snapshot.models).toEqual([]);
  configured = { available: metadata };
  expect(catalog.snapshot.models).toHaveLength(1);
  expect(requests).toBe(1);
  connection = undefined;
  await catalog.refresh();
  expect(catalog.snapshot).toEqual({ models: [] });
  configured = { available: metadata, invented: metadata };
  expect(catalog.snapshot).toEqual({ models: [] });
  expect(requests).toBe(1);
  catalog.close();
});
test("disconnected registration never starts helper or fetch; connection enables bound catalog", async () => {
  const f = catalogFixture(); await f.catalog.refresh();
  expect(f.statuses()).toBe(0); expect(f.requests).toEqual([]); expect(f.catalog.snapshot.models).toEqual([]);
  f.connect(); await f.catalog.refresh();
  expect(f.catalog.snapshot.sourceConnection).toMatchObject({ id: "one" }); expect(f.catalog.snapshot.models).toHaveLength(1);
  expect(f.reloads()).toBe(1);
  f.disconnect(); await f.catalog.refresh(); expect(f.catalog.snapshot).toEqual({ models: [] });
});

test("initial discovery awaits a superseding refresh until the latest generation settles", async () => {
  const f = catalogFixture(); f.connect();
  const releases: Array<() => void> = [];
  f.respond(async () => { await new Promise<void>(resolve => releases.push(resolve)); return Response.json({ data: [{ id: "m", ...metadata }] }); });
  let settled = false;
  const initial = f.catalog.refresh().then(() => { settled = true; });
  while (releases.length < 1) await Promise.resolve();
  const replacement = f.catalog.refresh();
  while (releases.length < 2) await Promise.resolve();
  releases[0]!();
  for (let i = 0; i < 30; i++) await Promise.resolve();
  try { expect(settled).toBe(false); }
  finally { releases[1]!(); await Promise.all([initial, replacement]); }
  expect(f.catalog.snapshot.models).toHaveLength(1);
});

test("same-connection refresh preserves inventory until atomic replacement", async () => {
  const f = catalogFixture(); f.connect(); await f.catalog.refresh();
  let release!: () => void;
  f.respond(async () => { await new Promise<void>(resolve => { release = resolve; }); return Response.json({ data: [{ id: "replacement", ...metadata }] }); });
  const refresh = f.catalog.refresh();
  while (!release) await Promise.resolve();
  try {
    expect(f.catalog.snapshot.models.map(model => String(model.id))).toEqual(["m"]);
    expect(f.reloads()).toBe(1);
  } finally { release(); await refresh; }
  expect(f.catalog.snapshot.models.map(model => String(model.id))).toEqual(["replacement"]);
  expect(f.reloads()).toBe(2);
});

test.each(["signedOut", "mismatch", "failHelper", "fetchFailure", "switch"] as const)("existing inventory is invalidated by %s", async cause => {
  const f = catalogFixture(); f.connect(); await f.catalog.refresh();
  if (cause === "fetchFailure") f.respond(async () => new Response(null, { status: 401 }));
  else if (cause === "switch") {
    f.connect("two");
    f.respond(async () => new Response(null, { status: 401 }));
  } else f[cause]();
  await f.catalog.refresh();
  expect(f.catalog.snapshot.models).toEqual([]);
  expect(f.reloads()).toBeGreaterThan(1);
});
test.each(["signedOut", "mismatch"] as const)("%s helper status never invokes model fetch or login", async method => {
  const f = catalogFixture(); f.connect(); f[method](); await f.catalog.refresh();
  expect(f.requests).toEqual([]); expect(f.messages.join()).toContain("connect using Vibe OIDC");
});
test("helper setup failure is actionable but does not prevent provider registration", async () => {
  const f = catalogFixture(); f.connect(); f.failHelper(); await f.catalog.refresh();
  expect(f.catalog.snapshot.models).toEqual([]); expect(f.messages.join()).toContain("uv-managed"); expect(f.messages.join()).not.toContain("SECRET");
  expect(f.requests).toEqual([]); expect(f.statuses()).toBe(1);
});
test("connection changes while discovery waits cannot publish another account's inventory", async () => {
  const f = catalogFixture(); f.connect(); let release!: () => void;
  f.respond(async () => { await new Promise<void>(resolve => { release = resolve; }); return Response.json({ data: [{ id: "m", ...metadata }] }); });
  const refresh = f.catalog.refresh(); while (!release) await Promise.resolve();
  f.connect("two"); release(); await refresh;
  expect(f.catalog.snapshot).toEqual({ models: [] });
});
test("cleanup aborts in-flight discovery and prevents late catalog publication", async () => {
  const f = catalogFixture(); f.connect(); let release!: () => void; let signal: AbortSignal | null | undefined;
  f.respond(async (_request, init) => { signal = init?.signal; await new Promise<void>(resolve => { release = resolve; }); return Response.json({ data: [{ id: "m", ...metadata }] }); });
  const refresh = f.catalog.refresh(); while (!release) await Promise.resolve();
  f.catalog.close(); expect(signal?.aborted).toBe(true); release(); await refresh;
  expect(f.catalog.snapshot).toEqual({ models: [] }); await f.catalog.refresh(); expect(f.statuses()).toBe(1);
});

test("deadline expiry abandons the attempt, keeps the last-good list and never aborts the session", async () => {
  const clock = fakeClock();
  const f = catalogFixture({ deadlineMs: 1_000, timer: clock });
  f.connect();
  await f.catalog.refresh();
  expect(clock.pending()).toBe(0);
  expect(clock.clearedCount()).toBe(1);
  let release!: () => void; let fetchSignal: AbortSignal | null | undefined;
  f.respond(async (_request, init) => { fetchSignal = init?.signal; await new Promise<void>(resolve => { release = resolve; }); return Response.json({ data: [{ id: "late", ...metadata }] }); });
  const refresh = f.catalog.refresh();
  while (!release) await Promise.resolve();
  expect(clock.pending()).toBe(1);
  expect(clock.fire()).toBe(true);
  expect(f.messages).toContain("Model discovery timed out after 1s; kept the last known model list.");
  expect(f.catalog.snapshot.models.map(model => String(model.id))).toEqual(["m"]);
  expect(fetchSignal?.aborted).toBe(false);
  expect(f.statusSignals().at(-1)?.aborted).toBe(false);
  expect(clock.pending()).toBe(0);
  release();
  await refresh;
  expect(f.catalog.snapshot.models.map(model => String(model.id))).toEqual(["m"]);
  expect(f.reloads()).toBe(1);
});

test("follow-up refresh after a timed-out attempt starts a new attempt", async () => {
  const clock = fakeClock();
  const f = catalogFixture({ deadlineMs: 1_000, timer: clock });
  f.connect();
  let release!: () => void;
  f.respond(async () => { await new Promise<void>(resolve => { release = resolve; }); return Response.json({ data: [{ id: "stale", ...metadata }] }); });
  const stalled = f.catalog.refresh();
  while (!release) await Promise.resolve();
  clock.fire();
  expect(f.messages.join()).toContain("timed out");
  release();
  await stalled;
  expect(f.catalog.snapshot.models).toEqual([]);
  f.respond(async () => Response.json({ data: [{ id: "fresh", ...metadata }] }));
  await f.catalog.refresh();
  expect(f.statuses()).toBe(2);
  expect(clock.delays).toEqual([1_000, 1_000]);
  expect(f.catalog.snapshot.models.map(model => String(model.id))).toEqual(["fresh"]);
  expect(f.reloads()).toBe(1);
});

test("follow-up refresh after a failed helper attempt starts a new attempt", async () => {
  const f = catalogFixture();
  f.connect();
  f.failHelper();
  await f.catalog.refresh();
  expect(f.messages.join()).toContain("uv-managed");
  expect(f.catalog.snapshot.models).toEqual([]);
  f.failHelper(false);
  await f.catalog.refresh();
  expect(f.statuses()).toBe(2);
  expect(f.catalog.snapshot.models.map(model => String(model.id))).toEqual(["m"]);
  expect(f.reloads()).toBe(1);
});

test("superseding refresh clears the previous attempt's deadline timer", async () => {
  const clock = fakeClock();
  const f = catalogFixture({ deadlineMs: 1_000, timer: clock });
  f.connect();
  const releases: Array<() => void> = [];
  f.respond(async () => { await new Promise<void>(resolve => releases.push(resolve)); return Response.json({ data: [{ id: "m", ...metadata }] }); });
  const initial = f.catalog.refresh();
  while (releases.length < 1) await Promise.resolve();
  const replacement = f.catalog.refresh();
  while (releases.length < 2) await Promise.resolve();
  expect(clock.pending()).toBe(1);
  expect(clock.clearedCount()).toBe(1);
  clock.fire();
  expect(f.messages.join()).toContain("timed out");
  releases[0]!();
  releases[1]!();
  await Promise.all([initial, replacement]);
  expect(f.catalog.snapshot.models).toEqual([]);
});

test("close during a pending attempt clears the deadline timer and reports no timeout", async () => {
  const clock = fakeClock();
  const f = catalogFixture({ deadlineMs: 1_000, timer: clock });
  f.connect();
  let release!: () => void;
  f.respond(async () => { await new Promise<void>(resolve => { release = resolve; }); return Response.json({ data: [{ id: "m", ...metadata }] }); });
  const refresh = f.catalog.refresh();
  while (!release) await Promise.resolve();
  f.catalog.close();
  expect(clock.pending()).toBe(0);
  expect(clock.clearedCount()).toBe(1);
  expect(clock.fire()).toBe(false);
  release();
  await refresh;
  expect(f.messages.join()).not.toContain("timed out");
  expect(f.catalog.snapshot).toEqual({ models: [] });
});

test("attempts arm the default 30s deadline and clear it on settle", async () => {
  const clock = fakeClock();
  const f = catalogFixture({ timer: clock });
  f.connect();
  await f.catalog.refresh();
  expect(clock.delays).toEqual([30_000]);
  expect(clock.pending()).toBe(0);
  expect(clock.clearedCount()).toBe(1);
});

test("deadline values at or below zero disable the per-attempt timer", async () => {
  const clock = fakeClock();
  const f = catalogFixture({ deadlineMs: 0, timer: clock });
  f.connect();
  await f.catalog.refresh();
  expect(clock.delays).toEqual([]);
  expect(clock.pending()).toBe(0);
  expect(f.catalog.snapshot.models).toHaveLength(1);
});
