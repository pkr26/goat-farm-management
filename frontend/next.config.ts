import { fileURLToPath } from "node:url";

import { backendRewrites } from "./src/lib/backend-rewrites";

/** Baseline hardening headers on every response. HSTS is production-only.
 *
 * CSP is NOT here: src/proxy.ts emits a per-request nonce policy (M-1,
 * 2026-09-20). A nonce must be minted before render so Next.js can stamp it
 * on its own scripts, which only the proxy/render boundary can do — these
 * static headers cannot. Deployment S3/MinIO origins reach the proxy as
 * runtime env (GOATFARM_CSP_IMG_ORIGINS / GOATFARM_CSP_CONNECT_ORIGINS),
 * keeping the generic registry image deployment-neutral. The edge re-adds
 * identical baseline headers on its own generated responses.
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

const isProd = process.env.NODE_ENV === "production";

const PROD_ONLY_HEADERS = [
  {
    key: "Strict-Transport-Security",
    value: "max-age=63072000; includeSubDomains",
  },
];

const projectRoot = fileURLToPath(new URL(".", import.meta.url));

const nextConfig = {
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
        headers: isProd ? [...SECURITY_HEADERS, ...PROD_ONLY_HEADERS] : SECURITY_HEADERS,
      },
      // The service worker and PWA manifest must always be revalidated — a
      // cached stale sw.js is the one deployment update path that can wedge
      // every tablet at once (ITEM 2 Phase 2).
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
};

export default nextConfig;
