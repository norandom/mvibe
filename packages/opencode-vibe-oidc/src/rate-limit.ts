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
 * x-ratelimit-* headers that is safe to record. */
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
// Gate key for model-list discovery: never proactively paced (Requirement 3.5).
const MODELS_KEY = "models";
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
