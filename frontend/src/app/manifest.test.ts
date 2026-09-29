/**
 * Worker-tablet PWA manifest (2026-09-28 audit, T2 — the file sat at 0%).
 * Pins the shape, including the tablet's straight-to-the-board start_url and
 * the W11 additions from the same audit: explicit id/scope, a maskable icon
 * purpose entry, and a background_color that is the app canvas token
 * (--background, oklch(0.988 0.005 95)) resolved to the hex the manifest
 * spec requires. The remaining W11 gap is `apple-icon`: iPads ignore
 * manifest icons for Add-to-Home-Screen and need a real 180×180 PNG at
 * src/app/apple-icon.png — pending an actual design asset; do not fabricate
 * a placeholder PNG.
 */

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
          src: "/icon-worker-512.png",
          sizes: "512x512",
          type: "image/png",
          purpose: "maskable",
        },
      ],
    });
  });
});
