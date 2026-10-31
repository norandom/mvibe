import { describe, expect, test } from "bun:test";
import { HostFixtureCredentials } from "./host-fixture-credentials.js";

const origin = "https://inference.example.test";
const target = origin + "/v1/models";
const headers = { Authorization: "Bearer synthetic-one" };
const request = (url = target, authorization = headers.Authorization, method = "GET", redirect: RequestRedirect = "manual") => new Request(url, { method, redirect, headers: { authorization } });

describe("isolated host fixture credential rotation", () => {
  test("accepts a model request resolved before rotation and sent afterward", () => {
    const credentials = new HostFixtureCredentials();
    credentials.resolved(target, headers);
    expect(credentials.assertRequest(request(), "two")).toBe("one");
  });
  test("requires current credentials for inference despite a valid earlier resolution", () => {
    const credentials = new HostFixtureCredentials();
    const chat = origin + "/v1/chat/completions";
    credentials.resolved(chat, headers);
    expect(() => credentials.assertRequest(request(chat, headers.Authorization, "POST"), "two")).toThrow();
    credentials.resolved(chat, { Authorization: "Bearer synthetic-two" });
    expect(credentials.assertRequest(request(chat, "Bearer synthetic-two", "POST"), "two")).toBe("two");
  });
  test("rejects unauthenticated, arbitrary and unresolved snapshots", () => {
    for (const authorization of ["", "Bearer arbitrary", "Bearer synthetic-one"]) {
      const credentials = new HostFixtureCredentials();
      expect(() => credentials.assertRequest(request(target, authorization), "two")).toThrow();
    }
    expect(() => new HostFixtureCredentials().resolved(target, { Authorization: "Bearer arbitrary" })).toThrow();
  });
  test("requires matching resolution destination, approved origin, path, method and redirect", () => {
    for (const invalid of [request(origin + "/v1/chat/completions", headers.Authorization, "POST"), request("https://other.example.test/v1/models"), request(origin + "/v1/other"), request(target, headers.Authorization, "POST"), request(target, headers.Authorization, "GET", "follow")]) {
      const credentials = new HostFixtureCredentials();
      credentials.resolved(target, headers);
      expect(() => credentials.assertRequest(invalid, "one")).toThrow();
    }
  });
});
