import assert from "node:assert/strict";

type Revision = "one" | "two";
function revision(authorization: string | null): Revision {
  if (authorization === "Bearer synthetic-one") return "one";
  if (authorization === "Bearer synthetic-two") return "two";
  throw new Error("Unrecognized synthetic fixture credential");
}

/** The isolated helper resolves immutable snapshots before transport is called. */
export class HostFixtureCredentials {
  private readonly snapshots = new Map<string, number>();

  resolved(target: string, headers: HeadersInit) {
    const key = target + "\n" + revision(new Headers(headers).get("authorization"));
    this.snapshots.set(key, (this.snapshots.get(key) ?? 0) + 1);
  }

  assertRequest(request: Request, current: string): Revision {
    const url = new URL(request.url);
    assert.equal(url.origin, "https://inference.example.test");
    assert.equal(request.redirect, "manual");
    const discovery = url.pathname === "/v1/models" && request.method === "GET";
    assert.ok(discovery || url.pathname === "/v1/chat/completions" && request.method === "POST");
    const observed = revision(request.headers.get("authorization"));
    const key = request.url + "\n" + observed;
    const count = this.snapshots.get(key) ?? 0;
    assert.ok(count > 0, "Each transport request must use a captured helper resolution for its destination");
    // Background discovery may cross the fixture's deliberate file rotation.
    // Inference remains strict: both one-shot requests must resolve afresh.
    if (!discovery) assert.equal(observed, current);
    if (count === 1) this.snapshots.delete(key); else this.snapshots.set(key, count - 1);
    return observed;
  }
}
