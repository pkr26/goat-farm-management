import { fileURLToPath } from "node:url";

import { backendRewrites } from "./src/lib/backend-rewrites";

/** Baseline hardening headers on every response. HSTS is production-only.
 *
 * The public Compose edge supplies CSP at runtime. A release frontend image
 * cannot know a deployment's public S3/MinIO origin, and baking one into this
 * config would make the documented generic registry artifact block direct
 * screening uploads. The edge validates then renders the exact origin list.
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
    ];
  },
};

export default nextConfig;
