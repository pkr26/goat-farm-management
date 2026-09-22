/**
 * Per-request nonce Content-Security-Policy (M-1, 2026-09-20 audit).
 *
 * Next.js 16's `proxy.ts` (the renamed `middleware.ts`) runs before every
 * matched page render: it mints a fresh nonce, sets the CSP on the REQUEST
 * headers — which is the signal Next.js uses to stamp the nonce onto its own
 * framework/bootstrap scripts automatically — and echoes the same policy on
 * the response. The former edge-supplied header carried
 * `script-src 'unsafe-inline'`; with the nonce, injected inline scripts are
 * blocked while Next's own still run.
 *
 * Deployment-owned S3/MinIO origins come from runtime env
 * (`GOATFARM_CSP_IMG_ORIGINS` / `GOATFARM_CSP_CONNECT_ORIGINS`), supplied by
 * Compose to this container exactly as they are to the edge entrypoint
 * (which validates them at boot). The parser in `lib/csp.ts` re-validates
 * before anything reaches a header.
 *
 * Nonce rendering requires dynamic pages; the root layout exports
 * `dynamic = "force-dynamic"` for that reason.
 */

import { NextRequest, NextResponse } from "next/server";

import { buildContentSecurityPolicy, parseCspOrigins } from "@/lib/csp";

export function proxy(request: NextRequest): NextResponse {
  // crypto.randomUUID() is unique per request; base64 matches the CSP nonce
  // grammar and the generator charset the builder enforces.
  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const contentSecurityPolicyHeaderValue = buildContentSecurityPolicy({
    nonce,
    isDev: process.env.NODE_ENV === "development",
    imgOrigins: parseCspOrigins(process.env.GOATFARM_CSP_IMG_ORIGINS),
    connectOrigins: parseCspOrigins(process.env.GOATFARM_CSP_CONNECT_ORIGINS),
  });

  const requestHeaders = new Headers(request.headers);
  // x-nonce lets server components nonce any hand-written <Script>; the CSP
  // on the request headers is what Next.js parses to nonce its own scripts.
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", contentSecurityPolicyHeaderValue);

  const response = NextResponse.next({
    request: {
      headers: requestHeaders,
    },
  });
  response.headers.set("Content-Security-Policy", contentSecurityPolicyHeaderValue);
  return response;
}

export const config = {
  matcher: [
    /*
     * Match page routes only: the backend-proxied paths (`/api`, `/healthz`,
     * `/readyz`) are JSON surfaces that need no CSP, and static assets
     * (`_next/static`, `_next/image`, favicon) are content-addressed.
     * The service worker and manifest must be served with their own cache
     * semantics (see next.config.ts), never with a nonce CSP page response.
     * Prefetches are skipped so <Link> hover/proxy loads don't mint nonces.
     */
    {
      source: "/((?!api|healthz|readyz|_next/static|_next/image|favicon.ico|sw.js|manifest.webmanifest|icon-worker-192.png|icon-worker-512.png).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
