import { expect, test } from "bun:test";
import { Clock, Workers, guarded, state, installExtendedNetworkGuards } from "./preload.js";
import dns from "node:dns";
import dgram from "node:dgram";
import { connect } from "node:net";
import { spawn } from "node:child_process";
import { request } from "node:https";

// Collection-time probe proves the preload protects module evaluation too.
expect(() => fetch("https://issuer.invalid")).toThrow("blocked external effect");

// Construct these harmless baselines during module evaluation, before importing
// any application code. A missing guard can only increment a local counter.
let networkProbeCalls = 0;
const safeDNS = Object.fromEntries(Object.getOwnPropertyNames(dns)
  .filter(name => /^(lookup|resolve|reverse)/.test(name) && typeof (dns as unknown as Record<string, unknown>)[name] === "function")
  .map(name => [name, () => { networkProbeCalls++; return "safe-probe"; }]));
class SafeResolverBase {}
class SafeResolver extends SafeResolverBase {}
class SafePromiseResolverBase {}
class SafePromiseResolver extends SafePromiseResolverBase {}
for (const prototype of [SafeResolverBase.prototype, SafePromiseResolverBase.prototype])
  for (const name of Object.keys(safeDNS)) Object.assign(prototype, { [name]: () => { networkProbeCalls++; } });
const safePromises = { ...safeDNS, Resolver: SafePromiseResolver };
const safeDNSModule = { ...safeDNS, Resolver: SafeResolver, promises: safePromises };
class SafeUDP { send() { networkProbeCalls++; } sendto() { networkProbeCalls++; } connect() { networkProbeCalls++; } }
installExtendedNetworkGuards(safeDNSModule, { Socket: SafeUDP });

test("preimport DNS record/resolver and UDP guards replace harmless probes", () => {
  for (const target of [safeDNSModule, safePromises, new SafeResolver(), new SafePromiseResolver()] as object[]) {
    for (const name of Object.keys(safeDNS)) {
      const probe = (target as Record<string, () => unknown>)[name]!;
      expect(() => probe.call(target)).toThrow("blocked external effect");
    }
  }
  const udp = new SafeUDP();
  expect(() => udp.send()).toThrow("blocked external effect");
  expect(() => udp.sendto()).toThrow("blocked external effect");
  expect(() => udp.connect()).toThrow("blocked external effect");
  expect(networkProbeCalls).toBe(0);
});

test("native network, browser and keyring command entrypoints are denied", () => {
  for (const probe of [
    () => fetch("https://issuer.invalid"),
    () => connect({ host: "127.0.0.1", port: 1 }),
    () => request("https://issuer.invalid"),
    () => spawn("synthetic-browser", []),
    () => Bun.spawn(["synthetic-keyring"]),
    () => new WebSocket("wss://issuer.invalid"),
    () => dns.resolve4("issuer.invalid", () => {}),
    () => dns.promises.resolve6("issuer.invalid"),
    () => new dns.Resolver().resolveTxt("issuer.invalid", () => {}),
    () => new dns.promises.Resolver().resolveMx("issuer.invalid"),
  ]) expect(probe).toThrow("blocked external effect");
  const udp = dgram.createSocket("udp4");
  try { expect(() => udp.send("synthetic", 53, "192.0.2.1")).toThrow("blocked external effect"); }
  finally { udp.close(); }
});

test("external-effect guard replaces harmless baseline probes", () => {
  let effects = 0;
  const probe = guarded(() => { effects++; });
  expect(probe).toThrow("blocked external effect");
  expect(effects).toBe(0);
});

test("controlled HTTP permits only queued destinations and clock advances without waiting", async () => {
  await state.clock.sleep(300_000);
  expect(state.clock.now).toBe(new Clock().now + 300_000);
  state.http.queue("GET", "https://issuer.invalid/discovery", Response.json({ issuer: "synthetic" }));
  expect(await (await state.http.fetch("https://issuer.invalid/discovery")).json()).toEqual({ issuer: "synthetic" });
  await expect(state.http.fetch("https://uncontrolled.invalid")).rejects.toThrow("unexpected synthetic HTTP");
});

let activeSyntheticWorkers = 0;
for (const iteration of [1, 2]) test(`state starts clean on fixture use ${iteration}`, () => {
  expect(activeSyntheticWorkers).toBe(0);
  activeSyntheticWorkers++;
  state.workers.own(async () => { await Promise.resolve(); activeSyntheticWorkers--; });
  expect(process.env.VIBE_CLI_OIDC_WORKSPACE_ID).toBeUndefined();
  expect(state.clock.now).toBe(new Clock().now);
  for (const map of [state.store, state.identity, state.tenant, state.tls]) {
    expect(map.size).toBe(0);
    map.set("synthetic", iteration);
  }
  process.env.VIBE_CLI_OIDC_WORKSPACE_ID = "synthetic-tenant";
  state.clock.advance(1);
});

test("workers drain before cleanup returns, including when another cleanup fails", async () => {
  const workers = new Workers();
  const events: string[] = [];
  workers.own(async () => { await Promise.resolve(); events.push("drained"); });
  workers.own(() => { throw new Error("synthetic failure"); });
  await expect(workers.close()).rejects.toThrow("test worker cleanup failed");
  expect(events).toEqual(["drained"]);
  await workers.close();
});
