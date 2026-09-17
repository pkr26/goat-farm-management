import { describe, expect, it } from "vitest";

import { assertSafeBackendUrl, backendRewrites } from "@/lib/backend-rewrites";

describe("backend rewrites", () => {
  it("proxies every root path emitted by the generated OpenAPI client", () => {
    const rewrites = backendRewrites("http://backend:8000");

    expect(rewrites).toEqual([
      { source: "/healthz", destination: "http://backend:8000/healthz" },
      { source: "/readyz", destination: "http://backend:8000/readyz" },
      { source: "/api/:path*", destination: "http://backend:8000/api/:path*" },
    ]);
  });
});

describe("BACKEND_URL transport guard", () => {
  it.each([
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://[::1]:9000",
    "http://backend:8000",
    "http://api:80",
    "https://backend:8443",
    "https://localhost:8443",
  ])("accepts %s", (url) => {
    expect(() => assertSafeBackendUrl(url)).not.toThrow();
  });

  it.each([
    ["http://api.example.com", "plaintext to a dotted public hostname"],
    ["http://10.0.0.5:8000", "plaintext to a reachable IP"],
    ["http://[::2]:9000", "plaintext to a non-loopback IPv6 literal"],
    // INFRA-2 (2026-09-16): https must not bypass the host rules — the proxy
    // forwards the bearer token and refresh cookie, so a public target is an
    // exfil path over TLS exactly as over plaintext (RT-R-7 follow-up).
    ["https://api.example.com", "https to a dotted public hostname"],
    ["https://10.0.0.5:8443", "https to a reachable IP"],
    ["https://evil.example", "https attacker hostname"],
    ["ftp://backend:8000", "non-http(s) scheme"],
    ["not a url", "not absolute"],
  ])("rejects %s (%s)", (url) => {
    expect(() => assertSafeBackendUrl(url)).toThrow(/BACKEND_URL/);
  });

  it("guards the rewrites build path itself", () => {
    expect(() => backendRewrites("http://10.0.0.5:8000")).toThrow(
      /session bearer token and refresh cookie/,
    );
  });
});
