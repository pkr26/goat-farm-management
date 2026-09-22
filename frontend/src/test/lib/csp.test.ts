/**
 * Unit tests for the per-request nonce CSP builder (M-1, 2026-09-20).
 *
 * These pin the property the former edge header could not provide: injected
 * inline scripts are blocked (no 'unsafe-inline' in script-src) while
 * Next.js's own nonce-stamped bootstrap still runs, and untrusted origin
 * env values can never smuggle header syntax into the policy.
 */

import { describe, expect, it } from "vitest";

import { buildContentSecurityPolicy, parseCspOrigins } from "@/lib/csp";

const NONCE = "Zm9vYmFyYmF6PT4mXyE="; // base64, 20 chars

describe("parseCspOrigins", () => {
  it("accepts valid HTTPS origins and loopback exceptions", () => {
    expect(parseCspOrigins("https://bucket.s3.example.test")).toEqual([
      "https://bucket.s3.example.test",
    ]);
    expect(
      parseCspOrigins("https://a.example.test https://b.example.test:8443"),
    ).toEqual(["https://a.example.test", "https://b.example.test:8443"]);
    expect(parseCspOrigins("http://localhost:9000 http://127.0.0.1 http://[::1]:9000")).toEqual([
      "http://localhost:9000",
      "http://127.0.0.1",
      "http://[::1]:9000",
    ]);
  });

  it("drops paths, queries, credentials and non-HTTPS schemes", () => {
    expect(parseCspOrigins("https://bucket.test/path")).toEqual([]);
    expect(parseCspOrigins("https://bucket.test?x=1")).toEqual([]);
    expect(parseCspOrigins("https://user:pw@bucket.test")).toEqual([]);
    expect(parseCspOrigins("http://bucket.test")).toEqual([]);
    expect(parseCspOrigins("ftp://bucket.test")).toEqual([]);
  });

  it("rejects comma lists, injection attempts and non-ASCII wholesale", () => {
    // CSV spelling means the operator misunderstood the variable.
    expect(parseCspOrigins("https://one.test,https://two.test")).toEqual([]);
    // Header smuggling: newlines, CR, semicolons, quotes must never pass.
    expect(parseCspOrigins("https://bucket.test\n")).toEqual([]);
    expect(parseCspOrigins("https://bucket.test\r\nX-Injected: 1")).toEqual([]);
    expect(parseCspOrigins("https://bucket.test; script-src 'none'")).toEqual([]);
    expect(parseCspOrigins("https://bucket.test\u00e9")).toEqual([]);
    // Wildcards are not origin literals here (matches the edge validator).
    expect(parseCspOrigins("https://*.bucket.test")).toEqual([]);
    expect(parseCspOrigins("https://bucket.test:99999")).toEqual([]);
  });

  it("treats empty and whitespace-only values as no origins", () => {
    expect(parseCspOrigins(undefined)).toEqual([]);
    expect(parseCspOrigins("")).toEqual([]);
    expect(parseCspOrigins("   ")).toEqual([]);
  });

  it("keeps only the valid entries from a mixed list", () => {
    expect(parseCspOrigins("https://good.test https://bad.test/path")).toEqual([
      "https://good.test",
    ]);
  });
});

describe("buildContentSecurityPolicy", () => {
  it("emits a single-line policy with the nonce and strict-dynamic in script-src", () => {
    const policy = buildContentSecurityPolicy({ nonce: NONCE, isDev: false });
    expect(policy).not.toContain("\n");
    const scriptSrc = policy
      .split("; ")
      .find((directive) => directive.startsWith("script-src "));
    expect(scriptSrc).toBe(`script-src 'self' 'nonce-${NONCE}' 'strict-dynamic'`);
    expect(scriptSrc).not.toContain("'unsafe-inline'");
    expect(policy).toContain("style-src 'self' 'unsafe-inline'");
    expect(policy).toContain("object-src 'none'");
    expect(policy).toContain("base-uri 'self'");
    expect(policy).toContain("form-action 'self'");
    expect(policy).toContain("frame-ancestors 'none'");
  });

  it("pins the PWA directives: same-origin service worker and web app manifest", () => {
    // ITEM 2: the worker tablet registers /sw.js and loads
    // /manifest.webmanifest — both must be same-origin only, in every
    // environment, so a regression dropping either directive fails here.
    for (const isDev of [true, false]) {
      const policy = buildContentSecurityPolicy({ nonce: NONCE, isDev });
      expect(policy).toContain("worker-src 'self'");
      expect(policy).toContain("manifest-src 'self'");
    }
  });

  it("adds unsafe-eval only for development", () => {
    expect(buildContentSecurityPolicy({ nonce: NONCE, isDev: true })).toContain(
      "script-src 'self' 'nonce-" + NONCE + "' 'strict-dynamic' 'unsafe-eval'",
    );
    expect(buildContentSecurityPolicy({ nonce: NONCE, isDev: false })).not.toContain(
      "unsafe-eval",
    );
  });

  it("appends validated deployment origins to img-src and connect-src only", () => {
    const policy = buildContentSecurityPolicy({
      nonce: NONCE,
      isDev: false,
      imgOrigins: ["https://minio.example.com"],
      connectOrigins: ["https://minio.example.com"],
    });
    expect(policy).toContain("img-src 'self' data: blob: https://minio.example.com");
    expect(policy).toContain("connect-src 'self' https://minio.example.com");
    // Without origins the directives stay self-contained (no trailing space).
    const bare = buildContentSecurityPolicy({ nonce: NONCE, isDev: false });
    expect(bare).toContain("img-src 'self' data: blob:");
    expect(bare).toContain("connect-src 'self'");
    expect(bare).not.toContain("blob: ");
  });

  it("refuses a nonce that could smuggle header syntax", () => {
    for (const evil of ["a; script-src 'none'", "x' style-src *", "short", ""]) {
      expect(() =>
        buildContentSecurityPolicy({ nonce: evil, isDev: false }),
      ).toThrow(/nonce/);
    }
  });

  it("never places an unvalidated origin into the policy", () => {
    // parseCspOrigins already dropped the bad entry; the builder receives
    // only what survived validation.
    const policy = buildContentSecurityPolicy({
      nonce: NONCE,
      isDev: false,
      imgOrigins: parseCspOrigins("https://good.test https://evil.test/paths"),
      connectOrigins: parseCspOrigins("https://good.test,https://csv.test"),
    });
    expect(policy).toContain("https://good.test");
    expect(policy).not.toContain("evil.test");
    expect(policy).not.toContain("csv.test");
  });
});
