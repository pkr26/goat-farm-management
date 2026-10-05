import { randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";
import type { NextConfig } from "next";

import { backendRewrites } from "./src/lib/backend-rewrites";
import { assertImageDecodeSafetyForBuild } from "./src/lib/image-deps-guard";

// Deploy-time sharp/libheif gate (libheif/AVIF decode trap): evaluated at
// configuration phase so CI's `pnpm build` and the Docker builder stage both refuse
// an unsafe next+sharp combination before it can become an artifact. Dev and
// test contexts only warn. Contract details: src/lib/image-deps-guard.ts.

/**
 * Baseline security headers for every response; HSTS applies in production. CSP
 * lives in src/proxy.ts because its nonce must exist before server rendering.
 * Runtime GOATFARM_CSP_IMG_ORIGINS and GOATFARM_CSP_CONNECT_ORIGINS configure
 * deployment-specific storage endpoints. The edge adds the same baseline headers to
 * its own responses.
 */
const SECURITY_HEADERS = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Permitted-Cross-Domain-Policies", value: "none" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  { key: "Cross-Origin-Resource-Policy", value: "same-origin" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

const isProduction = process.env.NODE_ENV === "production";

const PROD_ONLY_HEADERS = [
  {
    key: "Strict-Transport-Security",
    value: "max-age=63072000; includeSubDomains",
  },
];

const projectRoot = fileURLToPath(new URL(".", import.meta.url));

// Embedded in compiled HTML, so shell refreshes can distinguish a real
// deployment from another request of the same build. The server's runtime
// config evaluation cannot change the already compiled public constant.
const workerBuildId = randomUUID();

const nextConfig = {
  generateBuildId: async () => workerBuildId,
  env: { NEXT_PUBLIC_HERDLY_BUILD_ID: workerBuildId },
  poweredByHeader: false,
  turbopack: { root: projectRoot },
  // Produce the minimal Node server required by dynamic routes, rewrites and
  // response headers. This application is not a static export.
  output: "standalone",
  // Same-origin backend proxy: application routes live under /api/*, while
  // the OpenAPI service probes intentionally live at /healthz and /readyz.
  // Proxy all three surfaces so every generated client works in both dev and
  // the standalone production server without CORS or cookie differences.
  async rewrites() {
    return backendRewrites(process.env.BACKEND_URL ?? "http://localhost:8000");
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: isProduction ? [...SECURITY_HEADERS, ...PROD_ONLY_HEADERS] : SECURITY_HEADERS,
      },
      // The service worker and PWA manifest must always be revalidated — a
      // cached stale sw.js is the one deployment update path that can wedge
      // every tablet at once.
      {
        source: "/sw.js",
        headers: [
          { key: "Cache-Control", value: "no-cache, no-store, must-revalidate" },
        ],
      },
      {
        source: "/manifest.webmanifest",
        headers: [{ key: "Cache-Control", value: "no-cache" }],
      },
    ];
  },
} satisfies NextConfig;

export default function configureNext(phase: string) {
  // NEXT_PHASE is populated after Next first loads this configuration. Use
  // the documented phase argument so the initial build gate fails closed.
  assertImageDecodeSafetyForBuild(phase);
  return nextConfig;
}
