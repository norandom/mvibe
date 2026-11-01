import { createHmac, randomBytes, randomUUID } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { isAbsolute, join } from "node:path";
import { execFileSync } from "node:child_process";
import { userInfo } from "node:os";

export const CAPTURE_BODY_LIMIT = 256 * 1024;
export const CAPTURE_REQUEST_LIMIT = 64;
const RECORD_LIMIT = 384 * 1024;
const SESSION_LIMIT = 8 * 1024 * 1024;
export type CaptureOptions = { captureDirectory?: string; captureBody?: boolean };
type Sink = (name: string, data: string) => void;
const tenantField = /(?:tenant|organization|workspace)[_-]?id/i;
const secretField = /^(?:token|bearer)$|authorization|cookie|password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|credential/i;
const allowedHeaders = new Set(["accept", "content-type", "content-length", "user-agent", "x-client-name", "x-client-version", "x-request-id", "request-id", "x-correlation-id", "traceparent", "x-should-retry", "retry-after"]);

function privateSink(directory: string): Sink {
  let session: string | undefined;
  return (name, data) => {
    if (!session) {
      mkdirSync(directory, { recursive: true, mode: 0o700 });
      const candidate = join(directory, `session-${randomUUID()}`);
      mkdirSync(candidate, { mode: 0o700 });
      // Windows modes alone are insufficient: restrict the empty directory first.
      if (process.platform === "win32") execFileSync("icacls.exe", [candidate, "/inheritance:r", "/grant:r", `${userInfo().username}:(OI)(CI)F`], { windowsHide: true, stdio: "ignore", timeout: 5000 });
      session = candidate;
    }
    writeFileSync(join(session, name), data, { flag: "wx", mode: 0o600 });
  };
}

/** Never reads or clones request/response streams. IO failures disable capture. */
export function createHttpCapture(options: CaptureOptions, suppliedSink?: Sink) {
  const enabled = typeof options.captureDirectory === "string" && isAbsolute(options.captureDirectory);
  const sink = suppliedSink ?? (enabled ? privateSink(options.captureDirectory!) : undefined);
  const salt = randomBytes(32);
  let count = 0, bytes = 0, stopped = false;
  const pseudo = (value: string) => `tenant-${createHmac("sha256", salt).update(value).digest("hex").slice(0, 16)}`;
  const write = (id: string, event: string, record: object) => {
    if (!enabled || stopped || !sink) return;
    try {
      const data = JSON.stringify({ event, request_id: id, captured_at: new Date().toISOString(), ...record }, null, 2);
      const size = Buffer.byteLength(data);
      if (size > RECORD_LIMIT || bytes + size > SESSION_LIMIT - 1024) {
        sink("capture-limit.json", JSON.stringify({ event: "capture_limit", reason: size > RECORD_LIMIT ? "record_size" : "session_bytes" }));
        stopped = true; return;
      }
      sink(`${id}-${event}.json`, data); bytes += size;
    } catch { stopped = true; }
  };
  return {
    request(request: Request, body: string | undefined, resolvedHeaders: Record<string, string>) {
      if (!enabled || stopped) return undefined;
      if (count >= CAPTURE_REQUEST_LIMIT) { write("limit", "capture_limit", { reason: "request_count" }); stopped = true; return undefined; }
      count++;
      const id = randomUUID();
      const values = Object.entries(resolvedHeaders).filter(([key]) => tenantField.test(key) || secretField.test(key))
        .flatMap(([key, value]) => [[value, tenantField.test(key) ? pseudo(value) : "[REDACTED]"], ...(/^Bearer /i.test(value) ? [[value.slice(7), "[REDACTED]"]] : [])])
        .filter(([value]) => !!value).sort((a, b) => b[0]!.length - a[0]!.length);
      const clean = (text: string) => { for (const [value, replacement] of values) text = text.split(value!).join(replacement!); return text; };
      const sanitize = (value: unknown, depth = 0): unknown => {
        if (depth > 40) return "[OMITTED_DEPTH_LIMIT]";
        if (typeof value === "string") return clean(value);
        if (Array.isArray(value)) return value.map(item => sanitize(item, depth + 1));
        if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([key, item]) => [clean(key), tenantField.test(key) ? pseudo(String(item)) : secretField.test(key) ? "[REDACTED]" : sanitize(item, depth + 1)]));
        return value;
      };
      const headerValue = (value: string) => { const redacted = clean(value); return redacted.length > 2048 ? redacted.slice(0, 2048) + "[TRUNCATED]" : redacted; };
      const headers = (input: Headers) => Object.fromEntries([...input].map(([key, value]) => [clean(key), tenantField.test(key) ? pseudo(value) : allowedHeaders.has(key) && !secretField.test(key) ? headerValue(value) : "[REDACTED]"]));
      const target = new URL(request.url);
      for (const key of [...target.searchParams.keys()]) target.searchParams.set(key, "[REDACTED]");
      let bodyState = options.captureBody === true ? "unavailable_without_stream_read" : "disabled";
      let payload: unknown;
      if (options.captureBody === true && body !== undefined) {
        if (Buffer.byteLength(body) > CAPTURE_BODY_LIMIT) bodyState = "omitted_size_limit";
        else { try { payload = sanitize(JSON.parse(body)); bodyState = "captured_redacted_json"; } catch { bodyState = "omitted_invalid_json"; } }
      }
      write(id, "request", { method: request.method, url: clean(target.href), headers: headers(request.headers), body_state: bodyState, ...(payload === undefined ? {} : { body: payload }) });
      return {
        response(response: Response) { write(id, "response", { status: response.status, headers: headers(response.headers), body_state: "not_read" }); },
        failed() { write(id, "transport_error", { outcome: request.signal.aborted ? "cancelled" : "failed", body_state: "not_read" }); },
      };
    },
  };
}
