import type { MetadataRoute } from "next";

/** Worker-tablet PWA manifest (ITEM 2 Phase 2, 2026-09-21 playbook). The
 * tablet's home-screen icon opens straight onto the duty board. */
export default function manifest(): MetadataRoute.Manifest {
  return {
    // Explicit id pins the installed app's identity to the board, so a future
    // start_url change (tracking params, a different landing) never orphans
    // an install; explicit scope documents the default the spec would derive
    // (2026-09-28 audit, W11).
    id: "/worker",
    name: "Herdly Worker",
    short_name: "Herdly",
    description: "Daily duty board for farm workers",
    start_url: "/worker",
    scope: "/",
    display: "standalone",
    // The manifest spec requires a hex color (no oklch/var()): this is the
    // app canvas token --background (globals.css, oklch(0.988 0.005 95))
    // resolved to sRGB, so the launch splash matches the themed canvas
    // instead of a hardcoded white.
    background_color: "#FCFBF7",
    theme_color: "#166534",
    icons: [
      { src: "/icon-worker-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icon-worker-512.png", sizes: "512x512", type: "image/png" },
      // Android adaptive launchers crop to the central 66dp-of-108dp safe
      // circle: this dedicated variant keeps the whole glyph inside that
      // zone (W11; generated + pixel-verified by scripts/generate-worker-icons.mjs).
      {
        src: "/icon-worker-512-maskable.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
  };
}
