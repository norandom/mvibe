/** Offline unit contracts for the plugin rate-limit module (upstream-rate-limit-queue
 * tasks 3.1, 3.2 and 3.3): `rateLimit` provider options (Requirements 3.4, 3.6, 4.7),
 * 429 classification with Retry-After parsing (Requirements 1.1 to 1.5), the
 * UpstreamGate scheduling core (Requirements 3.1, 3.5, 4.1, 4.2, 6.3) and its
 * rate-limit holds (Requirements 1.3, 4.1, 4.2, 4.3, 5.1, 5.3), mirroring the proxy
 * matrix in tests/test_ratelimit.py. */
import { expect, spyOn, test } from "bun:test";
import { BridgeError } from "../src/bridge.js";
import {
  classifyRateLimit, createGateRecord, MODELS_KEY, parseRateLimitOptions, parseRetryAfter, rateFor, UpstreamGate,
  type GateDeps, type GateRecord, type RateLimitOptions,
} from "../src/rate-limit.js";

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

// --- gate scheduling (task 3.2; Requirements 3.1, 3.5, 4.1, 4.2, 6.3) ----------

const T0 = 1_700_000_000_000;
const LARGE = "mistral-large-latest";
const PACED = parseRateLimitOptions({ requestsPerSecond: 0.5 }); // one start every 2000 ms per key
const UNPACED = parseRateLimitOptions(undefined);

type Sleep = NonNullable<GateDeps["sleep"]>;

/** One macrotask turn: every pending microtask runs first, as with asyncio.sleep(0). */
function tick(): Promise<void> {
  return new Promise(resolve => setImmediate(() => resolve()));
}

async function settle(rounds = 5): Promise<void> {
  for (let round = 0; round < rounds; round++) await tick();
}

/** Bound an await on wall-clock time, so a scheduling regression fails instead of hanging.
 * The gate keeps running on the fake clock; the bound is protection only. */
async function bounded<T>(promise: Promise<T>): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error("the gate did not settle within 5 s")), 5000);
  });
  try {
    return await Promise.race([promise, timeout]);
  } finally {
    clearTimeout(timer);
  }
}

/** Mark a run as handled at once, so a run cancelled before the test awaits it is
 * never reported as an unhandled rejection. */
function handled<T>(promise: Promise<T>): Promise<T> {
  promise.catch(() => undefined);
  return promise;
}

async function expectCancelled(run: Promise<Response>): Promise<void> {
  const error = await bounded(run).then(() => undefined, (caught: unknown) => caught);
  expect(error).toBeInstanceOf(BridgeError);
  expect(error).toMatchObject({ code: "cancelled" });
}

async function expectSynthesized429(response: Response, retryAfter: number): Promise<void> {
  expect(response.status).toBe(429);
  expect([...response.headers.keys()].sort()).toEqual(["content-type", "retry-after"]);
  expect(response.headers.get("content-type")).toBe("application/json");
  expect(response.headers.get("retry-after")).toBe(String(retryAfter));
  expect(Number(response.headers.get("retry-after"))).toBeGreaterThanOrEqual(1);
  expect(await response.text()).toBe(JSON.stringify({ error: {
    message: `Upstream rate limit persisted; retry after ${retryAfter} seconds`,
    type: "rate_limit_error",
    code: "rate_limited",
  } }));
}

/** A promise the test opens by hand. */
class Latch {
  open: () => void = () => undefined;
  readonly opened = new Promise<void>(resolve => { this.open = () => resolve(); });
}

/** A fake monotonic clock in milliseconds (mirrors tests/fakes.Clock). Its sleep
 * advances the clock by exactly the delay asked for and records it, then yields a
 * macrotask turn so the other runs proceed and bounded() can still fire. It never
 * sees an abort; the cancellation tests use HeldSleep. */
class Clock {
  time = T0;
  readonly sleeps: number[] = [];
  readonly now = (): number => this.time;
  readonly sleep: Sleep = async ms => {
    this.sleeps.push(ms);
    this.advance(ms);
    await tick();
  };

  advance(ms: number): void {
    if (!(ms >= 0)) throw new RangeError("time cannot move backwards");
    this.time += ms;
  }
}

/** A sleep that stays pending until the test wakes it, which advances the clock by
 * its delay, or until its signal aborts, which rejects it with BridgeError("cancelled")
 * as the gate's default sleep does. */
class HeldSleep {
  readonly calls: number[] = [];
  readonly #pending: { ms: number; wake: () => void }[] = [];

  constructor(private readonly clock: Clock) {}

  readonly sleep: Sleep = (ms, signal) => {
    this.calls.push(ms);
    return new Promise((resolve, reject) => {
      if (signal?.aborted) { reject(new BridgeError("cancelled")); return; }
      const entry = { ms, wake: () => { signal?.removeEventListener("abort", abort); resolve(); } };
      const abort = (): void => {
        signal?.removeEventListener("abort", abort);
        this.#pending.splice(this.#pending.indexOf(entry), 1);
        reject(new BridgeError("cancelled"));
      };
      signal?.addEventListener("abort", abort, { once: true });
      this.#pending.push(entry);
    });
  };

  get pending(): number {
    return this.#pending.length;
  }

  /** Wake the oldest pending sleep after advancing the clock by its delay. */
  wake(): void {
    const entry = this.#pending.shift();
    if (entry === undefined) throw new Error("no sleep is pending");
    this.clock.advance(entry.ms);
    entry.wake();
  }
}

function ok(status = 200): Response {
  return new Response('{"ok":true}', { status, headers: { "content-type": "application/json" } });
}

/** One scripted upstream answer: a response factory, called once per use, or an error to throw. */
type Step = (() => Response) | Error;

/** Fake `attempt` factory: logs (label, clock time) as each attempt starts. Every
 * attempt suspends at least one turn, like an upstream round trip. */
class Upstream {
  readonly sent: [label: string, at: number][] = [];
  /** Every response a scripted attempt returned, in order. */
  readonly returned: Response[] = [];

  constructor(private readonly clock: Clock) {}

  attempt(label: string, { response, release, error }: { response?: Response; release?: Latch; error?: Error } = {}): () => Promise<Response> {
    return async () => {
      this.sent.push([label, this.clock.now()]);
      await (release?.opened ?? tick());
      if (error !== undefined) throw error;
      return response ?? ok();
    };
  }

  /** An attempt that answers call n with `steps[n]`; the last step repeats (mirrors
   * Upstream.script in tests/test_ratelimit.py). `latencyMs` advances the clock inside
   * every call, like an upstream round trip; `release` holds every call until it opens. */
  script(label: string, steps: readonly Step[], { release, latencyMs = 0 }: { release?: Latch; latencyMs?: number } = {}): () => Promise<Response> {
    const remaining = [...steps];
    return async () => {
      this.sent.push([label, this.clock.now()]);
      await (release?.opened ?? tick());
      this.clock.advance(latencyMs);
      const step = remaining.length > 1 ? remaining.shift()! : remaining[0]!;
      if (step instanceof Error) throw step;
      const response = step();
      this.returned.push(response);
      return response;
    };
  }

  labels(): string[] {
    return this.sent.map(([label]) => label);
  }

  times(label?: string): number[] {
    return this.sent.filter(([name]) => label === undefined || name === label).map(([, at]) => at);
  }
}

/** An attempt that counts its calls, for runs that must not reach the upstream. */
function counted(): { attempt: () => Promise<Response>; calls: () => number } {
  let calls = 0;
  return { attempt: async () => { calls += 1; return ok(); }, calls: () => calls };
}

function never(): never {
  throw new Error("called outside rejection handling");
}

function gateOn(clock: Clock, options: RateLimitOptions = PACED, sleep: Sleep = clock.sleep): UpstreamGate {
  return new UpstreamGate(options, { now: clock.now, sleep, random: never, warn: never });
}

function run(gate: UpstreamGate, key: string, attempt: () => Promise<Response>, { record = createGateRecord(), signal }: { record?: GateRecord; signal?: AbortSignal } = {}): Promise<Response> {
  return gate.run(key, attempt, signal, record);
}

/** Spies on a signal's listener bookkeeping: `leaked()` counts the listeners that
 * were added and never removed. */
function watchListeners(signal: AbortSignal): { added: () => number; leaked: () => number } {
  const added = spyOn(signal, "addEventListener");
  const removed = spyOn(signal, "removeEventListener");
  return {
    added: () => added.mock.calls.length,
    leaked: () => {
      const gone = new Set(removed.mock.calls.map(call => call[1]));
      return added.mock.calls.filter(call => !gone.has(call[1])).length;
    },
  };
}

test("observable: three runs on one key at 0.5 rps start exactly 2000 ms apart", async () => {
  const clock = new Clock();
  const gate = gateOn(clock);
  const upstream = new Upstream(clock);
  const responses = await bounded(Promise.all([0, 1, 2].map(() => run(gate, LARGE, upstream.attempt(LARGE)))));
  const times = upstream.times();
  expect(times).toEqual([T0, T0 + 2000, T0 + 4000]);
  expect(times.slice(1).map((at, index) => at - times[index]!)).toEqual([2000, 2000]);
  expect(clock.sleeps).toEqual([2000, 2000]); // the exact remaining delta, no polling
  expect(responses.map(response => response.status)).toEqual([200, 200, 200]);
  expect(gate.held).toBe(0);
});

test("waiting time is recorded as queuedMs", async () => {
  const clock = new Clock();
  const gate = gateOn(clock);
  const upstream = new Upstream(clock);
  const release = new Latch();
  const records = [createGateRecord(), createGateRecord(), createGateRecord()] as const;
  const runs = [
    run(gate, LARGE, upstream.attempt("a1", { release }), { record: records[0] }),
    run(gate, LARGE, upstream.attempt("a2"), { record: records[1] }),
    run(gate, LARGE, upstream.attempt("a3"), { record: records[2] }),
  ];
  await settle();
  expect(upstream.labels()).toEqual(["a1"]);
  release.open();
  await bounded(Promise.all(runs));
  expect(upstream.labels()).toEqual(["a1", "a2", "a3"]);
  expect(records.map(record => record.queuedMs)).toEqual([0, 2000, 4000]);
  for (const record of records) expect({ ...record, queuedMs: 0 }).toEqual(createGateRecord());
});

test("a per-model rate overrides the default rate", async () => {
  const clock = new Clock();
  const gate = gateOn(clock, parseRateLimitOptions({ requestsPerSecond: 0.5, modelRequestsPerSecond: { "codestral-latest": 2 } }));
  const upstream = new Upstream(clock);
  await bounded(Promise.all([0, 1, 2].map(() => run(gate, "codestral-latest", upstream.attempt("c")))));
  expect(upstream.times("c")).toEqual([T0, T0 + 500, T0 + 1000]);
  const t1 = clock.now();
  await bounded(Promise.all([0, 1, 2].map(() => run(gate, LARGE, upstream.attempt("m")))));
  expect(upstream.times("m")).toEqual([t1, t1 + 2000, t1 + 4000]);
});

test("the models key is never paced, even with a default rate and an override for it", async () => {
  const clock = new Clock();
  const gate = gateOn(clock, parseRateLimitOptions({ requestsPerSecond: 0.5, modelRequestsPerSecond: { models: 0.25 } }));
  const upstream = new Upstream(clock);
  const records = [createGateRecord(), createGateRecord(), createGateRecord()];
  await bounded(Promise.all(records.map(record => run(gate, MODELS_KEY, upstream.attempt("models"), { record }))));
  expect(MODELS_KEY).toBe("models");
  expect(upstream.times()).toEqual([T0, T0, T0]);
  expect(clock.sleeps).toEqual([]);
  expect(records.map(record => record.queuedMs)).toEqual([0, 0, 0]);
});

// Each case is a one-element tuple: test.each spreads an array case into arguments.
test.each([[UNPACED], [parseRateLimitOptions({ modelRequestsPerSecond: { "codestral-latest": 2 } })]] as const)(
  "a key without a configured rate is not spaced (%#)", async options => {
    const clock = new Clock();
    const gate = gateOn(clock, options);
    const upstream = new Upstream(clock);
    await bounded(Promise.all([0, 1, 2].map(() => run(gate, LARGE, upstream.attempt(LARGE)))));
    expect(upstream.times()).toEqual([T0, T0, T0]);
    expect(clock.sleeps).toEqual([]);
  });

test("two keys do not block each other", async () => {
  const clock = new Clock();
  const gate = gateOn(clock);
  const upstream = new Upstream(clock);
  const release = new Latch();
  const a1 = run(gate, "a", upstream.attempt("a1", { release }));
  const a2 = run(gate, "a", upstream.attempt("a2"));
  await settle();
  expect(gate.held).toBe(2);
  // Key "a" has an attempt in flight, a waiter queued and its next slot at
  // T0 + 2000; key "b" is served at once all the same.
  const record = createGateRecord();
  const response = await bounded(run(gate, "b", upstream.attempt("b1"), { record }));
  expect(response.status).toBe(200);
  expect(record.queuedMs).toBe(0);
  expect(upstream.sent).toEqual([["a1", T0], ["b1", T0]]);
  release.open();
  await bounded(Promise.all([a1, a2]));
  expect(upstream.sent).toEqual([["a1", T0], ["b1", T0], ["a2", T0 + 2000]]);
  expect(gate.held).toBe(0);
});

test("one attempt is in flight per key and runs are served in arrival order", async () => {
  const clock = new Clock();
  const gate = gateOn(clock, UNPACED);
  const upstream = new Upstream(clock);
  const latches = [new Latch(), new Latch(), new Latch(), new Latch()];
  const labels = ["r0", "r1", "r2", "r3"];
  const runs = latches.map((release, n) => run(gate, LARGE, upstream.attempt(labels[n]!, { release })));
  await settle();
  expect(upstream.labels()).toEqual(["r0"]);
  for (const [n, release] of latches.entries()) {
    release.open();
    await settle();
    expect(upstream.labels()).toEqual(labels.slice(0, Math.min(n + 2, 4)));
  }
  await bounded(Promise.all(runs));
  expect(gate.held).toBe(0);
});

test("held counts waiting and in-flight runs across keys", async () => {
  const clock = new Clock();
  const gate = gateOn(clock, UNPACED);
  const upstream = new Upstream(clock);
  const release = new Latch();
  expect(gate.held).toBe(0);
  const runs = ["a", "a", "b", MODELS_KEY].map(key => run(gate, key, upstream.attempt(key, { release })));
  await settle();
  expect(gate.held).toBe(4);
  expect(upstream.labels()).toEqual(["a", "b", MODELS_KEY]); // the second "a" waits
  release.open();
  await bounded(Promise.all(runs));
  expect(gate.held).toBe(0);
});

test("observable: the 65th concurrent run gets a synthesized 429 without an attempt", async () => {
  const clock = new Clock();
  const gate = gateOn(clock, parseRateLimitOptions({ maxQueue: 64 }));
  const upstream = new Upstream(clock);
  const release = new Latch();
  const keys = ["a", "b", MODELS_KEY];
  const runs = Array.from({ length: 64 }, (_, n) => run(gate, keys[n % 3]!, upstream.attempt(`r${n}`, { release })));
  await settle();
  expect(gate.held).toBe(64);
  const record = createGateRecord();
  const extra = counted();
  const response = await bounded(run(gate, "c", extra.attempt, { record }));
  expect(extra.calls()).toBe(0);
  await expectSynthesized429(response, 1);
  expect(record).toEqual({ queuedMs: 0, rejections: 0, headers: {}, exhausted: true });
  expect(gate.held).toBe(64);
  release.open();
  const responses = await bounded(Promise.all(runs));
  expect(responses.map(answer => answer.status)).toEqual(Array.from({ length: 64 }, () => 200));
  expect(gate.held).toBe(0);
  // Capacity is back once the held runs settle.
  expect((await bounded(run(gate, "c", extra.attempt))).status).toBe(200);
  expect(extra.calls()).toBe(1);
});

// [rate, ms the clock moves after the first send, expected retry-after seconds]
test.each([[0.5, 0, 2], [0.4, 0, 3], [0.8, 0, 2], [4, 0, 1], [0.5, 1500, 2]] as const)(
  "the queue bound counts every key and a refusal at %p rps (after %p ms) hints %p s", async (rate, advance, retryAfter) => {
    const clock = new Clock();
    const gate = gateOn(clock, parseRateLimitOptions({ requestsPerSecond: rate, maxQueue: 2 }));
    const upstream = new Upstream(clock);
    const release = new Latch();
    const a1 = run(gate, "a", upstream.attempt("a1", { release }));
    const b1 = run(gate, "b", upstream.attempt("b1", { release }));
    await settle();
    clock.advance(advance);
    const record = createGateRecord();
    const response = await bounded(run(gate, "a", upstream.attempt("a2"), { record }));
    // Key "a" has its next slot one interval after its first send; the hint is
    // at least that interval, rounded up.
    await expectSynthesized429(response, retryAfter);
    expect(record.exhausted).toBe(true);
    expect(upstream.labels()).toEqual(["a1", "b1"]);
    release.open();
    await bounded(Promise.all([a1, b1]));
    expect(gate.held).toBe(0);
  });

test("a next slot past the deadline yields a synthesized 429 without waiting", async () => {
  const clock = new Clock();
  const gate = gateOn(clock, parseRateLimitOptions({ requestsPerSecond: 0.5, maxWaitMs: 1000 }));
  const upstream = new Upstream(clock);
  await bounded(run(gate, LARGE, upstream.attempt("a1")));
  const record = createGateRecord();
  const response = await bounded(run(gate, LARGE, upstream.attempt("a2"), { record }));
  await expectSynthesized429(response, 2);
  expect(record).toEqual({ queuedMs: 0, rejections: 0, headers: {}, exhausted: true });
  expect(upstream.labels()).toEqual(["a1"]);
  expect(clock.sleeps).toEqual([]); // gave up without waiting
  expect(clock.now()).toBe(T0);
  expect(gate.held).toBe(0);
  // The refused run did not take the slot: the next run gets it at T0 + 2000.
  clock.advance(2000);
  await bounded(run(gate, LARGE, upstream.attempt("a3")));
  expect(upstream.sent).toEqual([["a1", T0], ["a3", T0 + 2000]]);
});

test("a next slot exactly at the deadline is still waited for and sent", async () => {
  const clock = new Clock();
  const gate = gateOn(clock, parseRateLimitOptions({ requestsPerSecond: 0.5, maxWaitMs: 2000 }));
  const upstream = new Upstream(clock);
  await bounded(run(gate, LARGE, upstream.attempt("a1")));
  const record = createGateRecord();
  expect((await bounded(run(gate, LARGE, upstream.attempt("a2"), { record }))).status).toBe(200);
  expect(upstream.sent).toEqual([["a1", T0], ["a2", T0 + 2000]]);
  expect(record).toEqual({ queuedMs: 2000, rejections: 0, headers: {}, exhausted: false });
});

test("a run reaching the head after its deadline is refused although its slot has come", async () => {
  // Strict Requirement 2.5: a run that reaches the head late, behind a long
  // attempt, is refused even though its slot (T0 + 2000) has already passed.
  const clock = new Clock();
  const gate = gateOn(clock, parseRateLimitOptions({ requestsPerSecond: 0.5, maxWaitMs: 1000 }));
  const upstream = new Upstream(clock);
  const release = new Latch();
  const a1 = run(gate, LARGE, upstream.attempt("a1", { release }));
  const record = createGateRecord();
  const a2 = run(gate, LARGE, upstream.attempt("a2"), { record });
  await settle();
  clock.advance(3000); // a1 is in flight for 3 s
  release.open();
  const response = await bounded(a2);
  await bounded(a1);
  await expectSynthesized429(response, 2); // still the 2 s pacing interval
  expect(upstream.sent).toEqual([["a1", T0]]);
  expect(record).toEqual({ queuedMs: 3000, rejections: 0, headers: {}, exhausted: true });
  expect(gate.held).toBe(0);
});

test.each([[1000, true], [1001, false]] as const)(
  "an unpaced run reaching the head %p ms after arrival with a 1000 ms maximum wait is sent: %p", async (inFlight, sent) => {
    const clock = new Clock();
    const gate = gateOn(clock, parseRateLimitOptions({ maxWaitMs: 1000 }));
    const upstream = new Upstream(clock);
    const release = new Latch();
    const a1 = run(gate, LARGE, upstream.attempt("a1", { release }));
    const record = createGateRecord();
    const a2 = run(gate, LARGE, upstream.attempt("a2"), { record });
    await settle();
    clock.advance(inFlight);
    release.open();
    const response = await bounded(a2);
    await bounded(a1);
    expect(response.status).toBe(sent ? 200 : 429);
    if (!sent) await expectSynthesized429(response, 1);
    expect(record.exhausted).toBe(!sent);
    expect(upstream.labels()).toEqual(sent ? ["a1", "a2"] : ["a1"]);
    expect(gate.held).toBe(0);
  });

test("a signal aborted before entry throws cancelled without entering the gate", async () => {
  const clock = new Clock();
  const gate = gateOn(clock);
  const controller = new AbortController();
  controller.abort();
  const refused = counted();
  await expectCancelled(run(gate, LARGE, refused.attempt, { signal: controller.signal }));
  expect(refused.calls()).toBe(0);
  expect(gate.held).toBe(0);
  // The cancelled run took no slot: the next run on the key is sent at once.
  const upstream = new Upstream(clock);
  await bounded(run(gate, LARGE, upstream.attempt("a1")));
  expect(upstream.sent).toEqual([["a1", T0]]);
});

test("a signal aborted before entry is cancelled, not refused, when the gate is full", async () => {
  const clock = new Clock();
  const gate = gateOn(clock, parseRateLimitOptions({ maxQueue: 1 }));
  const upstream = new Upstream(clock);
  const release = new Latch();
  const a1 = run(gate, LARGE, upstream.attempt("a1", { release }));
  await settle();
  expect(gate.held).toBe(1);
  const controller = new AbortController();
  controller.abort();
  const record = createGateRecord();
  const refused = counted();
  await expectCancelled(run(gate, "b", refused.attempt, { record, signal: controller.signal }));
  expect(refused.calls()).toBe(0);
  expect(record).toEqual(createGateRecord());
  expect(gate.held).toBe(1);
  release.open();
  await bounded(a1);
  expect(gate.held).toBe(0);
});

test("aborting the head during its pacing wait sends nothing and the next run takes the slot", async () => {
  const clock = new Clock();
  const sleeps = new HeldSleep(clock);
  const gate = gateOn(clock, PACED, sleeps.sleep);
  const upstream = new Upstream(clock);
  await bounded(run(gate, LARGE, upstream.attempt("a1")));
  const controller = new AbortController();
  const a2 = handled(run(gate, LARGE, upstream.attempt("a2"), { signal: controller.signal }));
  const a3 = run(gate, LARGE, upstream.attempt("a3"));
  await settle();
  expect(sleeps.calls).toEqual([2000]); // a2 sleeps towards T0 + 2000, a3 queues
  expect(gate.held).toBe(2);
  controller.abort();
  await expectCancelled(a2);
  expect(gate.held).toBe(1);
  await settle();
  expect(sleeps.calls).toEqual([2000, 2000]); // a3 now waits for the slot a2 never used
  expect(sleeps.pending).toBe(1);
  sleeps.wake();
  await bounded(a3);
  expect(upstream.sent).toEqual([["a1", T0], ["a3", T0 + 2000]]);
  expect(gate.held).toBe(0);
});

test("aborting a queued run removes it and the next run proceeds", async () => {
  const clock = new Clock();
  const gate = gateOn(clock, UNPACED);
  const upstream = new Upstream(clock);
  const release = new Latch();
  const controller = new AbortController();
  const a1 = run(gate, LARGE, upstream.attempt("a1", { release }));
  const a2 = handled(run(gate, LARGE, upstream.attempt("a2"), { signal: controller.signal }));
  const a3 = run(gate, LARGE, upstream.attempt("a3"));
  await settle();
  expect(gate.held).toBe(3);
  controller.abort();
  await expectCancelled(a2);
  expect(gate.held).toBe(2);
  release.open();
  const responses = await bounded(Promise.all([a1, a3]));
  expect(responses.map(response => response.status)).toEqual([200, 200]);
  expect(upstream.labels()).toEqual(["a1", "a3"]);
  expect(gate.held).toBe(0);
});

test("aborting a run just promoted to the head sends nothing and wakes the next", async () => {
  const clock = new Clock();
  const gate = gateOn(clock, UNPACED);
  const upstream = new Upstream(clock);
  const release = new Latch();
  const controller = new AbortController();
  const a1 = run(gate, LARGE, upstream.attempt("a1", { release }));
  const a2 = handled(run(gate, LARGE, upstream.attempt("a2"), { signal: controller.signal }));
  const a3 = run(gate, LARGE, upstream.attempt("a3"));
  await settle();
  // a1 leaves and promotes a2; the abort lands before a2 runs on.
  const aborted = a1.then(() => controller.abort());
  release.open();
  await bounded(aborted);
  await expectCancelled(a2);
  await bounded(a3);
  expect(upstream.labels()).toEqual(["a1", "a3"]);
  expect(gate.held).toBe(0);
});

test("the head finishing while the next run is aborted returns normally", async () => {
  const clock = new Clock();
  const gate = gateOn(clock, UNPACED);
  const upstream = new Upstream(clock);
  const release = new Latch();
  const controller = new AbortController();
  const a1 = run(gate, LARGE, upstream.attempt("a1", { release }));
  const a2 = handled(run(gate, LARGE, upstream.attempt("a2"), { signal: controller.signal }));
  const a3 = run(gate, LARGE, upstream.attempt("a3"));
  await settle();
  release.open(); // a1 resumes and leaves ...
  controller.abort(); // ... while a2 is aborted, not yet cleaned up
  expect((await bounded(a1)).status).toBe(200);
  await expectCancelled(a2);
  expect((await bounded(a3)).status).toBe(200);
  expect(upstream.labels()).toEqual(["a1", "a3"]);
  expect(gate.held).toBe(0);
});

test("a served or aborted run removes every abort listener it added", async () => {
  const clock = new Clock();
  const gate = gateOn(clock, UNPACED);
  const upstream = new Upstream(clock);
  const release = new Latch();
  const served = new AbortController();
  const servedListeners = watchListeners(served.signal);
  const aborted = new AbortController();
  const abortedListeners = watchListeners(aborted.signal);
  const a1 = run(gate, LARGE, upstream.attempt("a1", { release }));
  const a2 = run(gate, LARGE, upstream.attempt("a2"), { signal: served.signal });
  const a3 = handled(run(gate, LARGE, upstream.attempt("a3"), { signal: aborted.signal }));
  await settle();
  // Waiting for its turn, a queued run listens for an abort.
  expect(servedListeners.added()).toBeGreaterThan(0);
  expect(abortedListeners.added()).toBeGreaterThan(0);
  aborted.abort();
  await expectCancelled(a3);
  release.open();
  await bounded(Promise.all([a1, a2]));
  expect(upstream.labels()).toEqual(["a1", "a2"]);
  expect(servedListeners.leaked()).toBe(0);
  expect(abortedListeners.leaked()).toBe(0);
  expect(gate.held).toBe(0);
});

// Each case is a one-element tuple: test.each spreads an array case into arguments.
test.each([[new TypeError("fetch failed")], [new BridgeError("auth")]] as const)(
  "an error from attempt (%p) propagates unchanged and releases the key", async error => {
    const clock = new Clock();
    const gate = gateOn(clock, UNPACED);
    const upstream = new Upstream(clock);
    const release = new Latch();
    const a1 = handled(run(gate, LARGE, upstream.attempt("a1", { release, error })));
    const a2 = run(gate, LARGE, upstream.attempt("a2"));
    await settle();
    release.open();
    expect(await bounded(a1).then(() => undefined, (caught: unknown) => caught)).toBe(error);
    expect((await bounded(a2)).status).toBe(200);
    expect(upstream.labels()).toEqual(["a1", "a2"]);
    expect(gate.held).toBe(0);
    expect((await bounded(run(gate, LARGE, upstream.attempt("a3")))).status).toBe(200);
  });

test.each([200, 201, 400, 401, 404, 500, 503])("a %p response is returned as the very same object, unread", async status => {
  const gate = gateOn(new Clock());
  const response = ok(status);
  const record = createGateRecord();
  expect(await bounded(run(gate, LARGE, async () => response, { record }))).toBe(response);
  expect(response.bodyUsed).toBe(false);
  expect(record).toEqual(createGateRecord());
});

test("an upstream 429 whose hold fits the deadline is not returned: the answer after it is", async () => {
  // Task 3.3 turned the 3.2 pass-through of an upstream 429 into hold and re-send.
  const gate = holdingGate(new Clock(), PACED, { random: never });
  const rejected = new Response('{"message":"Rate limit exceeded"}', { status: 429, headers: { "retry-after": "2" } });
  const accepted = ok();
  const answers = [rejected, accepted];
  const record = createGateRecord();
  expect(await bounded(run(gate, LARGE, async () => answers.shift() ?? never(), { record }))).toBe(accepted);
  expect(answers).toEqual([]);
  expect(rejected.bodyUsed).toBe(true); // cancelled, never returned
  expect(accepted.bodyUsed).toBe(false);
  expect(record).toEqual({ queuedMs: 2000, rejections: 1, headers: {}, exhausted: false });
  expect(gate.held).toBe(0);
});

test("a new key does not reset another key's pending slot", async () => {
  // Creating key "b" prunes idle keys. Key "a" has no waiter, but its next slot
  // (T0 + 2000) is still ahead, so it must keep it: a2 waits for that slot.
  const clock = new Clock();
  const gate = gateOn(clock);
  const upstream = new Upstream(clock);
  await bounded(run(gate, "a", upstream.attempt("a1")));
  clock.advance(500);
  await bounded(run(gate, "b", upstream.attempt("b1")));
  await bounded(run(gate, "a", upstream.attempt("a2")));
  expect(upstream.sent).toEqual([["a1", T0], ["b1", T0 + 500], ["a2", T0 + 2000]]);
});

test("a new key does not drop a key with an attempt in flight", async () => {
  // Key "a" is unpaced, so its slot has always passed, but a1 is still in
  // flight. Creating key "b" must not drop "a", or a2 would start beside a1.
  const clock = new Clock();
  const gate = gateOn(clock, UNPACED);
  const upstream = new Upstream(clock);
  const release = new Latch();
  const a1 = run(gate, "a", upstream.attempt("a1", { release }));
  await settle();
  await bounded(run(gate, "b", upstream.attempt("b1")));
  const a2 = run(gate, "a", upstream.attempt("a2"));
  await settle();
  expect(upstream.labels()).toEqual(["a1", "b1"]);
  release.open();
  await bounded(Promise.all([a1, a2]));
  expect(upstream.labels()).toEqual(["a1", "b1", "a2"]);
  expect(gate.held).toBe(0);
});

test("now() reads the injected clock, and performance.now() by default", () => {
  const clock = new Clock();
  const gate = gateOn(clock);
  expect(gate.now()).toBe(T0);
  clock.advance(5);
  expect(gate.now()).toBe(T0 + 5);
  const reading = spyOn(performance, "now").mockReturnValue(1234.5);
  try {
    expect(new UpstreamGate(UNPACED).now()).toBe(1234.5);
  } finally {
    reading.mockRestore();
  }
});

test("the gate paces on the real clock and timers by default", async () => {
  const gate = new UpstreamGate(parseRateLimitOptions({ requestsPerSecond: 50 })); // 20 ms apart
  const controller = new AbortController();
  const listeners = watchListeners(controller.signal);
  const sent: number[] = [];
  const attempt = async (): Promise<Response> => { sent.push(performance.now()); return ok(); };
  const responses = await bounded(Promise.all([0, 1].map(() => run(gate, LARGE, attempt, { signal: controller.signal }))));
  expect(responses.map(response => response.status)).toEqual([200, 200]);
  expect(sent[1]! - sent[0]!).toBeGreaterThanOrEqual(19);
  expect(listeners.added()).toBeGreaterThan(0);
  expect(listeners.leaked()).toBe(0);
  expect(gate.held).toBe(0);
});

test("an abort ends the default pacing sleep at once", async () => {
  const gate = new UpstreamGate(PACED);
  await bounded(run(gate, LARGE, async () => ok()));
  const controller = new AbortController();
  const listeners = watchListeners(controller.signal);
  const refused = counted();
  const started = performance.now();
  const waiting = handled(run(gate, LARGE, refused.attempt, { signal: controller.signal }));
  await tick();
  expect(listeners.added()).toBeGreaterThan(0); // the default sleep listens for the abort
  controller.abort();
  await expectCancelled(waiting);
  expect(performance.now() - started).toBeLessThan(1000); // not the 2000 ms pacing wait
  expect(refused.calls()).toBe(0);
  expect(listeners.leaked()).toBe(0);
  expect(gate.held).toBe(0);
});

test("createGateRecord returns fresh, empty records", () => {
  const first = createGateRecord();
  const second = createGateRecord();
  expect(first).toEqual({ queuedMs: 0, rejections: 0, headers: {}, exhausted: false });
  first.headers["x-ratelimit-remaining-req-minute"] = "0";
  first.queuedMs = 5;
  expect(second).toEqual({ queuedMs: 0, rejections: 0, headers: {}, exhausted: false });
});

// --- rate-limit holds (task 3.3; Requirements 1.3, 4.1, 4.2, 4.3, 5.1, 5.3) -------

const RATE_LIMITED_BODY = '{"object":"error","message":"Rate limit exceeded","type":"rate_limited"}';
const RATE_LIMIT_HEADERS: Readonly<Record<string, string>> = Object.freeze({
  "x-ratelimit-limit-req-minute": "5",
  "x-ratelimit-remaining-req-minute": "0",
});

/** A 429 body that records whether it was read or cancelled. The stream is pulled
 * only on demand (high-water mark 0), so `pulls` counts real reads. */
class TrackedBody {
  pulls = 0;
  cancelled = false;
  readonly stream: ReadableStream<Uint8Array>;

  constructor(text = RATE_LIMITED_BODY) {
    const bytes = new TextEncoder().encode(text);
    let sent = false;
    this.stream = new ReadableStream<Uint8Array>({
      pull: controller => {
        this.pulls += 1;
        if (sent) controller.close();
        else { sent = true; controller.enqueue(bytes); }
      },
      cancel: () => { this.cancelled = true; },
    }, { highWaterMark: 0 });
  }
}

/** A Mistral-shaped upstream 429 (mirrors _rejection in tests/test_ratelimit.py). */
function rejection(retryAfter?: string, { headers = {}, body = RATE_LIMITED_BODY }: { headers?: Readonly<Record<string, string>>; body?: BodyInit | null } = {}): Response {
  const values: Record<string, string> = { "content-type": "application/json" };
  if (retryAfter !== undefined) values["retry-after"] = retryAfter;
  return new Response(body, { status: 429, headers: { ...values, ...headers } });
}

/** Records every warn(key, waitMs) call the gate makes. */
class Warnings {
  readonly calls: [key: string, waitMs: number][] = [];
  readonly warn = (key: string, waitMs: number): void => {
    this.calls.push([key, waitMs]);
  };

  waits(): number[] {
    return this.calls.map(([, waitMs]) => waitMs);
  }
}

/** A gate for rejection tests. A draw of 0.5 makes every backoff exactly its base;
 * the wall clock is the fake clock, so HTTP-dates are read relative to it. */
function holdingGate(clock: Clock, options: RateLimitOptions = UNPACED, { sleep = clock.sleep, random = (): number => 0.5, warn = new Warnings().warn }: { sleep?: Sleep; random?: () => number; warn?: (key: string, waitMs: number) => void } = {}): UpstreamGate {
  return new UpstreamGate(options, { now: clock.now, wallNow: clock.now, sleep, random, warn });
}

function httpDate(epochMs: number): string {
  return new Date(epochMs).toUTCString();
}

test("observable: a 429 then a 200 completes with the 200 after two attempts", async () => {
  const clock = new Clock();
  const warnings = new Warnings();
  const gate = holdingGate(clock, UNPACED, { warn: warnings.warn });
  const upstream = new Upstream(clock);
  const body = new TrackedBody();
  const accepted = ok();
  const record = createGateRecord();
  const response = await bounded(run(gate, LARGE, upstream.script("a", [
    () => rejection(undefined, { headers: RATE_LIMIT_HEADERS, body: body.stream }),
    () => accepted,
  ]), { record }));
  expect(response).toBe(accepted);
  expect(response.bodyUsed).toBe(false);
  // Unpaced and without Retry-After, the first backoff is the 1000 ms floor.
  expect(upstream.sent).toEqual([["a", T0], ["a", T0 + 1000]]);
  expect(clock.sleeps).toEqual([1000]);
  // The retried 429 was cancelled, never read.
  expect(body.cancelled).toBe(true);
  expect(body.pulls).toBe(0);
  expect(record).toEqual({ queuedMs: 1000, rejections: 1, headers: { ...RATE_LIMIT_HEADERS }, exhausted: false });
  expect(record.queuedMs).toBeGreaterThan(0);
  expect(warnings.calls).toEqual([[LARGE, 1000]]);
  expect(gate.held).toBe(0);
});

const HOLD_ORDER: [name: string, options: RateLimitOptions, sent: [string, number][], queuedMs: number[]][] = [
  ["paced", PACED, [["a", T0], ["a", T0 + 5000], ["b", T0 + 7000], ["c", T0 + 9000]], [5000, 7000, 9000]],
  ["unpaced", UNPACED, [["a", T0], ["a", T0 + 5000], ["b", T0 + 5000], ["c", T0 + 5000]], [5000, 5000, 5000]],
];

test.each(HOLD_ORDER)("%s: the rejected run is sent again first and later runs wait for the hold", async (_name, options, sent, queued) => {
  const clock = new Clock();
  const warnings = new Warnings();
  const gate = holdingGate(clock, options, { random: never, warn: warnings.warn });
  const upstream = new Upstream(clock);
  const release = new Latch();
  const records = [createGateRecord(), createGateRecord(), createGateRecord()] as const;
  const runs = [
    run(gate, LARGE, upstream.script("a", [() => rejection("5"), () => ok()], { release }), { record: records[0] }),
    run(gate, LARGE, upstream.attempt("b"), { record: records[1] }),
    run(gate, LARGE, upstream.attempt("c"), { record: records[2] }),
  ];
  await settle(); // all three are in the gate at T0
  expect(upstream.labels()).toEqual(["a"]);
  release.open();
  const responses = await bounded(Promise.all(runs));
  expect(responses.map(response => response.status)).toEqual([200, 200, 200]);
  // a holds the key for 5 s and is sent again ahead of b and c, which follow at
  // the key's pace (unpaced: as soon as the run ahead has finished).
  expect(upstream.sent).toEqual(sent);
  expect(warnings.calls).toEqual([[LARGE, 5000]]);
  expect(records.map(record => record.rejections)).toEqual([1, 0, 0]);
  expect(records.map(record => record.queuedMs)).toEqual(queued);
  expect(gate.held).toBe(0);
});

test("a run arriving after an exhausted run still waits for the clamped hold", async () => {
  // The key is unpaced, so only its hold can delay a2. Creating key "b" in between
  // must not drop the held key either.
  const clock = new Clock();
  const warnings = new Warnings();
  const gate = holdingGate(clock, parseRateLimitOptions({ maxWaitMs: 5000 }), { random: never, warn: warnings.warn });
  const upstream = new Upstream(clock);
  const first = createGateRecord();
  // The 7 s Retry-After ends past the deadline T0 + 5000: the run gives up at once,
  // and the key is held for maxWaitMs.
  const refused = await bounded(run(gate, "a", upstream.script("a1", [() => rejection("7")]), { record: first }));
  expect(refused).toBe(upstream.returned[0]!);
  expect(first.exhausted).toBe(true);
  expect(warnings.calls).toEqual([["a", 5000]]);
  await bounded(run(gate, "b", upstream.attempt("b1")));
  const record = createGateRecord();
  expect((await bounded(run(gate, "a", upstream.attempt("a2"), { record }))).status).toBe(200);
  expect(upstream.sent).toEqual([["a1", T0], ["b1", T0], ["a2", T0 + 5000]]);
  expect(record).toEqual({ queuedMs: 5000, rejections: 0, headers: {}, exhausted: false });
});

test("a run queued behind an exhausted one still waits for the hold", async () => {
  // The rejected run gives up at once, but its hold (clamped to maxWaitMs) stays on
  // the key: the next run is not sent before it ends (Requirement 2.2).
  const clock = new Clock();
  const gate = holdingGate(clock, UNPACED, { random: never });
  const upstream = new Upstream(clock);
  const release = new Latch();
  const a = run(gate, LARGE, upstream.script("a", [() => rejection("300")], { release }));
  const record = createGateRecord();
  const b = run(gate, LARGE, upstream.attempt("b"), { record });
  await settle();
  release.open();
  const [exhausted, accepted] = await bounded(Promise.all([a, b]));
  expect(exhausted).toBe(upstream.returned[0]!);
  expect(exhausted.headers.get("retry-after")).toBe("300");
  expect(accepted.status).toBe(200);
  expect(upstream.sent).toEqual([["a", T0], ["b", T0 + 120_000]]);
  expect(clock.sleeps).toEqual([120_000]);
  expect(record).toEqual({ queuedMs: 120_000, rejections: 0, headers: {}, exhausted: false });
  expect(gate.held).toBe(0);
});

test.each([["paced", PACED], ["unpaced", UNPACED]] as const)("%s: Retry-After seconds set the hold", async (_name, options) => {
  const clock = new Clock();
  const warnings = new Warnings();
  const gate = holdingGate(clock, options, { random: never, warn: warnings.warn });
  const upstream = new Upstream(clock);
  const record = createGateRecord();
  const response = await bounded(run(gate, LARGE, upstream.script("a", [() => rejection("7"), () => ok()]), { record }));
  expect(response.status).toBe(200);
  expect(upstream.times()).toEqual([T0, T0 + 7000]);
  expect(warnings.calls).toEqual([[LARGE, 7000]]);
  expect(record.queuedMs).toBe(7000);
});

test("a Retry-After HTTP-date sets the hold from the wall clock", async () => {
  const clock = new Clock();
  const warnings = new Warnings();
  const gate = holdingGate(clock, UNPACED, { random: never, warn: warnings.warn });
  const upstream = new Upstream(clock);
  await bounded(run(gate, LARGE, upstream.script("a", [() => rejection(httpDate(T0 + 10_000)), () => ok()])));
  expect(upstream.times()).toEqual([T0, T0 + 10_000]);
  expect(warnings.calls).toEqual([[LARGE, 10_000]]);
});

test("the wall clock for an HTTP-date is Date.now() by default, not the gate clock", async () => {
  const wall = 1_800_000_000_000;
  const reading = spyOn(Date, "now").mockReturnValue(wall);
  try {
    const clock = new Clock(); // T0, far from the wall clock
    const warnings = new Warnings();
    const gate = new UpstreamGate(UNPACED, { now: clock.now, sleep: clock.sleep, random: never, warn: warnings.warn });
    const upstream = new Upstream(clock);
    const response = await bounded(run(gate, LARGE, upstream.script("a", [() => rejection(httpDate(wall + 10_000)), () => ok()])));
    expect(response.status).toBe(200);
    expect(upstream.times()).toEqual([T0, T0 + 10_000]);
    expect(warnings.calls).toEqual([[LARGE, 10_000]]);
  } finally {
    reading.mockRestore();
  }
});

test.each([
  ["0", "paced", 2000, PACED],
  ["0", "unpaced", 1000, UNPACED],
  ["a past HTTP-date", "paced", 2000, PACED],
  ["a past HTTP-date", "unpaced", 1000, UNPACED],
] as const)("a Retry-After of %s (%s) still holds for %p ms", async (value, _name, floor, options) => {
  const clock = new Clock();
  const warnings = new Warnings();
  const gate = holdingGate(clock, options, { random: never, warn: warnings.warn });
  const upstream = new Upstream(clock);
  const retryAfter = value === "0" ? "0" : httpDate(T0 - 60_000);
  await bounded(run(gate, LARGE, upstream.script("a", [() => rejection(retryAfter), () => ok()])));
  // Never back to back: the hold is the pacing interval, or 1000 ms when unpaced.
  expect(upstream.times()).toEqual([T0, T0 + floor]);
  expect(warnings.calls).toEqual([[LARGE, floor]]);
});

test("without Retry-After each rejection of the run doubles the backoff", async () => {
  const clock = new Clock();
  const warnings = new Warnings();
  const gate = holdingGate(clock, UNPACED, { warn: warnings.warn });
  const upstream = new Upstream(clock);
  const record = createGateRecord();
  const response = await bounded(run(gate, LARGE, upstream.script("a", [rejection, rejection, rejection, () => ok()]), { record }));
  expect(response.status).toBe(200);
  // Unpaced, the backoff starts at 1000 ms and doubles; one warning per hold.
  expect(warnings.calls).toEqual([[LARGE, 1000], [LARGE, 2000], [LARGE, 4000]]);
  expect(upstream.times()).toEqual([T0, T0 + 1000, T0 + 3000, T0 + 7000]);
  expect(record.rejections).toBe(3);
  expect(record.queuedMs).toBe(7000);
});

test("queuedMs counts every hold once and never the time an attempt is in flight", async () => {
  const clock = new Clock();
  const gate = holdingGate(clock);
  const upstream = new Upstream(clock);
  const record = createGateRecord();
  const response = await bounded(run(gate, LARGE, upstream.script("a", [rejection, rejection, () => ok()], { latencyMs: 250 }), { record }));
  expect(response.status).toBe(200);
  // Each answer takes 250 ms; the holds of 1000 and 2000 ms start when it arrives.
  expect(upstream.times()).toEqual([T0, T0 + 1250, T0 + 3500]);
  expect(clock.sleeps).toEqual([1000, 2000]);
  expect(record.queuedMs).toBe(3000);
});

test("a paced backoff starts at the pacing interval and stops at the cap", async () => {
  const clock = new Clock();
  const warnings = new Warnings();
  const gate = holdingGate(clock, PACED, { warn: warnings.warn }); // backoff cap 30 000 ms
  const upstream = new Upstream(clock);
  await bounded(run(gate, LARGE, upstream.script("a", [...Array.from({ length: 6 }, () => rejection), () => ok()])));
  expect(warnings.waits()).toEqual([2000, 4000, 8000, 16_000, 30_000, 30_000]);
  expect(upstream.times()).toEqual([T0, T0 + 2000, T0 + 6000, T0 + 14_000, T0 + 30_000, T0 + 60_000, T0 + 90_000]);
});

test.each([
  [0, [1000, 1000, 1500, 1500, 1500]],
  [0.999999, [1499.999, 2999.998, 3000, 3000, 3000]],
] as const)("a jittered backoff with draw %p is positive and never beyond the cap", async (draw, waits) => {
  const clock = new Clock();
  const warnings = new Warnings();
  const gate = holdingGate(clock, parseRateLimitOptions({ backoffCapMs: 3000 }), { random: () => draw, warn: warnings.warn });
  const upstream = new Upstream(clock);
  const response = await bounded(run(gate, LARGE, upstream.script("a", [...Array.from({ length: 5 }, () => rejection), () => ok()])));
  expect(response.status).toBe(200);
  expect(warnings.waits()).toHaveLength(waits.length);
  warnings.waits().forEach((wait, index) => expect(wait).toBeCloseTo(waits[index]!, 6));
  for (const wait of warnings.waits()) {
    expect(wait).toBeGreaterThan(0);
    expect(wait).toBeLessThanOrEqual(3000);
  }
});

test("the backoff honours a cap below one second", async () => {
  const clock = new Clock();
  const warnings = new Warnings();
  const gate = holdingGate(clock, parseRateLimitOptions({ backoffCapMs: 250 }), { warn: warnings.warn });
  const upstream = new Upstream(clock);
  await bounded(run(gate, LARGE, upstream.script("a", [rejection, rejection, () => ok()])));
  expect(warnings.waits()).toEqual([250, 250]);
  expect(upstream.times()).toEqual([T0, T0 + 250, T0 + 500]);
});

test("a long run of rejections keeps the backoff finite", async () => {
  // 1100 one-second holds fit in the maximum wait; the doubling must stay bounded.
  const clock = new Clock();
  const warnings = new Warnings();
  const gate = holdingGate(clock, parseRateLimitOptions({ maxWaitMs: 2_000_000 }), { random: never, warn: warnings.warn });
  const upstream = new Upstream(clock);
  const record = createGateRecord();
  const response = await bounded(run(gate, LARGE, upstream.script("a", [...Array.from({ length: 1100 }, () => () => rejection("1")), () => ok()]), { record }));
  expect(response.status).toBe(200);
  expect(record.rejections).toBe(1100);
  expect(new Set(warnings.waits())).toEqual(new Set([1000]));
});

test("x-ratelimit-* headers from every rejection are merged into the record, the latest value winning", async () => {
  const clock = new Clock();
  const gate = holdingGate(clock);
  const record = createGateRecord();
  const upstream = new Upstream(clock);
  await bounded(run(gate, LARGE, upstream.script("a", [
    () => rejection(undefined, { headers: { "x-ratelimit-remaining-req-minute": "1", "x-ratelimit-limit-req-minute": "5" } }),
    () => rejection(undefined, { headers: { "x-ratelimit-remaining-req-minute": "0" } }),
    () => ok(),
  ]), { record }));
  expect(record.rejections).toBe(2);
  expect(record.headers).toEqual({ "x-ratelimit-limit-req-minute": "5", "x-ratelimit-remaining-req-minute": "0" });
});

test("observable: exhaustion returns the very same last 429 object, unread and with its headers untouched", async () => {
  const clock = new Clock();
  const warnings = new Warnings();
  const gate = holdingGate(clock, parseRateLimitOptions({ maxWaitMs: 1000 }), { random: never, warn: warnings.warn });
  const upstream = new Upstream(clock);
  const body = new TrackedBody();
  const answer = rejection("30", { headers: RATE_LIMIT_HEADERS, body: body.stream });
  const headersBefore = [...answer.headers];
  const record = createGateRecord();
  const response = await bounded(run(gate, LARGE, upstream.script("a", [() => answer], { latencyMs: 250 }), { record }));
  // The 30 s Retry-After would end at T0 + 30 250, past the deadline T0 + 1000: the
  // gate gives up without a further attempt. The key is held for maxWaitMs.
  expect(upstream.sent).toEqual([["a", T0]]);
  expect(response).toBe(answer);
  expect(response.bodyUsed).toBe(false);
  expect(body.pulls).toBe(0);
  expect(body.cancelled).toBe(false);
  expect(response.status).toBe(429);
  expect([...response.headers]).toEqual(headersBefore);
  expect(response.headers.get("retry-after")).toBe("30");
  expect(response.headers.has("x-should-retry")).toBe(false); // the provider adds that, not the gate
  expect(record).toEqual({ queuedMs: 0, rejections: 1, headers: { ...RATE_LIMIT_HEADERS }, exhausted: true });
  expect(warnings.calls).toEqual([[LARGE, 1000]]);
  expect(gate.held).toBe(0);
  // The host still receives the body intact.
  expect(await response.text()).toBe(RATE_LIMITED_BODY);
});

test.each([
  ["300", 120_000, 120_000],
  ["5", 1000, 1000],
  [undefined, 500, 500],
] as const)("a wait beyond the deadline (Retry-After %p, maxWaitMs %p) is answered at once after one send", async (retryAfter, maxWaitMs, hold) => {
  // The upstream answers in no clock time. The wait it asks for ends past the
  // deadline, so the run gives up at once; the key's hold, clamped to maxWaitMs,
  // would end exactly on the deadline, but no re-send happens there.
  const clock = new Clock();
  const warnings = new Warnings();
  const gate = holdingGate(clock, parseRateLimitOptions({ maxWaitMs }), { warn: warnings.warn });
  const upstream = new Upstream(clock);
  const record = createGateRecord();
  const response = await bounded(run(gate, LARGE, upstream.script("a", [() => rejection(retryAfter, { headers: RATE_LIMIT_HEADERS })]), { record }));
  expect(upstream.sent).toEqual([["a", T0]]);
  expect(clock.sleeps).toEqual([]);
  expect(clock.now()).toBe(T0);
  expect(response).toBe(upstream.returned[0]!);
  expect(response.bodyUsed).toBe(false);
  expect(await response.text()).toBe(RATE_LIMITED_BODY);
  expect(record).toEqual({ queuedMs: 0, rejections: 1, headers: { ...RATE_LIMIT_HEADERS }, exhausted: true });
  expect(warnings.calls).toEqual([[LARGE, hold]]);
  expect(gate.held).toBe(0);
});

test.each([["backoff", undefined], ["retry-after", "1"]] as const)(
  "%s: a hold ending exactly at the deadline is still sent again", async (_name, retryAfter) => {
    // Requirement 2.5 gives up on a request held *longer* than maxWaitMs. A 1000 ms
    // hold against a 1000 ms maximum wait ends on the deadline, so the run is sent
    // once more; it gives up only when the next hold would end past the deadline.
    const clock = new Clock();
    const warnings = new Warnings();
    const gate = holdingGate(clock, parseRateLimitOptions({ maxWaitMs: 1000 }), { warn: warnings.warn });
    const upstream = new Upstream(clock);
    const record = createGateRecord();
    const response = await bounded(run(gate, LARGE, upstream.script("a", [() => rejection(retryAfter)]), { record }));
    expect(upstream.times()).toEqual([T0, T0 + 1000]);
    expect(upstream.returned).toHaveLength(2);
    expect(response).toBe(upstream.returned[1]!);
    expect(upstream.returned[0]!.bodyUsed).toBe(true); // the retried 429 was cancelled
    expect(response.bodyUsed).toBe(false);
    expect(record).toEqual({ queuedMs: 1000, rejections: 2, headers: {}, exhausted: true });
    expect(warnings.waits()).toEqual([1000, 1000]);
  });

test("a backoff capped below the pacing interval returns the last 429 when the next slot lies past the deadline", async () => {
  // The 1000 ms backoff would fit the deadline (T0 + 1500), but the key's next
  // pacing slot is T0 + 2000: no re-send can happen in time, so the run returns
  // the 429 it holds, unread, instead of cancelling it first.
  const clock = new Clock();
  const warnings = new Warnings();
  const gate = holdingGate(clock, parseRateLimitOptions({ requestsPerSecond: 0.5, maxWaitMs: 1500, backoffCapMs: 1000 }), { warn: warnings.warn });
  const upstream = new Upstream(clock);
  const body = new TrackedBody();
  const record = createGateRecord();
  const response = await bounded(run(gate, LARGE, upstream.script("a", [() => rejection(undefined, { body: body.stream })]), { record }));
  expect(response).toBe(upstream.returned[0]!);
  expect(response.bodyUsed).toBe(false);
  expect(body.cancelled).toBe(false);
  expect(upstream.sent).toEqual([["a", T0]]);
  expect(clock.sleeps).toEqual([]);
  expect(record).toEqual({ queuedMs: 0, rejections: 1, headers: {}, exhausted: true });
  expect(warnings.calls).toEqual([[LARGE, 1000]]);
});

test("a backoff capped below the pacing interval re-sends at the next slot when it fits", async () => {
  const clock = new Clock();
  const warnings = new Warnings();
  const gate = holdingGate(clock, parseRateLimitOptions({ requestsPerSecond: 0.5, maxWaitMs: 2000, backoffCapMs: 1000 }), { warn: warnings.warn });
  const upstream = new Upstream(clock);
  const record = createGateRecord();
  const response = await bounded(run(gate, LARGE, upstream.script("a", [rejection, () => ok()]), { record }));
  expect(response.status).toBe(200);
  expect(upstream.times()).toEqual([T0, T0 + 2000]); // the slot, not the 1000 ms hold, decides
  expect(record).toEqual({ queuedMs: 2000, rejections: 1, headers: {}, exhausted: false });
  expect(warnings.calls).toEqual([[LARGE, 1000]]);
});

test("a run whose deadline passed behind a hold is refused at the head", async () => {
  const clock = new Clock();
  const gate = holdingGate(clock, parseRateLimitOptions({ maxWaitMs: 6000 }), { random: never });
  const upstream = new Upstream(clock);
  const release = new Latch();
  const slowAcceptance = (): Response => {
    clock.advance(2000); // the re-send takes 2 s
    return ok();
  };
  const a = run(gate, LARGE, upstream.script("a", [() => rejection("5"), slowAcceptance], { release }));
  const late = createGateRecord();
  const b = run(gate, LARGE, upstream.attempt("b"), { record: late });
  await settle(); // both are in the gate at T0
  release.open();
  const [first, refused] = await bounded(Promise.all([a, b]));
  // b reaches the head at T0 + 7000, past its deadline T0 + 6000: it is refused
  // and never sent.
  expect(first.status).toBe(200);
  await expectSynthesized429(refused, 1);
  expect(late).toEqual({ queuedMs: 7000, rejections: 0, headers: {}, exhausted: true });
  expect(upstream.sent).toEqual([["a", T0], ["a", T0 + 5000]]);
  expect(gate.held).toBe(0);
});

test("aborting a run during its hold sends nothing more, and the next run still waits for the hold", async () => {
  const clock = new Clock();
  const sleeps = new HeldSleep(clock);
  const gate = holdingGate(clock, UNPACED, { sleep: sleeps.sleep, random: never });
  const upstream = new Upstream(clock);
  const controller = new AbortController();
  const listeners = watchListeners(controller.signal);
  const body = new TrackedBody();
  const record = createGateRecord();
  const a = handled(run(gate, LARGE, upstream.script("a", [() => rejection("5", { body: body.stream }), () => ok()]), { record, signal: controller.signal }));
  const b = run(gate, LARGE, upstream.attempt("b"));
  await settle();
  expect(sleeps.calls).toEqual([5000]); // a sleeps through its hold
  expect(body.cancelled).toBe(true);
  expect(gate.held).toBe(2);
  controller.abort();
  await expectCancelled(a);
  expect(upstream.labels()).toEqual(["a"]); // attempt is not called again
  await settle();
  // b leads the line now, but the key is still held: b sleeps the same 5000 ms.
  expect(sleeps.calls).toEqual([5000, 5000]);
  expect(gate.held).toBe(1);
  sleeps.wake();
  expect((await bounded(b)).status).toBe(200);
  expect(upstream.sent).toEqual([["a", T0], ["b", T0 + 5000]]);
  expect(record).toMatchObject({ rejections: 1, exhausted: false });
  expect(listeners.added()).toBeGreaterThan(0);
  expect(listeners.leaked()).toBe(0);
  expect(gate.held).toBe(0);
});

test("an abort during a hold clears the default sleep timer at once", async () => {
  const gate = new UpstreamGate(UNPACED, { random: never });
  const controller = new AbortController();
  const listeners = watchListeners(controller.signal);
  const scheduled = spyOn(globalThis, "setTimeout");
  const cleared = spyOn(globalThis, "clearTimeout");
  try {
    let calls = 0;
    const started = performance.now();
    const waiting = handled(run(gate, LARGE, async () => { calls += 1; return rejection("5"); }, { signal: controller.signal }));
    await settle();
    // The hold timer covers the remaining ~5000 ms of the Retry-After.
    const hold = scheduled.mock.calls.findIndex(call => typeof call[1] === "number" && call[1] > 4000 && call[1] <= 5000);
    expect(hold).toBeGreaterThanOrEqual(0);
    controller.abort();
    expect(cleared).toHaveBeenCalledWith(scheduled.mock.results[hold]!.value);
    await expectCancelled(waiting);
    expect(performance.now() - started).toBeLessThan(1000);
    expect(calls).toBe(1);
    expect(listeners.leaked()).toBe(0);
    expect(gate.held).toBe(0);
  } finally {
    scheduled.mockRestore();
    cleared.mockRestore();
  }
});

test("an injected sleep that rejects with an AbortError on abort surfaces as BridgeError(cancelled)", async () => {
  const clock = new Clock();
  const sleep: Sleep = (_ms, signal) => new Promise((_, reject) => {
    signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
  });
  const gate = holdingGate(clock, UNPACED, { sleep, random: never });
  const upstream = new Upstream(clock);
  const controller = new AbortController();
  const waiting = handled(run(gate, LARGE, upstream.script("a", [() => rejection("5"), () => ok()]), { signal: controller.signal }));
  await settle();
  expect(gate.held).toBe(1);
  controller.abort();
  await expectCancelled(waiting);
  expect(upstream.labels()).toEqual(["a"]);
  expect(gate.held).toBe(0);
});

test("an error from the re-send propagates and releases the key", async () => {
  const clock = new Clock();
  const gate = holdingGate(clock, UNPACED, { random: never });
  const upstream = new Upstream(clock);
  const error = new BridgeError("auth");
  const a = handled(run(gate, LARGE, upstream.script("a", [() => rejection("3"), error])));
  const b = run(gate, LARGE, upstream.attempt("b"));
  expect(await bounded(a).then(() => undefined, (caught: unknown) => caught)).toBe(error);
  expect((await bounded(b)).status).toBe(200);
  expect(upstream.sent).toEqual([["a", T0], ["a", T0 + 3000], ["b", T0 + 3000]]);
  expect(gate.held).toBe(0);
});

test("a hold on one key does not hold another", async () => {
  const clock = new Clock();
  const sleeps = new HeldSleep(clock);
  const gate = holdingGate(clock, UNPACED, { sleep: sleeps.sleep, random: never });
  const upstream = new Upstream(clock);
  const a = run(gate, "a", upstream.script("a", [() => rejection("5"), () => ok()]));
  await settle();
  expect(sleeps.calls).toEqual([5000]);
  const record = createGateRecord();
  expect((await bounded(run(gate, "b", upstream.attempt("b"), { record }))).status).toBe(200);
  expect(record).toEqual(createGateRecord());
  sleeps.wake();
  expect((await bounded(a)).status).toBe(200);
  expect(upstream.sent).toEqual([["a", T0], ["b", T0], ["a", T0 + 5000]]);
  expect(gate.held).toBe(0);
});

test("a queue-full refusal on a held key hints the remaining hold", async () => {
  const clock = new Clock();
  const sleeps = new HeldSleep(clock);
  const gate = holdingGate(clock, parseRateLimitOptions({ maxQueue: 1 }), { sleep: sleeps.sleep, random: never });
  const upstream = new Upstream(clock);
  const a = run(gate, LARGE, upstream.script("a", [() => rejection("5"), () => ok()]));
  await settle();
  expect(sleeps.calls).toEqual([5000]); // a is held in the gate
  const record = createGateRecord();
  const refused = counted();
  await expectSynthesized429(await bounded(run(gate, LARGE, refused.attempt, { record })), 5);
  expect(record).toEqual({ queuedMs: 0, rejections: 0, headers: {}, exhausted: true });
  sleeps.wake();
  expect((await bounded(a)).status).toBe(200);
  expect(refused.calls()).toBe(0);
  expect(gate.held).toBe(0);
});

test("a gate without a warn callback still holds", async () => {
  const clock = new Clock();
  const gate = new UpstreamGate(UNPACED, { now: clock.now, sleep: clock.sleep, random: () => 0.5 });
  const upstream = new Upstream(clock);
  const response = await bounded(run(gate, LARGE, upstream.script("a", [rejection, () => ok()])));
  expect(response.status).toBe(200);
  expect(upstream.times()).toEqual([T0, T0 + 1000]);
});

test.each([
  ["locked", (): Response => { const answer = rejection(); answer.body!.getReader(); return answer; }],
  ["bodiless", (): Response => rejection(undefined, { body: null })],
] as const)("a %s 429 that cannot be cancelled is still held and sent again", async (_name, make) => {
  const clock = new Clock();
  const gate = holdingGate(clock);
  const upstream = new Upstream(clock);
  const response = await bounded(run(gate, LARGE, upstream.script("a", [make, () => ok()])));
  await settle(); // a rejected cancel() would surface as an unhandled rejection here
  expect(response.status).toBe(200);
  expect(upstream.times()).toEqual([T0, T0 + 1000]);
  expect(gate.held).toBe(0);
});
