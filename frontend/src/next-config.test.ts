import { describe, expect, it } from "vitest";

import { backendRewrites } from "@/lib/backend-rewrites";

describe("backend rewrites", () => {
  it("proxies every root path emitted by the generated OpenAPI client", () => {
    const rewrites = backendRewrites("http://backend.test");

    expect(rewrites).toEqual([
      { source: "/healthz", destination: "http://backend.test/healthz" },
      { source: "/readyz", destination: "http://backend.test/readyz" },
      { source: "/api/:path*", destination: "http://backend.test/api/:path*" },
    ]);
  });
});
