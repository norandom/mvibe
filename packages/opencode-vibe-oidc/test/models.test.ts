import { expect, test } from "bun:test";
import { createModelCatalog, discoverModels, metadataFromHostCatalog } from "../src/models.js";
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

function catalogFixture() {
  type Input = Parameters<typeof createModelCatalog>[0];
  let connection: Awaited<ReturnType<Input["active"]>>;
  let signedIn = true, configMatch = true, statuses = 0, reloads = 0, broken = false;
  const messages: string[] = [], requests: Request[] = [];
  let respond: Input["fetch"] = async request => { requests.push(new Request(request)); return Response.json({ data: [{ id: "m", ...metadata }] }); };
  const catalog = createModelCatalog({
    active: async () => connection, reload: async () => { reloads++; }, baseURL: base, metadata: {}, report: message => messages.push(message),
    bridge: { request: async () => { statuses++; if (broken) throw new Error("SECRET helper failure"); return { signed_in: signedIn, config_match: configMatch }; } } as Input["bridge"],
    fetch: (request, init) => respond(request, init),
  });
  return { catalog, messages, requests, connect: (id = "one") => { connection = { type: "credential", id, method: "key", label: "Vibe" }; }, disconnect: () => { connection = undefined; }, signedOut: () => { signedIn = false; }, mismatch: () => { configMatch = false; }, failHelper: () => { broken = true; }, respond: (fn: Input["fetch"]) => { respond = fn; }, statuses: () => statuses, reloads: () => reloads };
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
