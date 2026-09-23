import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import packageJson from "../../package.json";
import {
  MIN_SAFE_LIBHEIF_VERSION,
  NEXT_VERSIONS_WITH_HEIF_DECODE_DISABLED,
  checkInstalledImageDecodeSafety,
  compareDottedVersions,
  evaluateImageDecodeSafety,
  reportImageDecodeSafety,
} from "@/lib/image-deps-guard";

const tempRoots: string[] = [];

afterEach(() => {
  while (tempRoots.length > 0) {
    const root = tempRoots.pop();
    if (root !== undefined) rmSync(root, { recursive: true, force: true });
  }
});

/** A fake install tree containing node_modules/next/package.json. */
function fakeNextInstall(version: string): string {
  const root = mkdtempSync(join(tmpdir(), "image-deps-guard-"));
  tempRoots.push(root);
  mkdirSync(join(root, "node_modules", "next"), { recursive: true });
  writeFileSync(
    join(root, "node_modules", "next", "package.json"),
    JSON.stringify({ name: "next", version }),
  );
  return root;
}

describe("compareDottedVersions", () => {
  it("compares numerically, not lexicographically", () => {
    expect(compareDottedVersions("1.23.10", "1.23.2")).toBeGreaterThan(0);
    expect(compareDottedVersions("1.23.2", "1.23.2")).toBe(0);
    expect(compareDottedVersions("2.0", "2.0.1")).toBeLessThan(0);
    expect(compareDottedVersions("1.24", "1.23.99")).toBeGreaterThan(0);
  });
});

describe("evaluateImageDecodeSafety", () => {
  it("accepts the pinned next that disables HEIF decoding itself", () => {
    // Even a hypothetical vulnerable libheif is unreachable while the
    // optimizer refuses HEIF input at the Next layer.
    const verdict = evaluateImageDecodeSafety("16.3.3", { heif: "1.19.3" });
    expect(verdict).toEqual({ ok: true, basis: "next-disables-heif" });
  });

  it("accepts a re-enabled next once sharp bundles a fixed libheif", () => {
    expect(evaluateImageDecodeSafety("16.3.4", { heif: "1.23.2" })).toEqual({
      ok: true,
      basis: "libheif-safe",
    });
    expect(evaluateImageDecodeSafety("17.0.0", { heif: "1.24.0" })).toEqual({
      ok: true,
      basis: "libheif-safe",
    });
  });

  it("fails closed on a re-enabled next with a vulnerable bundled libheif", () => {
    const verdict = evaluateImageDecodeSafety("16.3.4", { heif: "1.23.1" });
    expect(verdict.ok).toBe(false);
    if (!verdict.ok) {
      expect(verdict.reason).toContain("1.23.1");
      expect(verdict.reason).toContain("NEXT_VERSIONS_WITH_HEIF_DECODE_DISABLED");
    }
  });

  it("treats a libvips without any HEIF decoder as having no surface", () => {
    expect(evaluateImageDecodeSafety("16.3.4", {})).toEqual({
      ok: true,
      basis: "no-heif-decoder",
    });
  });

  it("fails closed when sharp cannot be probed and next does not disable HEIF", () => {
    const verdict = evaluateImageDecodeSafety("16.3.4", "unloadable");
    expect(verdict.ok).toBe(false);
    if (!verdict.ok) {
      expect(verdict.reason).toContain("sharp could not be loaded");
    }
  });

  it("still passes an unloadable sharp while next disables HEIF decoding", () => {
    expect(evaluateImageDecodeSafety("16.3.3", "unloadable")).toEqual({
      ok: true,
      basis: "next-disables-heif",
    });
  });

  it("keeps deciding without a next version (unknown fails closed)", () => {
    expect(evaluateImageDecodeSafety(undefined, { heif: "1.19.3" }).ok).toBe(false);
  });
});

describe("checkInstalledImageDecodeSafety", () => {
  it("reads the installed next version from node_modules", () => {
    const root = fakeNextInstall("16.3.4");
    const verdict = checkInstalledImageDecodeSafety({
      rootDir: root,
      sharpProbe: { heif: "1.23.2" },
    });
    expect(verdict).toEqual({ ok: true, basis: "libheif-safe" });
  });

  it("fails when the installed next manifest cannot be read", () => {
    const verdict = checkInstalledImageDecodeSafety({
      rootDir: join(tmpdir(), "image-deps-guard-nonexistent"),
      sharpProbe: { heif: "1.23.2" },
    });
    expect(verdict.ok).toBe(false);
    if (!verdict.ok) {
      expect(verdict.reason).toContain("could not read the installed next version");
    }
  });

  it("runs the default sharp probe when none is injected", () => {
    // Under vitest's ESM runtime `require` does not exist, so the default
    // probe reports "unloadable" — and the pinned next still passes on the
    // HEIF-disabled leg, which is exactly the build context resilience the
    // guard needs (the real native probe runs in next's CJS config loader).
    const verdict = checkInstalledImageDecodeSafety({ nextVersion: "16.3.3" });
    expect(verdict).toEqual({ ok: true, basis: "next-disables-heif" });
  });

  it("the shipped dependency pair satisfies the guard's safe set", () => {
    // The repo pins next exactly; whenever that pin leaves the
    // HEIF-disabled set, the sharp side of the contract (>= 1.23.2) becomes
    // load-bearing — this sanity check keeps the test suite honest about
    // which leg the shipped tree stands on.
    const nextVersion = packageJson.dependencies.next;
    expect(NEXT_VERSIONS_WITH_HEIF_DECODE_DISABLED.has(nextVersion)).toBe(true);
    expect(
      evaluateImageDecodeSafety(nextVersion, { heif: MIN_SAFE_LIBHEIF_VERSION }).ok,
    ).toBe(true);
  });
});

describe("reportImageDecodeSafety", () => {
  it("throws in enforce mode and warns otherwise", () => {
    const failing = evaluateImageDecodeSafety("16.3.4", { heif: "1.23.1" });
    expect(failing.ok).toBe(false);
    if (!failing.ok) {
      expect(() => reportImageDecodeSafety(failing, "enforce")).toThrow(/libheif 1\.23\.1/);
      const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
      try {
        expect(() => reportImageDecodeSafety(failing, "warn")).not.toThrow();
      } finally {
        warn.mockRestore();
      }
      expect(warn).toHaveBeenCalledWith(expect.stringContaining("Unsafe image-decode"));
    }
  });

  it("stays silent on a passing verdict in both modes", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      expect(() =>
        reportImageDecodeSafety({ ok: true, basis: "libheif-safe" }, "enforce"),
      ).not.toThrow();
    } finally {
      warn.mockRestore();
    }
    expect(warn).not.toHaveBeenCalled();
  });
});
