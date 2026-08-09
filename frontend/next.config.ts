/** Baseline hardening headers on every response. CSP and HSTS are enforced in
 *  production builds only: Next dev HMR needs 'unsafe-eval' and a policy lax
 *  enough for it would be security theatre. */
const SECURITY_HEADERS = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Permitted-Cross-Domain-Policies", value: "none" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  { key: "Cross-Origin-Resource-Policy", value: "same-origin" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

/** Production Content-Security-Policy. script-src keeps 'unsafe-inline'
 *  because the App Router streams its RSC/hydration payload in inline
 *  <script> tags and we don't run nonce middleware yet — tightening to
 *  nonces is the documented next step. Everything else is 'self'. */
const PRODUCTION_CSP = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self' data:",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const PROD_ONLY_HEADERS = [
  { key: "Content-Security-Policy", value: PRODUCTION_CSP },
  {
    key: "Strict-Transport-Security",
    value: "max-age=63072000; includeSubDomains",
  },
];

const isProd = process.env.NODE_ENV === "production";

const nextConfig = {
  poweredByHeader: false,
  // Produce the minimal Node server required by dynamic routes, rewrites and
  // response headers. This application is not a static export.
  output: "standalone",
  // Dev proxy: the SPA calls same-origin /api/* and Next forwards to the
  // FastAPI backend — no CORS friction, and the refresh cookie stays
  // first-party.
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.BACKEND_URL ?? "http://localhost:8000"}/api/:path*`,
      },
    ];
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
