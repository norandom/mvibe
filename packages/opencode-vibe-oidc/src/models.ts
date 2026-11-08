import { Model, Provider } from "@opencode/plugin";
import type { Plugin } from "@opencode/plugin";
import type { StdioBridge } from "./bridge.js";
import type { ProviderFetch } from "./provider.js";
import { variantsFor } from "./thinking.js";

const metadataGuidance = "Some upstream models were omitted: configure options.modelMetadata[upstreamID] with name, capabilities (tools/input/output), positive token limit (context/output), time.released, status and nonnegative cost (input/output/cache.read/cache.write, USD per million tokens).";
function record(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}
const text = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value >= 0;
const positiveInt = (value: unknown): value is number => finite(value) && Number.isSafeInteger(value) && value > 0;
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.length > 0 && value.every(text);

/** Reuse only metadata from the host's canonical Mistral definitions. */
export function metadataFromHostCatalog(models: ReadonlyMap<string, unknown>, configured: unknown): unknown {
  const operator = record(configured);
  if (!operator) return configured;
  const result = new Map<string, unknown>();
  for (const [id, value] of models) {
    const model = record(value);
    if (!model || model.providerID !== "mistral" || model.id !== id || model.modelID !== id) continue;
    const validated = record(mapModel(id, model, undefined));
    if (!validated) continue;
    const metadata: Record<string, unknown> = {};
    for (const field of ["name", "capabilities", "limit", "time", "cost", "status"]) metadata[field] = validated[field];
    result.set(id, metadata);
  }
  for (const [id, value] of Object.entries(operator)) {
    result.set(id, record(value) ? { ...record(result.get(id)), ...record(value) } : value);
  }
  return Object.fromEntries(result);
}
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
    // Variants come only from the curated thinking-level table in thinking.ts
    // (mirroring the Vibe reference's known-model rows); nothing is fabricated
    // and model visibility remains a plugin policy — entitlement comes
    // exclusively from the authenticated upstream inventory.
    variants: variantsFor(id), enabled: true,
  };
}
type Inventory = { entries: Record<string, unknown>[]; incomplete: boolean; diagnostics: string[] };
function mapInventory(inventory: Inventory, metadata: unknown): { models: Model.Info[]; diagnostics: string[] } {
  const configured = record(metadata);
  if (!configured) return { models: [], diagnostics: ["Invalid model discovery/configuration data; verify the upstream models response and options.modelMetadata object."] };
  const models: Model.Info[] = [];
  let incomplete = inventory.incomplete;
  for (const entry of inventory.entries) {
    const id = entry.id as string;
    const model = mapModel(id, entry, Object.hasOwn(configured, id) ? configured[id] : undefined);
    if (model) models.push(model); else incomplete = true;
  }
  return { models, diagnostics: [...inventory.diagnostics, ...(incomplete ? [metadataGuidance] : [])] };
}
async function discoverInventory(fetch: ProviderFetch, baseURL: string, signal?: AbortSignal): Promise<Inventory> {
  try {
    const response = await fetch(baseURL + "/models", { method: "GET", ...(signal ? { signal } : {}) });
    if (!response.ok) {
      await response.body?.cancel();
      return { entries: [], incomplete: false, diagnostics: [response.status === 401 || response.status === 403 ? "Model discovery was not authorized; reconnect Vibe OIDC and verify account model access." : "Model discovery failed upstream; retry explicitly and verify the configured audience."] };
    }
    const payload: unknown = await response.json();
    const data = record(payload)?.data;
    if (!Array.isArray(data)) return { entries: [], incomplete: false, diagnostics: ["Invalid model discovery/configuration data; verify the upstream models response and options.modelMetadata object."] };
    const entries: Record<string, unknown>[] = []; let incomplete = false;
    const seen = new Set<string>();
    for (const item of data) {
      const entry = record(item);
      if (!entry || !text(entry.id) || seen.has(entry.id)) { incomplete = true; continue; }
      seen.add(entry.id);
      // Retain only authenticated IDs and metadata fields; never credentials,
      // destination settings or SDK overlays from the upstream response.
      const safe: Record<string, unknown> = { id: entry.id };
      for (const field of ["name", "capabilities", "limit", "time", "cost", "status"]) safe[field] = entry[field];
      entries.push(safe);
    }
    return { entries, incomplete, diagnostics: [] };
  } catch {
    return { entries: [], incomplete: false, diagnostics: ["Model discovery unavailable; configure an installed uv-managed mvibe-auth runtime, compatible bridge versions and current Vibe OIDC sign-in, then retry."] };
  }
}
export async function discoverModels(fetch: ProviderFetch, baseURL: string, metadata: unknown = {}, signal?: AbortSignal): Promise<{ models: Model.Info[]; diagnostics: string[] }> {
  return mapInventory(await discoverInventory(fetch, baseURL, signal), metadata);
}

/** Async work stays outside the host's synchronous provider transform. */
type ConnectionInfo = NonNullable<Awaited<ReturnType<Plugin.Context["integration"]["connection"]["active"]>>>;
/** Timing seam mirroring the `bridge.ts` runtime injection so tests use fake timers. */
export interface CatalogTimer {
  timer: (callback: () => void, ms: number) => ReturnType<typeof setTimeout>;
  clearTimer: (timer: ReturnType<typeof setTimeout>) => void;
}
/** Per-attempt discovery deadline; `deadlineMs` defaults to 30 000, values <= 0 disable it (tests only). */
export interface ModelCatalogOptions {
  deadlineMs?: number;
  timer?: CatalogTimer;
}
export function createModelCatalog(input: {
  active: () => Promise<ConnectionInfo | undefined>;
  reload: () => Promise<void>;
  bridge: Pick<StdioBridge, "request">;
  fetch: ProviderFetch;
  baseURL: string;
  metadata: unknown;
  report: (message: string) => void;
} & ModelCatalogOptions) {
  let inventory: Inventory | undefined;
  let sourceConnection: ConnectionInfo | undefined;
  const metadata = () => typeof input.metadata === "function" ? (input.metadata as () => unknown)() : input.metadata;
  const clear = () => { inventory = undefined; sourceConnection = undefined; };
  let generation = 0, closed = false;
  let controller: AbortController | undefined;
  let pending: Promise<void> = Promise.resolve();
  const io: CatalogTimer = input.timer ?? { timer: setTimeout, clearTimer: clearTimeout };
  const deadlineMs = input.deadlineMs ?? 30_000;
  let deadline: ReturnType<typeof setTimeout> | undefined;
  const clearDeadline = () => { if (deadline !== undefined) { io.clearTimer(deadline); deadline = undefined; } };
  const key = (connection: ConnectionInfo | undefined) => connection?.type === "credential" ? "credential:" + connection.id : connection ? "env:" + connection.name : undefined;
  const invalidate = async () => {
    const hadCatalog = sourceConnection !== undefined;
    clear();
    if (hadCatalog) await input.reload();
  };
  async function run() {
      if (closed) return;
      const turn = ++generation;
      clearDeadline();
      controller?.abort(); const current = controller = new AbortController();
      let armed: ReturnType<typeof setTimeout> | undefined;
      if (deadlineMs > 0) {
        armed = io.timer(() => {
          if (closed || turn !== generation) return;
          // Deadline by detachment, not abort: aborting would retire the shared
          // helper session and fail unrelated in-flight requests. The attempt is
          // abandoned instead — its generation goes stale so the late result is
          // discarded by the checks below; only close() ever aborts.
          generation++;
          deadline = undefined;
          input.report(`Model discovery timed out after ${deadlineMs / 1000}s; kept the last known model list.`);
        }, deadlineMs);
        deadline = armed;
      }
      try {
        const connection = await input.active();
        if (turn !== generation || closed) return;
        // A same-account update must not remove models that are currently
        // being resolved. Different/disconnected accounts invalidate at once.
        if (key(connection) !== key(sourceConnection)) await invalidate();
        if (!connection || turn !== generation || closed) return;
        const status = await input.bridge.request("status", {}, current.signal);
        if (!status.config_match || !status.signed_in) {
          if (turn === generation && !closed) {
            await invalidate();
            input.report("Model discovery requires current Vibe OIDC sign-in matching the configured audience; connect using Vibe OIDC.");
          }
          return;
        }
        const result = await discoverInventory(input.fetch, input.baseURL, current.signal);
        const active = await input.active();
        if (turn !== generation || closed) return;
        if (key(active) !== key(connection)) { await invalidate(); return; }
        inventory = result; sourceConnection = connection;
        for (const message of mapInventory(result, metadata()).diagnostics) input.report(message);
        await input.reload();
      } catch {
        if (turn !== generation || closed) return;
        await invalidate();
        input.report("Model catalog unavailable; verify the installed uv-managed helper, compatible OpenCode V2/bridge versions and Vibe OIDC connection, then retry.");
      } finally {
        if (armed !== undefined && deadline === armed) clearDeadline();
      }
  }
  return {
    get snapshot(): { models: readonly Model.Info[]; sourceConnection?: ConnectionInfo } {
      // The host runs transforms lazily. Resolve metadata only after its
      // canonical catalog is available, without doing IO inside the transform.
      return inventory && sourceConnection ? { models: mapInventory(inventory, metadata()).models, sourceConnection } : { models: [] };
    },
    close() { closed = true; generation++; controller?.abort(); clearDeadline(); clear(); },
    async refresh() {
      if (closed) return;
      pending = run();
      // Setup and event callers wait for the latest generation, including a
      // replacement started while their own request was being cancelled.
      let observed: Promise<void>;
      do { observed = pending; await observed; } while (!closed && observed !== pending);
    },
  };
}
