import { expect, it } from "vitest";

import { evaluateImageDecodeSafety, type SharpProbeResult } from "@/lib/image-deps-guard";

it.each<SharpProbeResult>(["unloadable", { heif: "1.23.1" }])(
  "labels a blank installed Next version as unknown while failing closed with %s", (probe) => {
    const verdict = evaluateImageDecodeSafety("", probe);
    expect(verdict.ok).toBe(false);
    if (!verdict.ok) expect(verdict.reason).toContain("next unknown");
  },
);

it.each<SharpProbeResult>(["unloadable", { heif: "1.23.1" }])(
  "identifies a provided Next version in the failure diagnostic with %s", (probe) => {
    const verdict = evaluateImageDecodeSafety("16.3.4", probe);
    expect(verdict.ok).toBe(false);
    if (!verdict.ok) {
      expect(verdict.reason).toContain("next 16.3.4");
      expect(verdict.reason).not.toContain("next unknown");
    }
  },
);
