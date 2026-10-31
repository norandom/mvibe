import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { parseRequest, parseResponse } from "../src/protocol.js";

type Vector = { name: string; kind: string; method: string; value: unknown };
const vectors = JSON.parse(readFileSync(new URL("../../../tests/fixtures/bridge-v1.json", import.meta.url), "utf8")) as { valid: Vector[]; invalid: Vector[] };
const frame = (value: unknown) => new TextEncoder().encode(JSON.stringify(value) + "\n");
for (const vector of vectors.valid) test(vector.name, () => {
  expect<unknown>(vector.kind === "request" ? parseRequest(frame(vector.value)) : parseResponse(frame(vector.value), vector.method)).toEqual(vector.value);
});
for (const vector of vectors.invalid) test(vector.name, () => {
  try {
    if (vector.kind === "request") parseRequest(frame(vector.value));
    else parseResponse(frame(vector.value), vector.method);
    throw new Error("expected rejection");
  } catch (error) {
    expect(error).toBeInstanceOf(Error);
    expect(String(error)).not.toContain("synthetic-secret");
    expect(String(error)).not.toContain("expected rejection");
  }
});
for (const raw of ["{}", "{}\n{}\n", "[]\n", "{\n", "NaN\n", " ".repeat(1048576) + "\n"]) test("invalid frame " + raw.slice(0, 8), () => {
  expect(() => parseRequest(new TextEncoder().encode(raw))).toThrow();
});
test("invalid UTF8", () => { expect(() => parseRequest(new Uint8Array([255, 10]))).toThrow(); });
test("byte cap includes LF and counts UTF8 bytes", () => {
  const value = { version: 1, id: "a", method: "hello", params: {} };
  const emptySize = frame(value).length - 1;
  value.id = "a".repeat(1048576 - emptySize);
  expect(frame(value).length).toBe(1048576);
  expect<unknown>(parseRequest(frame(value))).toEqual(value);
  value.id += "a";
  expect(() => parseRequest(frame(value))).toThrow();
  value.id = "é".repeat(524288);
  expect(() => parseRequest(frame(value))).toThrow();
});
test("response uses pending request method", () => {
  const vector = vectors.valid.find(v => v.kind === "response" && v.method === "hello");
  expect(() => parseResponse(frame(vector?.value), "resolve")).toThrow();
});
test("absent and unreadable storage retain distinct typed safe errors", () => {
  const outcomes = vectors.valid.filter(v => ["store absent", "store unreadable"].includes(v.name)).map(v => parseResponse(frame(v.value), "resolve"));
  expect(outcomes.map(value => value.ok ? "unexpected-success" : value.error.code)).toEqual(["missing", "transient"]);
  expect(outcomes.map(value => value.ok ? "unexpected-success" : value.error.retryable)).toEqual([false, true]);
});
test("local parser failures expose typed safe reasons", () => {
  for (const [value, code] of [[{ version: 2 }, "unsupported_version"], [{ version: 1, method: "unknown" }, "unknown_method"]] as const) {
    try { parseRequest(frame(value)); throw new Error("expected rejection"); }
    catch (error) { expect(error).toMatchObject({ code }); }
  }
  expect(() => parseRequest(new Uint8Array(1048577))).toThrow("exceeds 1 MiB");
});
for (const kind of ["request", "response"] as const) test(`oversized integer ${kind} has safe typed error`, () => {
  const prefix = '{"version":1,"id":"synthetic-id",';
  const body = kind === "request" ? '"method":"hello","params":{},' : '"ok":false,"error":{"code":"missing","message":"safe","retryable":false},';
  const raw = new TextEncoder().encode(prefix + body + '"extra":' + "9".repeat(5000) + '}\n');
  try {
    if (kind === "request") parseRequest(raw);
    else parseResponse(raw, "hello");
    throw new Error("expected rejection");
  } catch (error) {
    expect(error).toMatchObject({ code: "invalid_frame", message: "Invalid bridge frame." });
  }
});
