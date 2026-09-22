import type { MetadataRoute } from "next";

/** Worker-tablet PWA manifest (ITEM 2 Phase 2, 2026-09-21 playbook). The
 * tablet's home-screen icon opens straight onto the duty board. */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Herdly Worker",
    short_name: "Herdly",
    description: "Daily duty board for farm workers",
    start_url: "/worker",
    display: "standalone",
    background_color: "#ffffff",
    theme_color: "#166534",
    icons: [
      { src: "/icon-worker-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icon-worker-512.png", sizes: "512x512", type: "image/png" },
    ],
  };
}
