/**
 * Behavioural tests for the nonce-CSP proxy (M-1, 2026-09-20 audit). The
 * proxy sat at 0% coverage, pinned only by source-text greps (2026-09-28
 * audit, T2) — these exercise it end to end: a fresh base64 nonce is minted
 * per request onto BOTH the request headers (the signal Next.js uses to
 * nonce its own bootstrap scripts) and the response CSP, and the matcher
 * keeps API/health/static/service-worker/manifest/icon paths untouched.
 */

import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";

import { config, proxy } from "./proxy";

const NONCE_GRAMMAR = /^[A-Za-z0-9+/]+={0,2}$/;

/** Next encodes `NextResponse.next({ request: { headers } })` overrides as
 *  x-middleware-request-<key> on the response (spec-extension/response.js). */
function requestHeader(response: ReturnType<typeof proxy>, key: string): string | null {
  return response.headers.get(`x-middleware-request-${key}`);
}

describe("proxy nonce CSP", () => {
  it("mints a base64 nonce onto the request headers and the response CSP", () => {
    const response = proxy(new NextRequest("https://example.test/worker"));

    const requestNonce = requestHeader(response, "x-nonce");
    expect(requestNonce).toMatch(NONCE_GRAMMAR);

    const responseCsp = response.headers.get("Content-Security-Policy");
    expect(responseCsp).toBeDefined();
    // The SAME nonce rides the response policy — a mismatch means Next's
    // bootstrap scripts carry a nonce the policy never authorized.
    expect(responseCsp).toContain(`'nonce-${requestNonce}'`);
    const scriptSrc = responseCsp?.split("; ").find((d) => d.startsWith("script-src "));
    expect(scriptSrc).toContain("'strict-dynamic'");
    // The M-1 property: injected inline scripts are blocked (style-src keeps
    // its documented 'unsafe-inline'; the script policy never carries one).
    expect(scriptSrc).not.toContain("'unsafe-inline'");

    // The request-side CSP is what Next parses to stamp its own scripts.
    expect(requestHeader(response, "content-security-policy")).toBe(responseCsp);
    const overridden = response.headers.get("x-middleware-override-headers") ?? "";
    expect(overridden.split(",")).toEqual(
      expect.arrayContaining(["x-nonce", "content-security-policy"]),
    );
  });

  it("mints a fresh nonce per request — a reused nonce defeats the policy", () => {
    const first = requestHeader(proxy(new NextRequest("https://example.test/worker")), "x-nonce");
    const second = requestHeader(proxy(new NextRequest("https://example.test/login")), "x-nonce");
    expect(first).toMatch(NONCE_GRAMMAR);
    expect(second).toMatch(NONCE_GRAMMAR);
    expect(first).not.toBe(second);
  });

  it("keeps the production policy free of dev-only unsafe-eval", () => {
    // vitest runs with NODE_ENV=test, so isDev is false here — the dev-only
    // 'unsafe-eval' arm must not leak into the policy the proxy emits.
    const csp = proxy(new NextRequest("https://example.test/worker")).headers.get(
      "Content-Security-Policy",
    );
    expect(csp).not.toContain("'unsafe-eval'");
  });
});

describe("proxy matcher", () => {
  // Next compiles the matcher source through path-to-regexp; for this
  // pattern (leading slash + negative lookahead + trailing .*) a RegExp
  // evaluates the same accept/reject decision per path.
  const source = config.matcher[0]?.source ?? "";
  const matches = (path: string) => new RegExp(`^${source}$`).test(path);

  it("matches page routes", () => {
    for (const path of ["/", "/worker", "/worker/login", "/dashboard", "/login"]) {
      expect(matches(path), path).toBe(true);
    }
  });

  it("leaves API, health, static, service-worker, manifest and icon paths untouched", () => {
    for (const path of [
      "/api/auth/login",
      "/api/tasks",
      // The bare /api root is anchored too: it is a backend surface, not a page.
      "/api",
      "/healthz",
      "/readyz",
      "/_next/static/chunks/main.js",
      "/_next/image/photo",
      "/favicon.ico",
      // The app-router icon (src/app/icon.svg) is a content-addressed asset.
      "/icon.svg",
      // The service worker and manifest must keep their own cache semantics
      // (next.config.ts), never a nonce-CSP page response.
      "/sw.js",
      "/manifest.webmanifest",
      "/icon-worker-192.png",
      "/icon-worker-512.png",
      "/icon-worker-512-maskable.png",
    ]) {
      expect(matches(path), path).toBe(false);
    }
  });

  it("still matches a page route that merely starts with api", () => {
    // The lookahead is anchored (api(?:/|$)): an unanchored one would swallow
    // any future page route with an "api" prefix (2026-09-28 audit).
    expect(matches("/api-docs")).toBe(true);
    expect(matches("/apiary")).toBe(true);
  });

  it("skips prefetch requests so hover loads never mint nonces", () => {
    expect(config.matcher[0]?.missing).toEqual([
      { type: "header", key: "next-router-prefetch" },
      { type: "header", key: "purpose", value: "prefetch" },
    ]);
  });
});
