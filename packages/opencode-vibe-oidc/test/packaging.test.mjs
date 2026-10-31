import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

test("provider foundation pins SDK and host and enables strict builds", () => {
  const pkg = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
  const ts = JSON.parse(readFileSync(new URL("../tsconfig.json", import.meta.url), "utf8"));
  assert.equal(pkg.dependencies["@opencode/plugin"], "2.2.5");
  assert.equal(pkg.devDependencies["@opencode/cli"], "2.2.5");
  assert.equal(ts.compilerOptions.strict, true);
  assert.equal(pkg.exports["."].import, "./dist/index.js");
  assert.equal(pkg.exports["."].types, "./dist/index.d.ts");
  for (const script of ["test", "typecheck", "build", "test:host"]) assert.ok(pkg.scripts[script]);
});

