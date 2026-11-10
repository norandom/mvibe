/** Upstream rate-limit policy for the plugin (mirrors mvibe_proxy/ratelimit.py).
 *
 * Mistral rejects a request that exceeds a workspace limit with HTTP 429 before
 * any inference runs. parseRateLimitOptions validates the `rateLimit` provider
 * option once at setup, so the rest of the rate-limit path can trust the values;
 * an invalid field fails setup with BridgeError("setup") naming only the field
 * (`rateLimit.<field>`), never a configured value. Pacing is off unless
 * `requestsPerSecond` or `modelRequestsPerSecond` is configured; no
 * account-specific rate is built in. classifyRateLimit recognizes the rejection
 * by its status alone and turns it into a RateLimitSignal: the wait from
 * Retry-After (parseRetryAfter) or a capped fallback, plus a copy of the
 * x-ratelimit-* headers that is safe to record. UpstreamGate schedules the
 * outbound attempts per key: arrival order, optional pacing, rate-limit holds that
 * send a rejected request again, and a bounded wait and depth, with an AbortSignal
 * cancelling a waiting request. */
import { BridgeError } from "./bridge.js";

export interface RateLimitOptions {
  /** Default pacing rate in requests per second; absent means no pacing. */
  readonly requestsPerSecond?: number;
  /** Per-model pacing rates; a model listed here ignores the default rate. */
  readonly modelRequestsPerSecond: Readonly<Record<string, number>>;
  readonly maxWaitMs: number;
  /** Bound on the requests held across all keys (an integer). */
  readonly maxQueue: number;
  /** Ceiling for a single backoff wait when the 429 carries no usable Retry-After. */
  readonly backoffCapMs: number;
}

const DEFAULT_MAX_WAIT_MS = 120_000;
const DEFAULT_MAX_QUEUE = 64;
const DEFAULT_BACKOFF_CAP_MS = 30_000;
/** Gate key for model-list discovery: never proactively paced (Requirement 3.5). */
export const MODELS_KEY = "models";
// A model name is echoed in a setup error only when it is short and printable;
// otherwise the entry is named by its position, as the proxy does.
const NAMEABLE_MODEL = /^[A-Za-z0-9._:/@+-]{1,128}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isPositiveNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

function invalid(field: string, kind = "a positive number"): BridgeError {
  return new BridgeError("setup", `Configure rateLimit.${field} as ${kind}.`);
}

/** The own property `field` of `raw`; inherited properties are not options. */
function own(raw: Record<string, unknown>, field: string): unknown {
  return Object.hasOwn(raw, field) ? raw[field] : undefined;
}

function positiveNumber(raw: Record<string, unknown>, field: string, fallback: number): number {
  const value = own(raw, field);
  if (value === undefined) return fallback;
  if (!isPositiveNumber(value)) throw invalid(field);
  return value;
}

function positiveInteger(raw: Record<string, unknown>, field: string, fallback: number): number {
  const value = own(raw, field);
  if (value === undefined) return fallback;
  if (!isPositiveNumber(value) || !Number.isInteger(value)) throw invalid(field, "a positive integer");
  return value;
}

function modelRates(raw: Record<string, unknown>): Readonly<Record<string, number>> {
  const value = own(raw, "modelRequestsPerSecond");
  if (value === undefined) return Object.freeze({});
  if (!isRecord(value)) throw invalid("modelRequestsPerSecond", "an object of model names to positive numbers");
  const rates: [string, number][] = [];
  for (const [position, [model, rate]] of Object.entries(value).entries()) {
    if (!model.trim()) throw new BridgeError("setup", "Configure rateLimit.modelRequestsPerSecond with non-empty model names.");
    if (!isPositiveNumber(rate)) {
      if (NAMEABLE_MODEL.test(model)) throw invalid(`modelRequestsPerSecond.${model}`);
      throw new BridgeError("setup", `Configure rateLimit.modelRequestsPerSecond entry ${position + 1} as a positive number.`);
    }
    rates.push([model, rate]);
  }
  // fromEntries defines own properties, so even a model named "__proto__" stays an ordinary entry.
  return Object.freeze(Object.fromEntries(rates));
}

/** Validate the `rateLimit` provider option; `undefined` or `null` yields the defaults.
 * Unknown keys are ignored. Throws BridgeError("setup") naming the invalid field. */
export function parseRateLimitOptions(raw: unknown): RateLimitOptions {
  if (raw === undefined || raw === null) raw = {};
  if (!isRecord(raw)) throw new BridgeError("setup", "Configure rateLimit as an object.");
  const rate = own(raw, "requestsPerSecond");
  if (rate !== undefined && !isPositiveNumber(rate)) throw invalid("requestsPerSecond");
  return Object.freeze({
    ...(rate === undefined ? {} : { requestsPerSecond: rate }),
    modelRequestsPerSecond: modelRates(raw),
    maxWaitMs: positiveNumber(raw, "maxWaitMs", DEFAULT_MAX_WAIT_MS),
    maxQueue: positiveInteger(raw, "maxQueue", DEFAULT_MAX_QUEUE),
    backoffCapMs: positiveNumber(raw, "backoffCapMs", DEFAULT_BACKOFF_CAP_MS),
  });
}

/** The pacing rate for gate `key`, or `undefined` when the key is not paced.
 * A model override wins over the default rate; `models` (discovery) is never paced. */
export function rateFor(options: RateLimitOptions, key: string): number | undefined {
  if (key === MODELS_KEY) return undefined;
  return Object.hasOwn(options.modelRequestsPerSecond, key) ? options.modelRequestsPerSecond[key] : options.requestsPerSecond;
}

/** A classified upstream 429. `headers` holds only the upstream `x-ratelimit-*`
 * headers, with lower-cased names and values cut to 64 characters, so the signal
 * never carries credentials or body content and is safe to record. */
export interface RateLimitSignal {
  /** Wait in milliseconds for this rejection. */
  readonly waitMs: number;
  readonly retryAfterPresent: boolean;
  readonly headers: Readonly<Record<string, string>>;
}

const RATE_LIMIT_STATUS = 429;
const SNAPSHOT_PREFIX = "x-ratelimit-";
const SNAPSHOT_VALUE_CHARS = 64;
// delay-seconds is 1*DIGIT (RFC 9110 section 10.2.3); a decimal fraction is
// tolerated. ASCII digits only, no sign, exponent, separator or Infinity/NaN.
const DELAY_SECONDS = /^[0-9]+(?:\.[0-9]+)?$/;

const MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];
// RFC 5322 section 4.3 zones, as Python's email.utils knows them (hours from UTC).
const ZONES: Readonly<Record<string, number>> = Object.freeze({ GMT: 0, UT: 0, UTC: 0, Z: 0, AST: -4, ADT: -3, EST: -5, EDT: -4, CST: -6, CDT: -5, MST: -7, MDT: -6, PST: -8, PDT: -7 });
const DAY = "(?:mon|tue|wed|thu|fri|sat|sun|monday|tuesday|wednesday|thursday|friday|saturday|sunday)";
const MONTH = `(${MONTHS.join("|")})`;
const TIME = "(\\d{1,2}):(\\d{2})(?::(\\d{2}))?";
const ZONE = `(${Object.keys(ZONES).join("|")}|[+-]\\d{4})`;
// IMF-fixdate (`Sun, 09 Nov 2025 12:00:10 GMT`, zone optional) and RFC 850
// (`Sunday, 09-Nov-25 12:00:10 GMT`, zone required, as in email.utils).
const IMF_DATE = new RegExp(`^(?:${DAY},\\s*)?(\\d{1,2})\\s+${MONTH}\\s+(\\d{4}|\\d{2})\\s+${TIME}(?:\\s+${ZONE})?$`, "i");
const RFC850_DATE = new RegExp(`^(?:${DAY},\\s*)?(\\d{1,2})-${MONTH}-(\\d{4}|\\d{2})\\s+${TIME}\\s+${ZONE}$`, "i");
// asctime (`Sun Nov  9 12:00:10 2025`): no zone means UTC.
const ASCTIME_DATE = new RegExp(`^(?:${DAY}\\s+)?${MONTH}\\s+(\\d{1,2})\\s+${TIME}\\s+(\\d{4}|\\d{2})(?:\\s+${ZONE})?$`, "i");

/** Minutes east of UTC for a zone token; `undefined` (naive) means UTC. */
function zoneOffsetMinutes(zone: string | undefined): number | undefined {
  if (zone === undefined) return 0;
  const named = ZONES[zone.toUpperCase()];
  if (named !== undefined) return named * 60;
  const hours = Number(zone.slice(1, 3));
  const minutes = Number(zone.slice(3, 5));
  if (hours > 23 || minutes > 59) return undefined;
  return (zone.startsWith("-") ? -1 : 1) * (hours * 60 + minutes);
}

/** Epoch milliseconds of an HTTP-date in any form RFC 9110 names, or `undefined`.
 * A strict grammar replaces Date.parse, which is implementation-defined, accepts
 * non-HTTP forms and reads a zoneless asctime date as local time. */
function httpDateMs(text: string): number | undefined {
  let parts: [day: string, month: string, year: string, hour: string, minute: string, second: string | undefined, zone: string | undefined];
  const imf = IMF_DATE.exec(text) ?? RFC850_DATE.exec(text);
  const asctime = imf ? null : ASCTIME_DATE.exec(text);
  if (imf) parts = [imf[1]!, imf[2]!, imf[3]!, imf[4]!, imf[5]!, imf[6], imf[7]];
  else if (asctime) parts = [asctime[2]!, asctime[1]!, asctime[6]!, asctime[3]!, asctime[4]!, asctime[5], asctime[7]];
  else return undefined;
  const [dayText, monthText, yearText, hourText, minuteText, secondText, zone] = parts;
  const day = Number(dayText);
  const month = MONTHS.indexOf(monthText.toLowerCase());
  let year = Number(yearText);
  const hour = Number(hourText);
  const minute = Number(minuteText);
  const second = secondText === undefined ? 0 : Number(secondText);
  // A year below 100 follows the RFC 822 rule email.utils applies: 69-99 -> 19xx, 0-68 -> 20xx.
  if (year < 100) year += year > 68 ? 1900 : 2000;
  if (hour > 23 || minute > 59 || second > 59) return undefined;
  const offset = zoneOffsetMinutes(zone);
  if (offset === undefined) return undefined;
  const local = Date.UTC(year, month, day, hour, minute, second);
  const calendar = new Date(local);
  if (calendar.getUTCDate() !== day || calendar.getUTCMonth() !== month) return undefined;
  return local - offset * 60_000;
}

/** The wait in milliseconds that a `Retry-After` value asks for, or `undefined` if unusable.
 *
 * Delay-seconds must be a non-negative number of ASCII digits (a decimal fraction
 * is tolerated). An HTTP-date may be IMF-fixdate, RFC 850 or asctime; a date
 * without a zone is UTC, and a date at or before `nowMs` yields 0. Anything else,
 * a negative number included, yields `undefined`. `nowMs` is the current epoch
 * time in milliseconds (`Date.now()`); it only turns an HTTP-date into a delay. */
export function parseRetryAfter(value: string | null, nowMs: number): number | undefined {
  if (typeof value !== "string") return undefined;
  const text = value.trim();
  if (!text) return undefined;
  if (DELAY_SECONDS.test(text)) {
    const delayMs = Number(text) * 1000;
    return Number.isFinite(delayMs) ? delayMs : undefined;
  }
  const when = httpDateMs(text);
  if (when === undefined) return undefined;
  const delayMs = when - nowMs;
  if (!Number.isFinite(delayMs)) return undefined;
  return delayMs > 0 ? delayMs : 0;
}

/** The rate-limit signal for an upstream response, or `undefined` unless it is a 429.
 *
 * The status alone decides; the body is never read, so the caller may still
 * return or cancel it. A usable `Retry-After` sets `waitMs` to the parsed value,
 * uncapped (the gate clamps it to `maxWaitMs`). Without one, `waitMs` is
 * `fallbackWaitMs` capped at `capMs`; an unparsable `Retry-After` counts as absent.
 * Only `x-ratelimit-*` headers are copied into the frozen snapshot.
 *
 * `fallbackWaitMs` and `capMs` must be greater than zero, which is checked before
 * the status: a caller bug throws a RangeError even for a successful response. */
export function classifyRateLimit(response: Response, fallbackWaitMs: number, capMs: number, nowMs: number): RateLimitSignal | undefined {
  if (!(fallbackWaitMs > 0 && capMs > 0)) throw new RangeError("fallbackWaitMs and capMs must be positive");
  if (response.status !== RATE_LIMIT_STATUS) return undefined;
  const snapshot: Record<string, string> = {};
  response.headers.forEach((value, name) => {
    const lowered = name.toLowerCase();
    // Fetch header values are byte strings, so code units are characters here.
    if (lowered.startsWith(SNAPSHOT_PREFIX)) snapshot[lowered] = value.slice(0, SNAPSHOT_VALUE_CHARS);
  });
  const headers = Object.freeze(snapshot);
  const parsed = parseRetryAfter(response.headers.get("retry-after"), nowMs);
  if (parsed === undefined) return Object.freeze({ waitMs: Math.min(fallbackWaitMs, capMs), retryAfterPresent: false, headers });
  return Object.freeze({ waitMs: parsed, retryAfterPresent: true, headers });
}

// --- scheduling (Requirements 1.3, 3.1, 3.5, 4.1, 4.2, 4.3, 5.1, 5.3, 6.3) ---------

/** What the gate did for one request, for the request diagnostic (Requirement 5.1).
 * Numbers, a flag and `x-ratelimit-*` header values only: never a body, a prompt or
 * a credential. UpstreamGate.run fills a record the caller supplies. */
export interface GateRecord {
  /** Milliseconds the request waited inside the gate, holds included, before it was
   * sent or refused; the time an attempt was in flight is not counted. */
  queuedMs: number;
  /** Upstream 429s received for the request. */
  rejections: number;
  /** `x-ratelimit-*` names and values from the upstream 429s, merged across them;
   * a later 429's value replaces an earlier one. */
  headers: Record<string, string>;
  /** Set when the gate answered with a 429 (its own, or the last upstream one)
   * instead of waiting any longer. */
  exhausted: boolean;
}

/** A fresh, empty GateRecord. */
export function createGateRecord(): GateRecord {
  return { queuedMs: 0, rejections: 0, headers: {}, exhausted: false };
}

/** What UpstreamGate takes from its environment; each field has a default. */
export interface GateDeps {
  /** Monotonic clock in milliseconds; `performance.now()` by default. */
  now?: () => number;
  /** Waits `ms` of that clock and rejects with BridgeError("cancelled") when `signal`
   * aborts; a real timer by default. */
  sleep?: (ms: number, signal?: AbortSignal) => Promise<void>;
  /** Draws the backoff jitter from [0, 1); `Math.random` by default. */
  random?: () => number;
  /** Told the key and the hold in milliseconds (the wait, clamped to `maxWaitMs`)
   * as a rate-limit hold starts; does nothing by default. */
  warn?: (key: string, waitMs: number) => void;
  /** Wall-clock time in epoch milliseconds, used only to turn an HTTP-date
   * `Retry-After` into a delay; `Date.now()` by default. Holds and slots stay on
   * the monotonic `now`. */
  wallNow?: () => number;
}

const EXHAUSTED_TYPE = "rate_limit_error";
const EXHAUSTED_CODE = "rate_limited";
// The shortest hold after a 429 on an unpaced key; a paced key holds at least its interval.
const UNPACED_HOLD_MS = 1000;
// The backoff stops doubling here. Any cap is reached long before, and the base
// stays finite however many 429s a run receives.
const MAX_DOUBLINGS = 64;

function cancelled(): BridgeError {
  return new BridgeError("cancelled");
}

function throwIfAborted(signal: AbortSignal | undefined): void {
  if (signal?.aborted) throw cancelled();
}

/** The default sleep: a real timer that an abort clears, rejecting with BridgeError("cancelled").
 * The abort listener is removed on either outcome. */
function sleepFor(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(cancelled());
      return;
    }
    const abort = (): void => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      reject(cancelled());
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", abort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", abort, { once: true });
  });
}

/** Wait for `promise`, or throw BridgeError("cancelled") as soon as `signal` aborts.
 * The abort listener is removed either way. */
async function unlessAborted(promise: Promise<void>, signal: AbortSignal | undefined): Promise<void> {
  if (signal === undefined) return promise;
  throwIfAborted(signal);
  let abort = (): void => undefined;
  const aborted = new Promise<never>((_, reject) => {
    abort = () => reject(cancelled());
  });
  signal.addEventListener("abort", abort, { once: true });
  try {
    await Promise.race([promise, aborted]);
  } finally {
    signal.removeEventListener("abort", abort);
  }
}

/** One run's place in its key's line: `turn` settles when the run ahead promotes it. */
class Waiter {
  readonly turn: Promise<void>;
  readonly promote: () => void;

  constructor() {
    let promote = (): void => undefined;
    this.turn = new Promise<void>(resolve => {
      promote = () => resolve();
    });
    this.promote = promote;
  }
}

/** One gate key: its pacing slot, its rate-limit hold and its FIFO line.
 *
 * `waiters[0]` belongs to the run whose turn it is; every later run waits for its
 * turn until the run ahead of it leaves and promotes it. On a paced key the head
 * waits for the slot and then keeps its place while it has the key's only attempt
 * in flight. On an unpaced key the head leaves the line as it sends, so any number
 * of its attempts can be in flight (`sending` counts them), and a run they get a
 * 429 for re-joins the line at the head for the hold. `holdUntil` is the end of
 * the key's rate-limit hold, honoured by every run on the key before it sends.
 * Times are on the gate's clock, in milliseconds. */
class KeyState {
  nextSlot = 0;
  holdUntil = 0;
  /** Unpaced runs whose attempt is in flight; they are not in `waiters`. */
  sending = 0;
  readonly waiters: Waiter[] = [];

  /** The earliest time at which the head may send. */
  readyAt(): number {
    return Math.max(this.nextSlot, this.holdUntil);
  }
}

/** The 429 the gate answers without an attempt, with a `retry-after` of `waitMs`
 * rounded up to whole seconds, at least 1. The body is fixed text plus that number;
 * nothing from the request, the environment or an upstream response goes into it. */
function exhausted(record: GateRecord, waitMs: number): Response {
  record.exhausted = true;
  const retryAfter = String(Math.max(1, Math.ceil(waitMs / 1000)));
  const body = { error: { message: `Upstream rate limit persisted; retry after ${retryAfter} seconds`, type: EXHAUSTED_TYPE, code: EXHAUSTED_CODE } };
  return new Response(JSON.stringify(body), {
    status: RATE_LIMIT_STATUS,
    headers: { "content-type": "application/json", "retry-after": retryAfter },
  });
}

/** Cancel the body of a 429 that will be sent again, without reading it, so its
 * stream is released while the run waits. This never throws and never waits: a
 * body that is absent or locked has nothing to cancel, and a cancel that rejects
 * or never settles cannot hold up the key. */
function discard(response: Response): void {
  try {
    response.body?.cancel().catch(() => undefined);
  } catch {
    // Nothing left to release.
  }
}

/** Schedules upstream attempts per key: FIFO order, optional pacing, rate-limit
 * holds, and a bounded wait and depth (the plugin's counterpart of the proxy's
 * UpstreamGate; unlike the proxy's, an unpaced key here does not serialize).
 *
 * A key (`models`, a model id) is one line, and runs on a key take their turn in
 * arrival order. A paced key (`rateFor` returns a rate) has at most one attempt in
 * flight, the head's, and starts its attempts at least `1000 / rate` ms apart. An
 * unpaced key, `models` always among them, has no spacing to enforce: its head
 * leaves the line as it sends, so concurrent runs have their attempts in flight
 * at the same time, as they had without the gate; only a hold makes them wait.
 * Keys never wait for one another.
 *
 * An upstream 429 holds its key: no run on it starts an attempt before the hold
 * ends. Attempts already in flight on an unpaced key are not affected; each run
 * handles its own answer. The 429 asks for a wait: as long as `Retry-After` says,
 * but at least the pacing interval (1000 ms when unpaced). Without a usable
 * `Retry-After` it is a jittered backoff that starts at that interval, doubles
 * with each rejection of the run and never exceeds `backoffCapMs`. The key is held
 * for that wait, but never longer than `maxWaitMs`, and `warn(key, holdMs)` is
 * called as each hold starts (a 429 that does not move the hold forward starts
 * none). The rejected run keeps the head of its line (on an unpaced key it
 * re-joins the line at the head) and is sent again when the hold ends, unless the
 * wait it was asked for, or the key's next slot, ends past its deadline (arrival +
 * `maxWaitMs`): then it returns that 429 at once, unread. A wait that ends exactly
 * at the deadline is not longer than the maximum wait, so the run is sent again
 * there. Several unpaced runs rejected meanwhile each re-join at the head and are
 * all sent again when the hold ends.
 *
 * At most `maxQueue` runs are inside the gate at once, waiting or in flight,
 * counted across all keys, so on an unpaced key it also bounds the attempts in
 * flight. A run beyond that bound, a run that reaches the head after its deadline,
 * and a run whose next slot or hold ends past that deadline get a synthesized 429
 * at once, without an attempt.
 *
 * A run whose AbortSignal aborts while it waits, a hold included, leaves its line
 * without sending, promotes the run behind it, and throws BridgeError("cancelled");
 * the key's hold stays for the runs behind it. A run aborted while its attempt is
 * in flight detaches that attempt and throws the same; a paced key is released at
 * once. */
export class UpstreamGate {
  readonly #options: RateLimitOptions;
  readonly #deps: Readonly<Required<GateDeps>>;
  readonly #keys = new Map<string, KeyState>();
  #held = 0;

  constructor(options: RateLimitOptions, deps: GateDeps = {}) {
    this.#options = options;
    this.#deps = {
      now: deps.now ?? (() => performance.now()),
      sleep: deps.sleep ?? sleepFor,
      random: deps.random ?? Math.random,
      warn: deps.warn ?? (() => undefined),
      wallNow: deps.wallNow ?? (() => Date.now()),
    };
  }

  /** The runs inside the gate right now, waiting or in flight, on every key. */
  get held(): number {
    return this.#held;
  }

  /** A reading of the gate's clock, in milliseconds. */
  now(): number {
    return this.#deps.now();
  }

  /** Call `attempt` when `key` comes to this run and return the upstream answer.
   *
   * The run arrives when `run` is called and gives up at arrival + `maxWaitMs`. A
   * response other than a 429 is returned unchanged and unread. A 429 is counted in
   * `record` with its `x-ratelimit-*` headers and holds the key; its body is
   * cancelled, never read, and the run waits at the head of its line and calls
   * `attempt` again. When the wait that 429 asks for, or the key's next slot, would
   * end past the deadline, the run returns that very 429 object at once instead,
   * unread and with its headers as received, and sets `record.exhausted`. When the
   * gate is full, the run reaches the head too late, or the key's next slot lies past
   * the deadline before an attempt, the gate returns a synthesized 429 (JSON body,
   * `retry-after` of at least 1 second) without calling `attempt` and sets
   * `record.exhausted`. Time spent waiting, holds included, is added to
   * `record.queuedMs`; time an attempt is in flight is not.
   *
   * A `signal` that is aborted at entry, or aborts while the run waits (a hold
   * included), makes `run` throw BridgeError("cancelled") without calling `attempt`
   * again; a waiting run leaves its line and promotes the next, and the key's hold
   * stays. An error from `attempt` propagates unchanged once the key is released. */
  async run(key: string, attempt: () => Promise<Response>, signal: AbortSignal | undefined, record: GateRecord): Promise<Response> {
    throwIfAborted(signal);
    const arrived = this.#deps.now();
    if (this.#held >= this.#options.maxQueue) {
      const readyAt = this.#keys.get(key)?.readyAt() ?? arrived;
      return exhausted(record, Math.max(readyAt - arrived, this.#interval(key)));
    }
    const state = this.#state(key, arrived);
    const waiter = new Waiter();
    state.waiters.push(waiter);
    this.#held += 1;
    try {
      return await this.#serve(key, state, waiter, attempt, signal, arrived, record);
    } finally {
      this.#held -= 1;
      this.#leave(state, waiter);
    }
  }

  /** Wait for the head position, then send until the upstream takes the request.
   *
   * A paced run stays at the head while its attempt is in flight. An unpaced run
   * leaves the line as it sends, promoting the run behind it, and re-joins at the
   * head when a 429 is to be sent again.
   *
   * Each 429 holds the key and sends again from the head, until the run is out of
   * time. The run is out of time as soon as a 429 asks for a wait that ends past the
   * deadline. That is decided on the wait as asked, before the key's hold is clamped
   * to `maxWaitMs`; otherwise a clamped hold ending exactly on the deadline would buy
   * one more send the upstream already refused. It is also out of time when the
   * key's next slot ends past the deadline (a backoff cap below the pacing
   * interval), since no send could happen in time. Either way the 429 at hand is
   * returned unread; a 429 that is sent again has its body cancelled first. */
  async #serve(key: string, state: KeyState, waiter: Waiter, attempt: () => Promise<Response>,
    signal: AbortSignal | undefined, arrived: number, record: GateRecord): Promise<Response> {
    if (state.waiters[0] !== waiter) await unlessAborted(waiter.turn, signal);
    const interval = this.#interval(key);
    const shortest = interval > 0 ? interval : UNPACED_HOLD_MS;
    const deadline = arrived + this.#options.maxWaitMs;
    // Waiting is counted from arrival and then from each 429, so the time an
    // attempt is in flight is never counted as queued.
    let waitedFrom = arrived;
    let rejections = 0;
    for (;;) {
      // After a 429 the decision below already covers the slot and the hold, so
      // only a clock that moved on in between, or a longer hold that another run on
      // an unpaced key started meanwhile, can make this refuse.
      const onTime = await this.#waitForSlot(state, deadline, signal);
      // However late an abort lands, nothing is sent after it.
      throwIfAborted(signal);
      const now = this.#deps.now();
      // On an unpaced key another run's 429 can start a hold while this run resumes
      // from its check; that hold applies to this run as well. (A paced key's slot
      // and hold change only through its head, so this never repeats there.)
      if (onTime && now < state.readyAt()) continue;
      record.queuedMs += now - waitedFrom;
      if (!onTime) return exhausted(record, Math.max(state.readyAt() - now, interval));
      if (interval > 0) state.nextSlot = now + interval;
      // A paced head keeps the key while its attempt is in flight, unless the run
      // is aborted meanwhile: then the attempt is detached and the key released.
      // An unpaced key has no spacing to enforce, so its head hands the line on
      // as soon as it sends and concurrent requests overlap as they did before
      // the gate existed; only a hold makes them wait. Everything from the check
      // above to the send runs without a pause, so no hold can start in between.
      let response: Response;
      if (interval === 0) {
        this.#leave(state, waiter);
        state.sending += 1;
        try {
          response = await this.#inFlight(attempt(), signal);
        } finally {
          state.sending -= 1;
        }
      } else {
        response = await this.#inFlight(attempt(), signal);
      }
      const rejection = classifyRateLimit(response, shortest * 2 ** Math.min(rejections, MAX_DOUBLINGS),
        this.#options.backoffCapMs, this.#deps.wallNow());
      if (rejection === undefined) return response;
      rejections += 1;
      record.rejections += 1;
      Object.assign(record.headers, rejection.headers);
      const rejectedAt = this.#deps.now();
      const wait = this.#hold(key, state, rejection, shortest, rejectedAt);
      if (Math.max(rejectedAt + wait, state.readyAt()) > deadline) {
        // The host presents this 429 itself: status, headers and body as received.
        record.exhausted = true;
        return response;
      }
      discard(response);
      waitedFrom = rejectedAt;
      // An unpaced run left its line to send; rejected, it is back at the head
      // and waits out the hold there.
      if (interval === 0) state.waiters.unshift(waiter);
    }
  }

  /** Await an in-flight attempt, or detach it as soon as `signal` aborts.
   *
   * On abort the run rejects with BridgeError("cancelled") at once. A paced run then
   * releases the key, so a transport that ignores the abort cannot hold later runs
   * on the key; an unpaced run (a discovery superseded by a newer one among them)
   * left the line when it sent and only stops counting as in flight and as held.
   * The detached attempt settles on its own: its eventual response is discarded and
   * a rejection is swallowed. A failure of the attempt itself propagates unchanged. */
  async #inFlight(pending: Promise<Response>, signal: AbortSignal | undefined): Promise<Response> {
    if (signal === undefined) return pending;
    let abort = (): void => undefined;
    const aborted = new Promise<never>((_, reject) => {
      abort = () => reject(cancelled());
    });
    signal.addEventListener("abort", abort, { once: true });
    try {
      return await Promise.race([pending, aborted]);
    } catch (error) {
      if (!signal.aborted) throw error;
      pending.then(discard, () => undefined);
      throw cancelled();
    } finally {
      signal.removeEventListener("abort", abort);
    }
  }

  /** Hold `key` after a 429, warn once as the hold starts, and return the wait.
   *
   * `Retry-After` sets the wait, floored at `shortest` (the pacing interval, 1000 ms
   * when unpaced) so that a zero or past value never makes the re-send follow at
   * once. Without one, the backoff from classifyRateLimit (already capped) is
   * jittered between half and one and a half times itself, then kept within
   * `shortest` and `backoffCapMs`, the cap winning. The key is held from `now` for
   * that wait, but for no longer than `maxWaitMs`; the hold only ever moves forward.
   * The returned wait is the unclamped one, which decides whether the run can still
   * be sent within its deadline. */
  #hold(key: string, state: KeyState, rejection: RateLimitSignal, shortest: number, now: number): number {
    const wait = rejection.retryAfterPresent
      ? Math.max(rejection.waitMs, shortest)
      : Math.min(Math.max(rejection.waitMs * (0.5 + this.#deps.random()), shortest), this.#options.backoffCapMs);
    const hold = Math.min(wait, this.#options.maxWaitMs);
    if (now + hold > state.holdUntil) {
      state.holdUntil = now + hold;
      this.#deps.warn(key, hold);
    }
    return wait;
  }

  /** Sleep until the key may send and return true, or return false to give up.
   *
   * The run gives up at once, without sleeping, when it is already past its
   * deadline or when the key's next slot or hold ends past it. Each sleep covers
   * exactly the remaining delta, and the slot is checked again after waking. Only
   * the slot is held against the deadline after a sleep, so a late wake-up from a
   * sleep that ended within it still sends. */
  async #waitForSlot(state: KeyState, deadline: number, signal: AbortSignal | undefined): Promise<boolean> {
    if (this.#deps.now() > deadline) return false;
    for (;;) {
      const now = this.#deps.now();
      const readyAt = state.readyAt();
      if (now >= readyAt) return true;
      if (readyAt > deadline) return false;
      await this.#pause(readyAt - now, signal);
    }
  }

  /** The injected sleep; whatever it rejects with, an abort surfaces as BridgeError("cancelled"). */
  async #pause(ms: number, signal: AbortSignal | undefined): Promise<void> {
    try {
      await this.#deps.sleep(ms, signal);
    } catch (error) {
      if (!signal?.aborted) throw error;
    }
    throwIfAborted(signal);
  }

  /** The pacing interval of `key` in milliseconds, or 0 when the key is not paced. */
  #interval(key: string): number {
    const rate = rateFor(this.#options, key);
    return rate === undefined ? 0 : 1000 / rate;
  }

  /** The state for `key`, created on first use.
   *
   * Before creating one, the gate drops idle keys whose slot and hold have passed:
   * no waiter, no unpaced attempt in flight (its 429 would hold this very state)
   * and nothing left to enforce. A dropped key behaves exactly like a new one, so
   * the key map holds only keys that still have something to enforce. */
  #state(key: string, now: number): KeyState {
    let state = this.#keys.get(key);
    if (state === undefined) {
      for (const [name, known] of this.#keys) {
        if (known.waiters.length === 0 && known.sending === 0 && known.readyAt() <= now) this.#keys.delete(name);
      }
      state = new KeyState();
      this.#keys.set(key, state);
    }
    return state;
  }

  /** Take `waiter` out of its line; if it was the head, promote the next run.
   * A next run that was aborted meanwhile leaves through here as the head in turn
   * and promotes the run after it. A waiter that is no longer in the line (an
   * unpaced run that left it to send) is left alone. On an unpaced key a run that
   * re-joined at the head can stand before runs already past their turn, so a run
   * may also leave from behind the head; it then promotes nobody, and promoting a
   * run already past its turn does nothing. */
  #leave(state: KeyState, waiter: Waiter): void {
    const position = state.waiters.indexOf(waiter);
    // An unpaced run that is in flight has already left its line.
    if (position < 0) return;
    state.waiters.splice(position, 1);
    if (position === 0) state.waiters[0]?.promote();
  }
}
