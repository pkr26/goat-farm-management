/**
 * Deploy-time guard for the HEIF/AVIF decode surface in image optimization.
 *
 * next@16.3.3 shipped a fix for libheif/AVIF decoder RCEs by DISABLING HEIF
 * decoding in its image optimizer; 16.3.4 re-enabled AVIF without a runtime
 * guard, so from that version on safety rests entirely on the libheif that
 * sharp bundles (readable as `sharp.versions.heif`). This module turns that
 * implicit dependency into an explicit, machine-checked contract:
 *
 * - a next version known to disable HEIF decoding passes as-is;
 * - any other next version requires the installed sharp to report a
 *   libheif >= MIN_SAFE_LIBHEIF_VERSION (or no HEIF decoder at all);
 * - anything else fails the production build (next.config.ts calls
 *   {@link assertImageDecodeSafetyForBuild} at config-load time, so CI's
 *   `pnpm build` and the Docker builder stage both gate on it).
 *
 * When bumping next, either verify the new version's sharp bundles a safe
 * libheif (the build then passes on its own), or — if upstream disabled HEIF
 * decoding again — add the exact version to NEXT_VERSIONS_WITH_HEIF_DECODE_DISABLED.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

/** Exact next versions whose image optimizer refuses to decode HEIF input. */
export const NEXT_VERSIONS_WITH_HEIF_DECODE_DISABLED: ReadonlySet<string> = new Set([
  "16.3.3",
]);

/** Minimum bundled libheif with the decoder fixes (sharp 0.35.4 ships it). */
export const MIN_SAFE_LIBHEIF_VERSION = "1.23.2";

export type ImageDecodeSafety =
  | { ok: true; basis: "next-disables-heif" | "libheif-safe" | "no-heif-decoder" }
  | { ok: false; reason: string };

/** Probe result for the installed sharp: its version info, or why not. */
export type SharpProbeResult =
  | { heif?: string | undefined }
  | "unloadable";

/**
 * Compare dotted numeric versions segment by segment ("1.23.10" > "1.23.2").
 */
export function compareDottedVersions(a: string, b: string): number {
  const left = a.split(".").map((part) => Number(part) || 0);
  const right = b.split(".").map((part) => Number(part) || 0);
  const length = Math.max(left.length, right.length);
  for (let index = 0; index < length; index += 1) {
    const delta = (left[index] ?? 0) - (right[index] ?? 0);
    if (delta !== 0) return delta;
  }
  return 0;
}

/**
 * The pure policy: may this (next, bundled libheif) pair decode untrusted
 * images? "unloadable" sharp only matters once the next pin no longer
 * disables HEIF itself — fail closed rather than guess.
 */
export function evaluateImageDecodeSafety(
  nextVersion: string | undefined,
  sharpProbe: SharpProbeResult,
): ImageDecodeSafety {
  if (nextVersion !== undefined && NEXT_VERSIONS_WITH_HEIF_DECODE_DISABLED.has(nextVersion)) {
    return { ok: true, basis: "next-disables-heif" };
  }
  if (sharpProbe === "unloadable") {
    return {
      ok: false,
      reason:
        "sharp could not be loaded, so the bundled libheif version cannot be verified " +
        `(next ${nextVersion ?? "unknown"} does not disable HEIF decoding itself).`,
    };
  }
  if (sharpProbe.heif === undefined) {
    // No HEIF decoder compiled into libvips: untrusted HEIF inputs error out
    // instead of reaching libheif, so there is no decoder surface to exploit.
    return { ok: true, basis: "no-heif-decoder" };
  }
  if (compareDottedVersions(sharpProbe.heif, MIN_SAFE_LIBHEIF_VERSION) >= 0) {
    return { ok: true, basis: "libheif-safe" };
  }
  return {
    ok: false,
    reason:
      `sharp bundles libheif ${sharpProbe.heif} < ${MIN_SAFE_LIBHEIF_VERSION}, but ` +
      `next ${nextVersion ?? "unknown"} decodes HEIF/AVIF input again — upgrading ` +
      "next without a sharp whose libheif is fixed reopens the decoder RCE surface. " +
      "Upgrade sharp, or verify upstream disabled HEIF decoding and pin that " +
      "exact next version in NEXT_VERSIONS_WITH_HEIF_DECODE_DISABLED.",
  };
}

/** Default probe: read sharp's compiled-in dependency versions. */
export function defaultSharpProbe(): SharpProbeResult {
  try {
    // sharp is a native CJS module — must be require()d lazily so bundlers
    // never inline it and jsdom test environments never load it at import.
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const sharp = require("sharp") as { versions?: { heif?: string | undefined } };
    return sharp.versions ?? {};
  } catch {
    return "unloadable";
  }
}

export type InstalledImageDeps = {
  nextVersion?: string | undefined;
  sharpProbe?: SharpProbeResult | undefined;
  rootDir?: string | undefined;
};

/**
 * Evaluate the policy against the installed tree (package metadata is read
 * from `<rootDir>/node_modules/next/package.json`, defaulting to the working
 * directory). Every input is injectable for tests.
 */
export function checkInstalledImageDecodeSafety(deps: InstalledImageDeps = {}): ImageDecodeSafety {
  const sharpProbe = deps.sharpProbe ?? defaultSharpProbe();
  let nextVersion = deps.nextVersion;
  if (nextVersion === undefined) {
    const manifest = join(deps.rootDir ?? process.cwd(), "node_modules", "next", "package.json");
    try {
      nextVersion = (JSON.parse(readFileSync(manifest, "utf8")) as { version?: string }).version;
    } catch {
      return {
        ok: false,
        reason: `could not read the installed next version from ${manifest}`,
      };
    }
  }
  return evaluateImageDecodeSafety(nextVersion, sharpProbe);
}

export type SafetyReportMode = "enforce" | "warn";

/**
 * Act on a verdict: "enforce" turns a failure into a thrown error (failing
 * the surrounding `next build`), "warn" only logs it.
 */
export function reportImageDecodeSafety(
  verdict: ImageDecodeSafety,
  mode: SafetyReportMode,
): void {
  if (verdict.ok) return;
  const message = `Unsafe image-decode dependency combination: ${verdict.reason}`;
  if (mode === "enforce") {
    throw new Error(message);
  }
  console.warn(message);
}

/**
 * Hook used by next.config.ts: hard-fail during a production build
 * (NEXT_PHASE=phase-production-build), warn in every other context so a
 * broken developer tree cannot be shipped as a build artifact.
 */
export function assertImageDecodeSafetyForBuild(): void {
  const mode: SafetyReportMode =
    process.env.NEXT_PHASE === "phase-production-build" ? "enforce" : "warn";
  reportImageDecodeSafety(checkInstalledImageDecodeSafety(), mode);
}
