import { Model, Provider } from "@opencode/plugin";
import type { Plugin } from "@opencode/plugin";
import type { StdioBridge } from "./bridge.js";
import type { ProviderFetch } from "./provider.js";

const metadataGuidance = "Some upstream models were omitted: configure options.modelMetadata[upstreamID] with name, capabilities (tools/input/output), positive token limit (context/output), time.released, status and nonnegative cost (input/output/cache.read/cache.write, USD per million tokens).";
function record(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}
const text = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value >= 0;
const positiveInt = (value: unknown): value is number => finite(value) && Number.isSafeInteger(value) && value > 0;
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.length > 0 && value.every(text);
function mapModel(id: string, upstream: Record<string, unknown>, operator: unknown): Model.Info | undefined {
  if (operator !== undefined && !record(operator)) return;
  // Only named metadata fields can cross this boundary: never headers/settings,
  // alternate model/provider IDs, credentials, URLs or arbitrary SDK overlays.
  const source: Record<string, unknown> = {};
  for (const key of ["name", "capabilities", "limit", "time", "cost", "status"]) {
    source[key] = Object.hasOwn(record(operator) ?? {}, key) ? record(operator)![key] : upstream[key];
  }
  const cap = record(source.capabilities), limit = record(source.limit), time = record(source.time);
  if (!text(source.name) || !cap || typeof cap.tools !== "boolean" || !strings(cap.input) || !strings(cap.output) || cap.temperature !== undefined && typeof cap.temperature !== "boolean") return;
  if (!limit || !positiveInt(limit.context) || !positiveInt(limit.output) || limit.output > limit.context || limit.input !== undefined && (!positiveInt(limit.input) || limit.input > limit.context)) return;
  if (!time || !finite(time.released) || typeof source.status !== "string" || !["active", "alpha", "beta", "deprecated"].includes(source.status)) return;
  if (!Array.isArray(source.cost) || source.cost.length === 0) return;
  const costs: Model.Cost[] = [];
  for (const value of source.cost) {
    const cost = record(value), cache = record(cost?.cache), tier = record(cost?.tier);
    if (!cost || !cache || !finite(cost.input) || !finite(cost.output) || !finite(cache.read) || !finite(cache.write)) return;
    if (cost.tier !== undefined && (!tier || tier.type !== "context" || !positiveInt(tier.size))) return;
    costs.push({ input: cost.input as Model.Cost["input"], output: cost.output as Model.Cost["output"], cache: { read: cache.read as Model.Cost["input"], write: cache.write as Model.Cost["input"] }, ...(tier ? { tier: { type: "context" as const, size: tier.size as number } } : {}) });
  }
  return {
    id: Model.ID.make(id), modelID: Model.ID.make(id), providerID: Provider.ID.make("vibe-oidc"), name: source.name,
    capabilities: { tools: cap.tools, input: [...cap.input], output: [...cap.output], ...(typeof cap.temperature === "boolean" ? { temperature: cap.temperature } : {}) },
    limit: { context: limit.context, output: limit.output, ...(typeof limit.input === "number" ? { input: limit.input } : {}) },
    time: { released: time.released }, status: source.status as Model.Info["status"], cost: costs,
    // No fabricated variants; visibility is a plugin policy, entitlement comes
    // exclusively from the authenticated upstream inventory.
    variants: [], enabled: true,
  };
}
export async function discoverModels(fetch: ProviderFetch, baseURL: string, metadata: unknown = {}, signal?: AbortSignal): Promise<{ models: Model.Info[]; diagnostics: string[] }> {
  try {
    const response = await fetch(baseURL + "/models", { method: "GET", ...(signal ? { signal } : {}) });
    if (!response.ok) {
      await response.body?.cancel();
      return { models: [], diagnostics: [response.status === 401 || response.status === 403 ? "Model discovery was not authorized; reconnect Vibe OIDC and verify account model access." : "Model discovery failed upstream; retry explicitly and verify the configured audience."] };
    }
    const payload: unknown = await response.json();
    const data = record(payload)?.data;
    const configured = record(metadata);
    if (!Array.isArray(data) || !configured) return { models: [], diagnostics: ["Invalid model discovery/configuration data; verify the upstream models response and options.modelMetadata object."] };
    const models: Model.Info[] = []; let incomplete = false;
    const seen = new Set<string>();
    for (const item of data) {
      const entry = record(item);
      if (!entry || !text(entry.id) || seen.has(entry.id)) { incomplete = true; continue; }
      seen.add(entry.id);
      const model = mapModel(entry.id, entry, Object.hasOwn(configured, entry.id) ? configured[entry.id] : undefined);
      if (model) models.push(model); else incomplete = true;
    }
    return { models, diagnostics: incomplete ? [metadataGuidance] : [] };
  } catch {
    return { models: [], diagnostics: ["Model discovery unavailable; configure an installed uv-managed mvibe-auth runtime, compatible bridge versions and current Vibe OIDC sign-in, then retry."] };
  }
}

/** Async work stays outside the host's synchronous provider transform. */
type ConnectionInfo = NonNullable<Awaited<ReturnType<Plugin.Context["integration"]["connection"]["active"]>>>;
export function createModelCatalog(input: {
  active: () => Promise<ConnectionInfo | undefined>;
  reload: () => Promise<void>;
  bridge: Pick<StdioBridge, "request">;
  fetch: ProviderFetch;
  baseURL: string;
  metadata: unknown;
  report: (message: string) => void;
}) {
  let snapshot: { models: readonly Model.Info[]; sourceConnection?: ConnectionInfo } = { models: [] };
  let generation = 0, closed = false;
  let controller: AbortController | undefined;
  const key = (connection: ConnectionInfo | undefined) => connection?.type === "credential" ? "credential:" + connection.id : connection ? "env:" + connection.name : undefined;
  return {
    get snapshot() { return snapshot; },
    close() { closed = true; generation++; controller?.abort(); snapshot = { models: [] }; },
    async refresh() {
      if (closed) return;
      const turn = ++generation;
      controller?.abort(); const current = controller = new AbortController();
      try {
        const connection = await input.active();
        if (turn !== generation || closed) return;
        const hadCatalog = snapshot.sourceConnection !== undefined;
        snapshot = { models: [] };
        if (hadCatalog) await input.reload();
        if (!connection || turn !== generation || closed) return;
        const status = await input.bridge.request("status", {}, current.signal);
        if (!status.config_match || !status.signed_in) {
          if (turn === generation && !closed) input.report("Model discovery requires current Vibe OIDC sign-in matching the configured audience; connect using Vibe OIDC.");
          return;
        }
        const result = await discoverModels(input.fetch, input.baseURL, input.metadata, current.signal);
        const active = await input.active();
        if (turn !== generation || closed || key(active) !== key(connection)) return;
        snapshot = { models: result.models, sourceConnection: connection };
        for (const message of result.diagnostics) input.report(message);
        await input.reload();
      } catch {
        if (turn !== generation || closed) return;
        snapshot = { models: [] };
        input.report("Model catalog unavailable; verify the installed uv-managed helper, compatible OpenCode V2/bridge versions and Vibe OIDC connection, then retry.");
      }
    },
  };
}
