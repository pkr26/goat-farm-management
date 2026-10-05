import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
  assertImageDecodeSafetyForBuild,
  defaultSharpProbe,
} from "@/lib/image-deps-guard";

const tempRoots: string[] = [];
const safeVersions = { sharp: "0.35.4", vips: "8.18.6", heif: "1.23.2" };

function fakeSharpInstall(source: string): string {
  const root = mkdtempSync(join(tmpdir(), "image-deps-probe-"));
  tempRoots.push(root);
  mkdirSync(join(root, "node_modules", "sharp"), { recursive: true });
  mkdirSync(join(root, "node_modules", "next"), { recursive: true });
  writeFileSync(join(root, "node_modules", "sharp", "index.js"), source);
  writeFileSync(join(root, "node_modules", "next", "package.json"), '{"version":"16.3.8"}');
  return root;
}

function fakeVersions(versions: unknown): string {
  return fakeSharpInstall(`module.exports = {versions: ${JSON.stringify(versions)}};`);
}

afterEach(() => {
  for (const root of tempRoots.splice(0)) rmSync(root, { recursive: true, force: true });
});

describe("isolated installed-sharp version probe", () => {
  it("reads the requested install tree without loading sharp into its parent and retains the unsafe-version gate", () => {
    const root = fakeVersions({ ...safeVersions, heif: "1.23.1" });
    const require = createRequire(join(root, "package.json"));
    const sharpPath = require.resolve("sharp");
    expect(require.cache[sharpPath]).toBeUndefined();
    expect(defaultSharpProbe(root)).toEqual({ heif: "1.23.1" });
    expect(require.cache[sharpPath]).toBeUndefined();
    expect(() => assertImageDecodeSafetyForBuild("phase-production-build", { rootDir: root }))
      .toThrow(/libheif 1\.23\.1/);
  });

  it("recognizes no decoder only from a valid sharp/libvips version report", () => {
    expect(defaultSharpProbe(fakeVersions({ sharp: "0.35.4", vips: "8.18.6" }))).toEqual({});
  });

  it.each([
    ["nonzero exit", "process.exit(1)"],
    ["signal termination", 'process.kill(process.pid, "SIGKILL")'],
  ])("rejects real child %s even when stdout claims a safe version", (_label, terminate) => {
    const root = fakeSharpInstall(`process.stdout.write(${JSON.stringify(JSON.stringify(safeVersions))}); ${terminate};`);
    expect(defaultSharpProbe(root)).toBe("unloadable");
  });

  it("rejects output beyond the buffer bound", () => {
    const root = fakeSharpInstall(`process.stdout.write(" ".repeat(32 * 1024)); module.exports = {versions: ${JSON.stringify(safeVersions)}};`);
    expect(defaultSharpProbe(root)).toBe("unloadable");
  });

  it("rejects a failure to start the child in a missing install directory", () => {
    const root = fakeVersions(safeVersions);
    rmSync(root, { recursive: true });
    expect(defaultSharpProbe(root)).toBe("unloadable");
  });

  it.each(["process.exit(0)", 'process.stdout.write("not-json"); module.exports = {versions: {}};'])
    ("rejects empty or non-JSON child output: %s", (source) => {
      expect(defaultSharpProbe(fakeSharpInstall(source))).toBe("unloadable");
    });

  it.each([
    null, [], "1.23.2", {},
    { ...safeVersions, sharp: null },
    { ...safeVersions, vips: "" },
    ...[null, false, 1.24, "", "1.23.x", "1.23.2-rc.1"].map((heif) => ({ ...safeVersions, heif })),
  ].map((versions) => ({ versions })))
    ("fails closed on malformed native report %j", ({ versions }) => {
    const root = fakeVersions(versions);
    expect(() => assertImageDecodeSafetyForBuild("phase-production-build", { rootDir: root }))
      .toThrow(/sharp could not be loaded/);
  });

  it("kills a real non-exiting child at the bound, including one that ignores SIGTERM", () => {
    const root = fakeSharpInstall(`process.on("SIGTERM", () => {}); setInterval(() => {}, 1000); module.exports = {versions: ${JSON.stringify(safeVersions)}};`);
    const start = performance.now();
    expect(defaultSharpProbe(root)).toBe("unloadable");
    expect(performance.now() - start).toBeLessThan(15_000);
  }, 20_000);
});
