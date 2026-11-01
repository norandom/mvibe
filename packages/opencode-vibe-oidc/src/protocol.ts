/** Private protocol v1. Unknown bytes are checked before any type narrowing. */
import canonicalSchema from "./protocol.schema.json" with { type: "json" };

export const MAX_FRAME_BYTES = 1_048_576;
export type Method = "hello" | "status" | "login.start" | "login.wait" | "login.cancel" | "resolve" | "shutdown";
export type ErrorCode = "missing" | "invalid" | "rejected" | "transient" | "conflict" | "cancelled" | "unsupported_version" | "unknown_method" | "invalid_frame" | "frame_too_large";
export type AuthError = { code: ErrorCode; message: string; retryable: boolean };
export type RequestContext = { session_id?: string; message_id?: string; parent_session_id?: string; client_name?: string; client_version?: string; agent_entrypoint?: "cli" | "acp" | "desktop" | "programmatic" | "unknown"; agent_version?: string; purpose?: "agent" | "compaction" | "classify" | "title" | "generate"; iteration?: number };
export type Params = {
  hello: Record<string, never>;
  status: Record<string, never>;
  "login.start": { force: boolean };
  "login.wait": { attempt_id: string };
  "login.cancel": { attempt_id: string };
  resolve: { target_url: string; request_context?: RequestContext };
  shutdown: Record<string, never>;
};
export type Results = {
  hello: { protocol: 1; api_version: string; audience: string };
  status: { signed_in: boolean; config_match: boolean };
  "login.start": { attempt_id: string; authorization_url: string; expires_at: number };
  "login.wait": { outcome: "completed" | "reused_current" };
  "login.cancel": { cancelled: boolean };
  resolve: { headers: Record<string, string>; expires_at: number; revision: string; target: string; metadata?: Record<string, string> };
  shutdown: { closed: boolean };
};
export type Request = { [M in Method]: { version: 1; id: string; method: M; params: Params[M] } }[Method];
export type Success<M extends Method = Method> = { version: 1; id: string; ok: true; result: Results[M] };
export type Failure = { version: 1; id: string; ok: false; error: AuthError };
export type Response<M extends Method = Method> = Success<M> | Failure;

export class ProtocolError extends Error {
  constructor(readonly code: ErrorCode = "invalid_frame") {
    const messages: Partial<Record<ErrorCode, string>> = { unsupported_version: "Unsupported bridge version; install compatible library and provider versions.", unknown_method: "Unknown bridge method; install compatible library and provider versions.", frame_too_large: "Bridge frame exceeds 1 MiB." };
    super(messages[code] ?? "Invalid bridge frame.");
    this.name = "ProtocolError";
  }
}

type Schema = { $ref?: string; type?: string; const?: unknown; enum?: unknown[]; oneOf?: Schema[]; properties?: Record<string, Schema>; required?: string[]; additionalProperties?: boolean | Schema; minLength?: number; maxLength?: number; minimum?: number; maximum?: number };
const definitions: Record<string, Schema> = canonicalSchema.$defs;
const record = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const owns = (value: object, key: string) => Object.prototype.hasOwnProperty.call(value, key);

/** Evaluate only the JSON Schema keywords used by our canonical schema. */
function matches(value: unknown, schema: Schema): boolean {
  if (schema.$ref) {
    const definition = definitions[schema.$ref.replace("#/$defs/", "")];
    return definition !== undefined && matches(value, definition);
  }
  if (schema.oneOf && schema.oneOf.filter(choice => matches(value, choice)).length !== 1) return false;
  if (owns(schema, "const") && value !== schema.const) return false;
  if (schema.enum && !schema.enum.includes(value)) return false;
  switch (schema.type) {
    case "object": {
      if (!record(value)) return false;
      if (schema.required?.some(key => !owns(value, key))) return false;
      const properties = schema.properties ?? {};
      for (const [key, nested] of Object.entries(value)) {
        if (owns(properties, key)) {
          const property = properties[key];
          if (!property || !matches(nested, property)) return false;
        } else if (schema.additionalProperties === false) return false;
        else if (typeof schema.additionalProperties === "object" && !matches(nested, schema.additionalProperties)) return false;
      }
      break;
    }
    case "string": return typeof value === "string" && value.length >= (schema.minLength ?? 0) && [...value].length <= (schema.maxLength ?? Infinity);
    case "boolean": return typeof value === "boolean";
    case "integer": return typeof value === "number" && Number.isFinite(value) && Number.isInteger(value) && value >= (schema.minimum ?? -Infinity) && value <= (schema.maximum ?? Infinity);
    case "number": return typeof value === "number" && Number.isFinite(value);
  }
  return true;
}
function decode(frame: Uint8Array): Record<string, unknown> {
  if (frame.byteLength > MAX_FRAME_BYTES) throw new ProtocolError("frame_too_large");
  if (frame.at(-1) !== 10 || frame.subarray(0, -1).includes(10) || frame.includes(13)) throw new ProtocolError();
  let value: unknown;
  try { value = JSON.parse(new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(frame)); }
  catch { throw new ProtocolError(); }
  if (!record(value)) throw new ProtocolError();
  if (owns(value, "version") && value.version !== 1) throw new ProtocolError("unsupported_version");
  return value;
}
function definition(name: string): Schema {
  const result = definitions[name];
  if (!result) throw new ProtocolError("unknown_method");
  return result;
}
export function parseRequest(frame: Uint8Array): Request {
  const value = decode(frame);
  if (typeof value.method !== "string" || !owns(definitions, value.method + ".params")) throw new ProtocolError("unknown_method");
  if (!matches(value, definition("request"))) throw new ProtocolError();
  return value as Request;
}
export function parseResponse(frame: Uint8Array, method: string): Response {
  const value = decode(frame);
  const resultSchema = definition(method + ".result");
  if (!matches(value, definition("response")) || (value.ok === true && !matches(value.result, resultSchema))) throw new ProtocolError();
  return value as Response;
}
