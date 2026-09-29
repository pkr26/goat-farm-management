/**
 * Worker-tablet PWA manifest (2026-09-28 audit, T2 — the file sat at 0%).
 * Pins the shape, including the tablet's straight-to-the-board start_url and
 * the W11 additions from the same audit: explicit id/scope, a maskable icon
 * purpose entry, and a background_color that is the app canvas token
 * (--background, oklch(0.988 0.005 95)) resolved to the hex the manifest
 * spec requires.
 *
 * The icon set is generated from the brand mark (scripts/generate-worker-icons.mjs):
 * the maskable entry is a DEDICATED asset whose whole glyph sits inside the
 * 66dp-of-108dp safe circle, and src/app/apple-icon.png (180×180, iOS
 * Add-to-Home-Screen ignores manifest icons) exists for real — both are
 * asserted on disk so a rename/regression cannot ship a manifest that
 * points at nothing.
 */

import { existsSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import manifest from "./manifest";

describe("worker PWA manifest", () => {
  it("opens the home-screen icon straight onto the duty board", () => {
    expect(manifest()).toEqual({
      id: "/worker",
      name: "Herdly Worker",
      short_name: "Herdly",
      description: "Daily duty board for farm workers",
      start_url: "/worker",
      scope: "/",
      display: "standalone",
      background_color: "#FCFBF7",
      theme_color: "#166534",
      icons: [
        { src: "/icon-worker-192.png", sizes: "192x192", type: "image/png" },
        { src: "/icon-worker-512.png", sizes: "512x512", type: "image/png" },
        {
          src: "/icon-worker-512-maskable.png",
          sizes: "512x512",
          type: "image/png",
          purpose: "maskable",
        },
      ],
    });
  });

  it("ships every manifest icon as a real file in public/", () => {
    for (const icon of manifest().icons ?? []) {
      const file = join(process.cwd(), "public", icon.src.replace(/^\//, ""));
      expect(existsSync(file), `${icon.src} must exist in public/`).toBe(true);
    }
  });

  it("ships the 180×180 apple-icon for iOS Add-to-Home-Screen", () => {
    expect(existsSync(join(process.cwd(), "src", "app", "apple-icon.png"))).toBe(true);
  });
});
