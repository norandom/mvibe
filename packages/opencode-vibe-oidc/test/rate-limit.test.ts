/** Offline unit contracts for the plugin rate-limit module (upstream-rate-limit-queue
 * task 3.1): `rateLimit` provider options (Requirements 3.4, 3.6, 4.7) and 429
 * classification with Retry-After parsing (Requirements 1.1 to 1.5), mirroring
 * the proxy matrix in tests/test_ratelimit.py. */
import { expect, test } from "bun:test";
import { BridgeError } from "../src/bridge.js";
import { classifyRateLimit, parseRateLimitOptions, parseRetryAfter, rateFor } from "../src/rate-limit.js";

const DEFAULTS = { modelRequestsPerSecond: {}, maxWaitMs: 120_000, maxQueue: 64, backoffCapMs: 30_000 };
const FIELDS = ["requestsPerSecond", "modelRequestsPerSecond", "maxWaitMs", "maxQueue", "backoffCapMs"] as const;

function setupFailure(raw: unknown): BridgeError {
  try {
    parseRateLimitOptions(raw);
  } catch (error) {
    if (!(error instanceof BridgeError)) throw error;
    expect(error.code).toBe("setup");
    return error;
  }
  throw new Error("parseRateLimitOptions accepted invalid options");
}

// --- defaults (Requirements 3.3, 3.6, 4.7) -------------------------------------

test.each([undefined, null, {}])("rateLimit %p yields the reactive defaults", raw => {
  const options = parseRateLimitOptions(raw);
  expect(options).toEqual(DEFAULTS);
  expect(Object.hasOwn(options, "requestsPerSecond")).toBe(false);
  expect(rateFor(options, "mistral-large-latest")).toBeUndefined();
  expect(rateFor(options, "embeddings")).toBeUndefined();
});

test("observable: a pacing rate alone keeps the default maximum wait", () => {
  const options = parseRateLimitOptions({ requestsPerSecond: 0.5 });
  expect(options.maxWaitMs).toBe(120_000);
  expect(options).toEqual({ ...DEFAULTS, requestsPerSecond: 0.5 });
});

test("explicit values are parsed", () => {
  const options = parseRateLimitOptions({
    requestsPerSecond: 0.5,
    modelRequestsPerSecond: { "mistral-large-latest": 0.25, "codestral-latest": 2 },
    maxWaitMs: 90_000.5,
    maxQueue: 8,
    backoffCapMs: 10_000,
  });
  expect(options).toEqual({
    requestsPerSecond: 0.5,
    modelRequestsPerSecond: { "mistral-large-latest": 0.25, "codestral-latest": 2 },
    maxWaitMs: 90_000.5,
    maxQueue: 8,
    backoffCapMs: 10_000,
  });
});

test("an override of one field keeps the defaults of the others", () => {
  expect(parseRateLimitOptions({ maxQueue: 1 })).toEqual({ ...DEFAULTS, maxQueue: 1 });
  expect(parseRateLimitOptions({ backoffCapMs: 0.5 })).toEqual({ ...DEFAULTS, backoffCapMs: 0.5 });
});

test("unknown keys are ignored and never copied", () => {
  const options = parseRateLimitOptions({ requestsPerSecond: 1, unknown: "x", maxwaitms: -1, MaxQueue: 0 });
  expect(Object.keys(options).sort()).toEqual([...FIELDS].sort());
  expect(options).toEqual({ ...DEFAULTS, requestsPerSecond: 1 });
});

test("only own option properties are read", () => {
  expect(parseRateLimitOptions(Object.create({ maxWaitMs: -1, requestsPerSecond: 3 }))).toEqual(DEFAULTS);
});

// --- per-model rates and rateFor (Requirements 3.1, 3.2, 3.3, 3.5) ------------

test("a model override wins and other models use the default rate", () => {
  const options = parseRateLimitOptions({ requestsPerSecond: 0.5, modelRequestsPerSecond: { "codestral-latest": 2 } });
  expect(rateFor(options, "codestral-latest")).toBe(2);
  expect(rateFor(options, "mistral-large-latest")).toBe(0.5);
  expect(rateFor(options, "embeddings")).toBe(0.5);
});

test("a model override without a default leaves other models unpaced", () => {
  const options = parseRateLimitOptions({ modelRequestsPerSecond: { "codestral-latest": 2 } });
  expect(rateFor(options, "codestral-latest")).toBe(2);
  expect(rateFor(options, "mistral-large-latest")).toBeUndefined();
});

test("the models key is never paced, even with a default or an override", () => {
  expect(rateFor(parseRateLimitOptions({ requestsPerSecond: 0.5 }), "models")).toBeUndefined();
  expect(rateFor(parseRateLimitOptions({ requestsPerSecond: 0.5, modelRequestsPerSecond: { models: 3 } }), "models")).toBeUndefined();
});

test.each(["toString", "constructor", "__proto__", "hasOwnProperty", "valueOf"])("prototype key %s never shadows the default rate", key => {
  expect(rateFor(parseRateLimitOptions({ requestsPerSecond: 0.5, modelRequestsPerSecond: { "codestral-latest": 2 } }), key)).toBe(0.5);
  expect(rateFor(parseRateLimitOptions({}), key)).toBeUndefined();
});

test("a model named __proto__ is an ordinary own override", () => {
  const raw: unknown = JSON.parse('{"requestsPerSecond":1,"modelRequestsPerSecond":{"__proto__":2}}');
  const options = parseRateLimitOptions(raw);
  expect(rateFor(options, "__proto__")).toBe(2);
  expect(rateFor(options, "other")).toBe(1);
  expect(Object.getPrototypeOf(options.modelRequestsPerSecond)).toBe(Object.prototype);
});

test("options are frozen copies of the input", () => {
  const raw = { requestsPerSecond: 0.5, modelRequestsPerSecond: { "codestral-latest": 2 } as Record<string, number> };
  const options = parseRateLimitOptions(raw);
  raw.modelRequestsPerSecond["codestral-latest"] = 9;
  raw.modelRequestsPerSecond["added-later"] = 1;
  raw.requestsPerSecond = 7;
  expect(options).toEqual({ ...DEFAULTS, requestsPerSecond: 0.5, modelRequestsPerSecond: { "codestral-latest": 2 } });
  expect(Object.isFrozen(options)).toBe(true);
  expect(Object.isFrozen(options.modelRequestsPerSecond)).toBe(true);
  expect(Reflect.set(options, "maxQueue", 1)).toBe(false);
  expect(Reflect.set(options.modelRequestsPerSecond, "other", 1)).toBe(false);
  expect(Object.isFrozen(parseRateLimitOptions(undefined).modelRequestsPerSecond)).toBe(true);
});

// --- invalid options (Requirement 3.4) -----------------------------------------

const INVALID_NUMBERS: unknown[] = ["fast", "0.5", "", 0, -0, -1, -0.5, Number.NaN, Infinity, -Infinity, null, true, [], {}, [1], 10n];

test.each(["requestsPerSecond", "maxWaitMs", "backoffCapMs"].flatMap(field => INVALID_NUMBERS.map(value => [field, value] as const)))(
  "rateLimit.%s = %p fails setup naming the option", (field, value) => {
    const message = setupFailure({ [field]: value }).message;
    expect(message).toContain(`rateLimit.${field}`);
    expect(message).toContain("positive number");
  });

test.each(["abc", "64", "", 0, -3, 1.5, 64.5, Number.NaN, Infinity, -Infinity, null, true, 10n])("rateLimit.maxQueue = %p fails setup naming the option", value => {
  const message = setupFailure({ maxQueue: value }).message;
  expect(message).toContain("rateLimit.maxQueue");
  expect(message).toContain("positive integer");
});

// Each case is a one-element tuple: test.each spreads an array case into arguments.
const NON_RECORDS: [unknown][] = [["a=1"], [1], [true], [null], [[]], [[["a", 1]]]];

test.each(NON_RECORDS)("rateLimit.modelRequestsPerSecond = %p fails setup naming the option", value => {
  expect(setupFailure({ modelRequestsPerSecond: value }).message).toContain("rateLimit.modelRequestsPerSecond");
});

test.each([0, -1, "fast", "2", Number.NaN, Infinity, null, true, {}])("a per-model rate of %p fails setup naming the model entry", value => {
  const message = setupFailure({ modelRequestsPerSecond: { "codestral-latest": 2, m: value } }).message;
  expect(message).toContain("rateLimit.modelRequestsPerSecond.m");
  expect(message).toContain("positive number");
});

test.each(["", "  "])("an empty model name %p fails setup", name => {
  expect(setupFailure({ modelRequestsPerSecond: { [name]: 1 } }).message).toContain("rateLimit.modelRequestsPerSecond");
});

test.each(["bad\nname", "x".repeat(129), "a b"])("an unprintable model name is named by position, never echoed", name => {
  const message = setupFailure({ modelRequestsPerSecond: { "codestral-latest": 2, [name]: 0 } }).message;
  expect(message).toContain("rateLimit.modelRequestsPerSecond entry 2");
  expect(message).not.toContain(name);
});

const NON_OBJECTS: [unknown][] = [["fast"], [0.5], [true], [[]], [[{ requestsPerSecond: 1 }]]];

test.each(NON_OBJECTS)("rateLimit = %p fails setup naming the option", raw => {
  const message = setupFailure(raw).message;
  expect(message).toContain("rateLimit");
  expect(message).not.toContain("fast");
});

// Distinctive valid values for every field; none may appear in an error raised for another field.
const SENTINEL = { requestsPerSecond: 0.3125, modelRequestsPerSecond: { "sentinel-model-alpha": 0.4375 }, maxWaitMs: 98_765, maxQueue: 4321, backoffCapMs: 27_750 };
const SENTINEL_TEXT = ["0.3125", "sentinel-model-alpha", "0.4375", "98765", "4321", "27750"];
const INVALID = "sentinel-invalid-value";

test.each([
  ["requestsPerSecond", { requestsPerSecond: INVALID }],
  ["maxWaitMs", { maxWaitMs: INVALID }],
  ["maxQueue", { maxQueue: INVALID }],
  ["backoffCapMs", { backoffCapMs: INVALID }],
  ["modelRequestsPerSecond", { modelRequestsPerSecond: INVALID }],
  ["modelRequestsPerSecond.sentinel-model-beta", { modelRequestsPerSecond: { ...SENTINEL.modelRequestsPerSecond, "sentinel-model-beta": INVALID } }],
] as const)("rateLimit.%s errors never echo option values", (field, override) => {
  const message = setupFailure({ ...SENTINEL, ...override }).message;
  expect(message).toContain(`rateLimit.${field}`);
  expect(message).not.toContain(INVALID);
  for (const text of SENTINEL_TEXT) expect(message).not.toContain(text);
  for (const other of FIELDS) if (!field.startsWith(other)) expect(message).not.toContain(`rateLimit.${other}`);
});

// --- Retry-After parsing (Requirement 1.2) -------------------------------------

// 2025-11-09 12:00:00 UTC in epoch milliseconds; the HTTP-dates below are relative to it.
const NOW_MS = 1_762_689_600_000;
const TEN_SECONDS_AHEAD = "Sun, 09 Nov 2025 12:00:10 GMT";

test.each([["7", 7000], [" 7 ", 7000], ["0", 0], ["120", 120_000], ["007", 7000], ["1.5", 1500]] as const)(
  "delay-seconds %p is %p ms", (value, expected) => {
    expect(parseRetryAfter(value, NOW_MS)).toBe(expected);
  });

test.each([
  TEN_SECONDS_AHEAD, // IMF-fixdate
  "Sunday, 09-Nov-25 12:00:10 GMT", // obsolete RFC 850 form
  "Sun Nov  9 12:00:10 2025", // obsolete asctime form, no zone: UTC
  "Sun, 09 Nov 2025 12:00:10 -0000", // unknown zone: UTC
  "Sun, 09 Nov 2025 13:00:10 +0100", // explicit offset is honoured
  "Sun, 09 Nov 2025 07:00:10 EST", // RFC 5322 obsolete US zone
  "sun, 09 nov 2025 12:00:10 gmt", // names match case-insensitively
  "09 Nov 2025 12:00:10 UTC", // day name omitted
])("HTTP-date %p is ten seconds ahead", value => {
  expect(parseRetryAfter(value, NOW_MS)).toBe(10_000);
});

test("an HTTP-date uses the fractional now", () => {
  expect(parseRetryAfter(TEN_SECONDS_AHEAD, NOW_MS + 250)).toBe(9750);
});

test.each([
  "Sun, 09 Nov 2025 12:00:00 GMT", // exactly now
  "Sun, 09 Nov 2025 11:59:00 GMT", // one minute ago
  "Wed, 21 Oct 2015 07:28:00 GMT", // years ago
])("past or present HTTP-date %p yields 0", value => {
  expect(parseRetryAfter(value, NOW_MS)).toBe(0);
});

test.each([
  null, "", "   ",
  "abc", "soon", "7 seconds", "7s",
  "-1", "-0", "-0.5", "+5", // delay-seconds is a non-negative number
  "nan", "NaN", "inf", "Infinity", "1e3", "0x10", "1_000", "1.", ".5", "1,5", "3, 5",
  "١٢", // non-ASCII digits
  "9".repeat(400), // overflows to infinity
  "Wed, 99 Foo 2015 07:28:00 GMT", // not a date
  "Wed, 21 Oct 2015", // date without a time
  "Sat, 01 Jan 10000 00:00:00 GMT", // year out of range
  "2025-11-09T12:00:10Z", // ISO 8601 is not an HTTP-date
  "Sun, 31 Nov 2025 12:00:10 GMT", // no such day
  "Sat, 29 Feb 2025 12:00:00 GMT", // not a leap year
  "Sun, 09 Nov 2025 24:00:00 GMT", // hour out of range
  "Sun, 09 Nov 2025 12:60:00 GMT", // minute out of range
  "Sun, 09 Nov 2025 12:00:60 GMT", // second out of range
  "Sun, 09 Nov 2025 12:00:10 +2400", // offset out of range
  "Sun, 09 Nov 2025 12:00:10 CET", // zone not known to RFC 5322
])("garbage Retry-After %p yields undefined", value => {
  expect(parseRetryAfter(value, NOW_MS)).toBeUndefined();
});

test.each([Number.NaN, Infinity, -Infinity])("an HTTP-date against a non-finite now %p yields undefined", now => {
  expect(parseRetryAfter(TEN_SECONDS_AHEAD, now)).toBeUndefined();
});

test("delay-seconds does not depend on now", () => {
  expect(parseRetryAfter("7", Number.NaN)).toBe(7000);
});

// --- classification (Requirements 1.1, 1.3, 1.4, 1.5, 1.6) ---------------------

function upstream(status: number, headers: HeadersInit = {}, body: BodyInit | null = null): Response {
  return new Response(body, { status, headers });
}

function classify(response: Response, { fallbackWaitMs = 1000, capMs = 30_000, nowMs = NOW_MS } = {}) {
  return classifyRateLimit(response, fallbackWaitMs, capMs, nowMs);
}

test("observable: Retry-After seconds set the wait", () => {
  expect(classifyRateLimit(upstream(429, { "retry-after": "3" }), 1000, 30_000, 0)?.waitMs).toBe(3000);
});

test.each([200, 201, 400, 401, 403, 404, 428, 430, 500, 502, 503, 504])("status %p is not a rate-limit rejection", status => {
  expect(classify(upstream(status, { "retry-after": "7", "x-ratelimit-remaining-req-minute": "0" }))).toBeUndefined();
});

test("a 429 without Retry-After uses the fallback", () => {
  expect(classify(upstream(429, { "content-type": "application/json" }), { fallbackWaitMs: 2000 }))
    .toEqual({ waitMs: 2000, retryAfterPresent: false, headers: {} });
});

test("a header-less 429 fallback is capped", () => {
  expect(classify(upstream(429), { fallbackWaitMs: 45_000, capMs: 30_000 })).toEqual({ waitMs: 30_000, retryAfterPresent: false, headers: {} });
});

test.each([1, 500, 1000, 29_999, 30_000, 30_001, 1e12, Infinity])("a fallback of %p ms is positive and never exceeds the cap", fallbackWaitMs => {
  const signal = classify(upstream(429), { fallbackWaitMs, capMs: 30_000 });
  expect(signal?.waitMs).toBe(Math.min(fallbackWaitMs, 30_000));
  expect(signal!.waitMs).toBeGreaterThan(0);
  expect(signal!.waitMs).toBeLessThanOrEqual(30_000);
});

test("an unbounded cap leaves the fallback unchanged", () => {
  expect(classify(upstream(429), { fallbackWaitMs: 5000, capMs: Infinity })?.waitMs).toBe(5000);
});

test("Retry-After seconds set the wait", () => {
  expect(classify(upstream(429, { "retry-after": "7" }))).toEqual({ waitMs: 7000, retryAfterPresent: true, headers: {} });
});

test("a Retry-After HTTP-date sets the wait", () => {
  expect(classify(upstream(429, { "retry-after": TEN_SECONDS_AHEAD }))).toEqual({ waitMs: 10_000, retryAfterPresent: true, headers: {} });
});

test("a Retry-After in the past yields a zero wait", () => {
  expect(classify(upstream(429, { "retry-after": "Wed, 21 Oct 2015 07:28:00 GMT" }), { fallbackWaitMs: 5000 }))
    .toEqual({ waitMs: 0, retryAfterPresent: true, headers: {} });
});

test("Retry-After is not capped here; the gate clamps it to maxWaitMs", () => {
  expect(classify(upstream(429, { "retry-after": "45" }), { capMs: 30_000 })?.waitMs).toBe(45_000);
});

test.each(["soon", "-1", "", "3, 5", "1e3", "Infinity"])("an unusable Retry-After %p counts as absent", value => {
  expect(classify(upstream(429, { "retry-after": value }), { fallbackWaitMs: 4000 })).toEqual({ waitMs: 4000, retryAfterPresent: false, headers: {} });
});

test("repeated Retry-After headers combine to an unusable value", () => {
  const headers = new Headers([["retry-after", "3"], ["retry-after", "5"]]);
  expect(classify(upstream(429, headers), { fallbackWaitMs: 4000 })).toEqual({ waitMs: 4000, retryAfterPresent: false, headers: {} });
});

test("an HTTP-date against a non-finite now counts as absent", () => {
  expect(classify(upstream(429, { "retry-after": TEN_SECONDS_AHEAD }), { fallbackWaitMs: 4000, nowMs: Number.NaN }))
    .toEqual({ waitMs: 4000, retryAfterPresent: false, headers: {} });
});

test.each(["Retry-After", "RETRY-AFTER", "retry-after"])("the %s name matches case-insensitively", name => {
  expect(classify(upstream(429, { [name]: "7" }))).toEqual({ waitMs: 7000, retryAfterPresent: true, headers: {} });
});

test.each(["application/json", "text/html", "text/plain", ""])("a 429 is classified whatever the content type %p", contentType => {
  expect(classify(upstream(429, { "content-type": contentType }, "<html>Too Many Requests</html>"))).toBeDefined();
});

// Distinctive values that must never reach the signal (1.6).
const SECRET_HEADERS: Record<string, string> = {
  Authorization: "Bearer sk-sentinel-secret-value",
  Cookie: "session=sentinel-cookie",
  "Set-Cookie": "session=sentinel-set-cookie",
  "Content-Type": "application/json",
  "Content-Length": "87",
  "X-Request-Id": "sentinel-request-id",
  "X-Mistral-Error": '{"message":"Rate limit exceeded","code":"1300"}',
  "RateLimit-Policy": "sentinel-ietf-policy",
  "X-RateLimit": "sentinel-no-dash",
  "X-RateLimitRemaining": "sentinel-no-separator",
};

test("the header snapshot keeps only x-ratelimit-* headers", () => {
  const signal = classify(upstream(429, {
    ...SECRET_HEADERS,
    "Retry-After": "3",
    "X-RateLimit-Limit-Req-Minute": "5",
    "x-ratelimit-remaining-req-minute": "0",
  }, '{"message":"Rate limit exceeded","code":"1300"}'));
  expect(signal?.headers).toEqual({ "x-ratelimit-limit-req-minute": "5", "x-ratelimit-remaining-req-minute": "0" });
  expect(Object.keys(signal!).sort()).toEqual(["headers", "retryAfterPresent", "waitMs"]);
  const rendered = JSON.stringify(signal);
  for (const [name, value] of Object.entries(SECRET_HEADERS)) {
    expect(rendered).not.toContain(value);
    expect(Object.hasOwn(signal!.headers, name.toLowerCase())).toBe(false);
  }
  expect(rendered).not.toContain("Rate limit exceeded");
  expect(rendered).not.toContain("retry-after");
});

test("the header snapshot is recorded without Retry-After", () => {
  expect(classify(upstream(429, { "x-ratelimit-remaining-req-minute": "0" })))
    .toEqual({ waitMs: 1000, retryAfterPresent: false, headers: { "x-ratelimit-remaining-req-minute": "0" } });
});

test.each([[0, 0], [63, 63], [64, 64], [65, 64], [500, 64]] as const)("a %p-character snapshot value keeps %p characters", (length, kept) => {
  expect(classify(upstream(429, { "x-ratelimit-note": "v".repeat(length) }))?.headers["x-ratelimit-note"]).toBe("v".repeat(kept));
});

test("the signal is frozen and its snapshot is a copy", () => {
  const response = upstream(429, { "x-ratelimit-remaining-req-minute": "0" });
  const signal = classify(response)!;
  response.headers.set("x-ratelimit-remaining-req-minute", "9");
  response.headers.set("x-ratelimit-added-later", "1");
  expect(signal.headers).toEqual({ "x-ratelimit-remaining-req-minute": "0" });
  expect(Object.isFrozen(signal)).toBe(true);
  expect(Object.isFrozen(signal.headers)).toBe(true);
  expect(Reflect.set(signal.headers, "x-ratelimit-other", "1")).toBe(false);
  expect(Reflect.set(signal, "waitMs", 0)).toBe(false);
});

test.each([429, 200, 503])("classifying a %p never reads the body", status => {
  let pulled = false;
  const body = new ReadableStream<Uint8Array>({ pull() { pulled = true; throw new Error("body read"); } }, { highWaterMark: 0 });
  const response = upstream(status, { "retry-after": "2", "x-ratelimit-remaining-req-minute": "0" }, body);
  const signal = classify(response);
  expect(signal).toEqual(status === 429 ? { waitMs: 2000, retryAfterPresent: true, headers: { "x-ratelimit-remaining-req-minute": "0" } } : undefined);
  expect(pulled).toBe(false);
  expect(response.bodyUsed).toBe(false);
});

test("the body stays intact for the caller after classification", async () => {
  const payload = '{"object":"error","message":"Rate limit exceeded","code":"1300"}';
  const response = upstream(429, { "content-type": "application/json" }, payload);
  expect(classify(response)).toBeDefined();
  expect(await response.text()).toBe(payload);
});

test.each([429, 200].flatMap(status => [
  [0, 30_000], [-1, 30_000], [Number.NaN, 30_000], [-Infinity, 30_000],
  [1000, 0], [1000, -1], [1000, Number.NaN], [1000, -Infinity],
].map(([fallbackWaitMs, capMs]) => [status, fallbackWaitMs!, capMs!] as const)))(
  "status %p with fallback %p and cap %p is rejected before the status check", (status, fallbackWaitMs, capMs) => {
    expect(() => classify(upstream(status), { fallbackWaitMs, capMs })).toThrow(RangeError);
  });
