/** Pure preparation checks; importing this module never starts a process. */
type Manifest = {
  packageManager: string;
  dependencies: Record<string, string>;
  devDependencies: Record<string, string>;
};

export function checkPreparedDependencies(read: (path: string) => string, bunVersion: string): void {
  let manifest: Manifest;
  let lock: {
    workspaces: Record<string, { dependencies: Record<string, string>; devDependencies: Record<string, string> }>;
    packages: Record<string, [string, ...unknown[]]>;
  };
  try {
    manifest = JSON.parse(read("package.json"));
    // Bun's text lockfile allows trailing commas, as in the canonical verifier.
    lock = JSON.parse(read("bun.lock").replace(/,\s*([}\]])/g, "$1"));
  } catch {
    throw new Error("Read valid package.json and bun.lock from the prepared provider checkout");
  }
  if (manifest.packageManager !== "bun@1.4.4" || bunVersion !== "1.4.4")
    throw new Error("Prepare pinned Bun 1.4.4 before offline checks");
  if (manifest.devDependencies["@modelcontextprotocol/sdk"] !== "1.32.2" ||
      manifest.devDependencies["@ai-sdk/provider"] !== "3.0.19")
    throw new Error("Declare the approved MCP SDK 1.32.2 and V3 provider types 3.0.19 as exact development dependencies");
  const declared = { ...manifest.dependencies, ...manifest.devDependencies };
  const workspace = lock.workspaces[""];
  const locked = { ...workspace?.dependencies, ...workspace?.devDependencies };
  if (Object.keys(declared).length !== Object.keys(locked).length)
    throw new Error("Manifest/lock mismatch; prepare the approved lockfile explicitly");
  for (const [name, version] of Object.entries(declared)) {
    if (locked[name] !== version || lock.packages[name]?.[0] !== `${name}@${version}`)
      throw new Error("Manifest/lock mismatch; prepare the approved lockfile explicitly");
    let installed: { version?: string };
    try { installed = JSON.parse(read(`node_modules/${name}/package.json`)); }
    catch { throw new Error("Run bun install --frozen-lockfile explicitly before offline checks"); }
    if (installed.version !== version)
      throw new Error("Prepared dependency version mismatch; run bun install --frozen-lockfile explicitly before offline checks");
  }
}

export function requireReferenceHostVersion(output: string, expected = "2.2.5"): string {
  if (!/^2\.\d+\.\d+$/.test(expected) || output.trim().replace(/^(?:opencode\s+)?v?/i, "") !== expected)
    throw new Error(expected === "2.2.5" ? "Prepare OpenCode 2.2.5; other versions require separate compatibility evidence" : "Prepare the exact expected OpenCode 2.x pair");
  return expected;
}
