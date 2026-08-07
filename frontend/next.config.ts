/** Baseline hardening headers on every response. No CSP on purpose: a policy
 *  lax enough for Next dev HMR ('unsafe-eval'/'unsafe-inline') is security
 *  theatre, so CSP is deferred to a production-only tightening pass. */
const SECURITY_HEADERS = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

const nextConfig = {
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
    return [{ source: "/:path*", headers: SECURITY_HEADERS }];
  },
};

export default nextConfig;
