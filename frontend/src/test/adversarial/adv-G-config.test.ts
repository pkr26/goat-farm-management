/**
 * ADVERSARIAL AUDIT G2/G3 — configuration & secret-exposure attacks (executed).
 *
 * G2  CSP: pin every hardening directive that exists today and document the
 *     known `script-src 'unsafe-inline'` gap (M-9) as an assertion, so any
 *     accidental CSP loosening (or the future nonce fix) is caught here.
 * G3  Secret exposure: scan every shipped source file for token/credential
 *     persistence patterns. The design invariant: the access token lives in
 *     memory only; localStorage carries exactly two keys (the farm selection
 *     and the UI language preference — both non-sensitive UI state);
 *     sessionStorage carries exactly the idempotency store.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const SRC_ROOT = join(import.meta.dirname, "..", "..");
const NEXT_CONFIG_SOURCE = readFileSync(join(SRC_ROOT, "..", "next.config.ts"), "utf8");

function allSourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry.startsWith(".") || entry === "generated") continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...allSourceFiles(full));
    else if (/\.(ts|tsx)$/.test(entry) && !/\.test\./.test(entry)) out.push(full);
  }
  return out;
}

describe("ADV G2: Content-Security-Policy hardening directives", () => {
  it("ships the baseline security header set on every response", () => {
    expect(NEXT_CONFIG_SOURCE).toContain('"X-Frame-Options", value: "DENY"');
    expect(NEXT_CONFIG_SOURCE).toContain('"X-Content-Type-Options", value: "nosniff"');
    expect(NEXT_CONFIG_SOURCE).toContain('"Referrer-Policy"');
    expect(NEXT_CONFIG_SOURCE).toContain('"Permissions-Policy"');
    expect(NEXT_CONFIG_SOURCE).toContain('"Cross-Origin-Opener-Policy"');
    expect(NEXT_CONFIG_SOURCE).toContain('"Cross-Origin-Resource-Policy"');
    expect(NEXT_CONFIG_SOURCE).toContain('"X-Permitted-Cross-Domain-Policies"');
    // CSP + HSTS are appended for production builds only (documented HMR
    // carve-out for dev): the PROD_ONLY_HEADERS list must hold exactly those.
    expect(NEXT_CONFIG_SOURCE).toContain("isProd ? [...SECURITY_HEADERS, ...PROD_ONLY_HEADERS] : SECURITY_HEADERS");
  });

  it("ATTEMPTED & REVERTED (M-9): nonce CSP blocked by Next 16 bootstrap scripts", () => {
    // A middleware nonce CSP was built and smoke-tested during this audit:
    // Next 16 (standalone) never applied the request-header nonce to its
    // inline bootstrap scripts — with 'strict-dynamic' real browsers would
    // block hydration. The verified posture keeps script-src 'unsafe-inline'
    // (pinned here) until the framework supports nonce'd RSC payloads; every
    // other directive is enforced and pinned below.
    expect(NEXT_CONFIG_SOURCE).toContain("script-src 'self' 'unsafe-inline'");
    expect(NEXT_CONFIG_SOURCE).toContain("object-src 'none'");
    expect(NEXT_CONFIG_SOURCE).toContain("base-uri 'self'");
    expect(NEXT_CONFIG_SOURCE).toContain("form-action 'self'");
    expect(NEXT_CONFIG_SOURCE).toContain("frame-ancestors 'none'");
    expect(NEXT_CONFIG_SOURCE).toContain("connect-src 'self'");
    expect(NEXT_CONFIG_SOURCE).toContain("max-age=63072000; includeSubDomains");
  });

  it("no reply-address smuggling: poweredByHeader stays off", () => {
    expect(NEXT_CONFIG_SOURCE).toContain("poweredByHeader: false");
  });
});

describe("ADV G3: secret & persistence surface scan", () => {
  const files = allSourceFiles(SRC_ROOT);

  it("scanned a meaningful corpus", () => {
    expect(files.length).toBeGreaterThan(50);
  });

  it("no credential-shaped literals anywhere in shipped source", () => {
    const patterns: Array<[string, RegExp]> = [
      ["AWS key", /AKIA[0-9A-Z]{16}/],
      ["private key block", /-----BEGIN [A-Z ]*PRIVATE KEY-----/],
      ["JWT literal", /eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\./],
      ["generic secret assignment", /(api[_-]?key|secret|password)\s*[:=]\s*["'][^"'{}$\s]{8,}["']/i],
    ];
    for (const file of files) {
      const text = readFileSync(file, "utf8");
      for (const [label, pattern] of patterns) {
        expect(text, `${file} contains a ${label}`).not.toMatch(pattern);
      }
    }
  });

  it("every localStorage write goes through a pinned allowlisted key", () => {
    // The app writes via constants, not literals — so the invariant is:
    // (a) each constant's value, and (b) no other setItem target. The
    // language preference joins the farm id as reviewed UI state (never a
    // credential: nothing session-bearing may enter this list).
    const authContext = readFileSync(join(SRC_ROOT, "lib", "auth-context.tsx"), "utf8");
    expect(authContext).toContain('FARM_STORAGE_KEY = "goatfarm.farmId"');
    const i18n = readFileSync(join(SRC_ROOT, "lib", "i18n", "index.tsx"), "utf8");
    expect(i18n).toContain('LANGUAGE_STORAGE_KEY = "herdly.language"');
    for (const file of files) {
      const text = readFileSync(file, "utf8");
      for (const match of text.matchAll(/\.setItem\(\s*([^,)]+)/g)) {
        expect(
          match[1].trim(),
          `${file} writes storage key ${match[1]}`,
        ).toMatch(/^(FARM_STORAGE_KEY|IDEMPOTENCY_SESSION_STORAGE_KEY|LANGUAGE_STORAGE_KEY)$/);
      }
    }
  });

  it("sessionStorage writes exactly the idempotency store key", () => {
    const idem = readFileSync(join(SRC_ROOT, "lib", "idempotent-request.ts"), "utf8");
    expect(idem).toContain('IDEMPOTENCY_SESSION_STORAGE_KEY = "goatfarm:idempotency:v1"');
    // The only sessionStorage API users are that module and its test-safe
    // availableSessionStorage() helper — nothing else may touch it.
    for (const file of files) {
      const text = readFileSync(file, "utf8");
      if (text.includes("sessionStorage")) {
        expect(file).toContain("idempotent-request");
      }
    }
  });

  it("the access token is never persisted (memory-only invariant)", () => {
    for (const file of files) {
      const text = readFileSync(file, "utf8");
      expect(text, `${file} persists a token`).not.toMatch(
        /localStorage\.setItem\([^)]*(token|Token)/,
      );
      expect(text, `${file} persists a token`).not.toMatch(
        /sessionStorage\.setItem\([^)]*(token|Token)/,
      );
    }
  });
});
