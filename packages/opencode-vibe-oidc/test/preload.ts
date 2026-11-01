/** Test-only external boundaries, installed before any provider imports. */
import { beforeEach, afterEach } from "bun:test";
import net from "node:net";
import tls from "node:tls";
import http from "node:http";
import https from "node:https";
import dns from "node:dns";
import dgram from "node:dgram";
import childProcess from "node:child_process";
import { syncBuiltinESMExports } from "node:module";
import { mkdtempSync, mkdirSync, rmSync, realpathSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

export function denyExternal(): never {
  throw new Error("offline test blocked external effect");
}

export function guarded(_probe: () => unknown): () => never {
  return denyExternal;
}

export function installExtendedNetworkGuards(dnsTarget: object, udpTarget: object): void {
  const patchMethods = (target: object, pattern: RegExp): void => {
    // Resolver APIs may live on a shared base prototype rather than directly
    // on Resolver.prototype, so include the full chain up to Object.prototype.
    for (let current = target; current && current !== Object.prototype; current = Object.getPrototypeOf(current) as object) {
      for (const name of Object.getOwnPropertyNames(current)) {
        if (pattern.test(name) && typeof (current as Record<string, unknown>)[name] === "function")
          Object.assign(current, { [name]: denyExternal });
      }
    }
  };
  for (const api of [dnsTarget, (dnsTarget as { promises?: object }).promises]) {
    if (!api) continue;
    patchMethods(api, /^(lookup|resolve|reverse)/);
    const resolver = (api as { Resolver?: { prototype: object } }).Resolver;
    if (resolver) patchMethods(resolver.prototype, /^(lookup|resolve|reverse)/);
  }
  const socket = (udpTarget as { Socket?: { prototype: object } }).Socket;
  if (socket) patchMethods(socket.prototype, /^(send|sendto|connect)$/);
}

{
  globalThis.fetch = Object.assign(() => denyExternal(), { preconnect: denyExternal });
  globalThis.WebSocket = class { constructor() { denyExternal(); } } as unknown as typeof WebSocket;
  Object.assign(net, { connect: denyExternal, createConnection: denyExternal });
  Object.assign(net.Socket.prototype, { connect: denyExternal });
  Object.assign(tls, { connect: denyExternal });
  Object.assign(http, { request: denyExternal, get: denyExternal });
  Object.assign(https, { request: denyExternal, get: denyExternal });
  Object.assign(dns, { lookup: denyExternal, resolve: denyExternal });
  Object.assign(dns.promises, { lookup: denyExternal, resolve: denyExternal });
  installExtendedNetworkGuards(dns, dgram);
  // Browser launch and keyring command execution both cross this boundary.
  for (const name of ["spawn", "spawnSync", "exec", "execSync", "execFile", "execFileSync", "fork"])
    Object.assign(childProcess, { [name]: denyExternal });
  Object.assign(Bun, { connect: denyExternal, spawn: denyExternal, spawnSync: denyExternal });
  syncBuiltinESMExports();
}

export class Clock {
  now = 1_700_000_000_000;
  advance(milliseconds: number): void {
    if (milliseconds < 0) throw new Error("time cannot move backwards");
    this.now += milliseconds;
  }
  async sleep(milliseconds: number): Promise<void> { this.advance(milliseconds); }
}

export class ControlledHTTP {
  readonly requests: Request[] = [];
  private readonly responses: { method: string; url: string; response: Response }[] = [];
  queue(method: string, url: string, response: Response): void { this.responses.push({ method, url, response }); }
  fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const request = new Request(input, init);
    const next = this.responses.shift();
    if (!next || request.method !== next.method || request.url !== next.url)
      throw new Error("unexpected synthetic HTTP request");
    this.requests.push(request);
    return next.response;
  };
}

export class Workers {
  private readonly cleanups: (() => void | Promise<void>)[] = [];
  own(cleanup: () => void | Promise<void>): void { this.cleanups.push(cleanup); }
  async close(): Promise<void> {
    const errors: unknown[] = [];
    while (this.cleanups.length) {
      try { await this.cleanups.pop()!(); } catch (error) { errors.push(error); }
    }
    if (errors.length) throw new AggregateError(errors, "test worker cleanup failed");
  }
}

export const state = {
  clock: new Clock(), http: new ControlledHTTP(), store: new Map<string, unknown>(),
  identity: new Map<string, unknown>(), tenant: new Map<string, unknown>(), tls: new Map<string, unknown>(),
  workers: new Workers(), root: "",
};
function isolateEnvironment(root: string): void {
  for (const name of Object.keys(process.env)) {
    if (/^(VIBE_|MISTRAL_|OPENAI_|OPENCODE_)/.test(name) ||
        /^(https?_proxy|all_proxy|no_proxy|ssl_cert_file|ssl_cert_dir|node_extra_ca_certs|requests_ca_bundle|curl_ca_bundle)$/i.test(name))
      delete process.env[name];
  }
  for (const name of ["HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"]) {
    const path = join(root, name.toLowerCase());
    mkdirSync(path);
    process.env[name] = path;
  }
}
// Quarantine environment before Bun evaluates test/application modules.
// macOS exposes /var through /private/var; fixture guards require canonical roots.
const sessionRoot = realpathSync(mkdtempSync(join(tmpdir(), "vibe-offline-import-")));
isolateEnvironment(sessionRoot);
process.on("exit", () => rmSync(sessionRoot, { recursive: true, force: true }));
let originalEnv: NodeJS.ProcessEnv;
beforeEach(() => {
  originalEnv = { ...process.env };
  state.root = realpathSync(mkdtempSync(join(tmpdir(), "vibe-offline-")));
  isolateEnvironment(state.root);
  state.clock = new Clock(); state.http = new ControlledHTTP(); state.workers = new Workers();
  for (const map of [state.store, state.identity, state.tenant, state.tls]) map.clear();
});
afterEach(async () => {
  try { await state.workers.close(); }
  finally {
    for (const map of [state.store, state.identity, state.tenant, state.tls]) map.clear();
    for (const name of Object.keys(process.env)) delete process.env[name];
    Object.assign(process.env, originalEnv);
    rmSync(state.root, { recursive: true, force: true });
  }
});
