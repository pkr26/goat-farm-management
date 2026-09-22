/**
 * ADVERSARIAL AUDIT G2/G3 — configuration & secret-exposure attacks (executed).
 *
 * G2  CSP: pin the per-request nonce policy delivered by src/proxy.ts (M-1,
 *     2026-09-20): script-src carries a nonce + 'strict-dynamic' and never
 *     'unsafe-inline'. Deployment S3 origins arrive as runtime env validated
 *     at the edge; the templates themselves carry no CSP at all.
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
const COMPOSE_SOURCE = readFileSync(join(SRC_ROOT, "..", "..", "docker-compose.yml"), "utf8");
const EDGE_ENTRYPOINT_SOURCE = readFileSync(
  join(SRC_ROOT, "..", "..", "docker", "edge-entrypoint.sh"),
  "utf8",
);
const PRODUCTION_EDGE_TEMPLATE_SOURCE = readFileSync(
  join(SRC_ROOT, "..", "..", "docker", "edge-proxy.production.conf.template"),
  "utf8",
);
const DEV_EDGE_TEMPLATE_SOURCE = readFileSync(
  join(SRC_ROOT, "..", "..", "docker", "edge-proxy.dev.conf.template"),
  "utf8",
);
const PROXY_SOURCE = readFileSync(join(SRC_ROOT, "proxy.ts"), "utf8");
const CSP_LIB_SOURCE = readFileSync(join(SRC_ROOT, "lib", "csp.ts"), "utf8");
const ROOT_LAYOUT_SOURCE = readFileSync(join(SRC_ROOT, "app", "layout.tsx"), "utf8");

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
    // HSTS is appended for production builds only. CSP must never appear here:
    // a nonce policy must be minted per request at the render boundary
    // (src/proxy.ts), which static build-time headers cannot do.
    expect(NEXT_CONFIG_SOURCE).toContain("isProd ? [...SECURITY_HEADERS, ...PROD_ONLY_HEADERS] : SECURITY_HEADERS");
    expect(NEXT_CONFIG_SOURCE).not.toContain('key: "Content-Security-Policy"');
  });

  it("delivers a strict per-request nonce CSP and never 'unsafe-inline' scripts", () => {
    // The policy builder: nonce + strict-dynamic in script-src, with the dev
    // 'unsafe-eval' (React debug tooling) as the ONLY relaxation. No CODE
    // line that builds script-src may ever carry the inline escape hatch
    // (comment lines are excluded from the scan).
    expect(CSP_LIB_SOURCE).toContain("'strict-dynamic'");
    expect(CSP_LIB_SOURCE).toContain("'nonce-${nonce}'");
    const codeLines = CSP_LIB_SOURCE.split("\n").filter((line) => !/^\s*(\*|\/\/|\/\*)/.test(line));
    for (const line of codeLines) {
      if (/script/i.test(line)) {
        expect(line, `script line carries unsafe-inline: ${line}`).not.toContain("'unsafe-inline'");
      }
    }
    expect(CSP_LIB_SOURCE).toContain("'unsafe-eval'");
    expect(CSP_LIB_SOURCE).toContain("object-src 'none'");
    expect(CSP_LIB_SOURCE).toContain("base-uri 'self'");
    expect(CSP_LIB_SOURCE).toContain("form-action 'self'");
    expect(CSP_LIB_SOURCE).toContain("frame-ancestors 'none'");
    // The worker tablet PWA registers /sw.js and /manifest.webmanifest —
    // both directives must stay pinned same-origin in the source.
    expect(CSP_LIB_SOURCE).toContain("worker-src 'self'");
    expect(CSP_LIB_SOURCE).toContain("manifest-src 'self'");
    // Camera previews use same-page URL.createObjectURL() values before an
    // image reaches S3, so blob: is a narrowly scoped image-only source.
    expect(CSP_LIB_SOURCE).toContain("img-src 'self' data: blob:");
    // The proxy wires the nonce onto BOTH the request headers (the signal
    // Next.js uses to stamp the nonce on its own scripts) and the response.
    expect(PROXY_SOURCE).toContain('requestHeaders.set("x-nonce", nonce)');
    expect(PROXY_SOURCE).toContain('requestHeaders.set("Content-Security-Policy"');
    expect(PROXY_SOURCE).toContain('response.headers.set("Content-Security-Policy"');
    expect(PROXY_SOURCE).toContain("crypto.randomUUID()");
    // Nonce rendering needs dynamic pages: the root layout must force it.
    expect(ROOT_LAYOUT_SOURCE).toContain('export const dynamic = "force-dynamic"');
  });

  it("routes runtime S3 origins through env and keeps them out of the templates", () => {
    // The frontend container receives the same validated values the edge
    // entrypoint checks at boot; the proxy re-validates before a header sees
    // them.
    expect(COMPOSE_SOURCE).toContain("GOATFARM_CSP_CONNECT_ORIGINS");
    expect(COMPOSE_SOURCE).toContain("GOATFARM_CSP_IMG_ORIGINS");
    expect(PROXY_SOURCE).toContain("GOATFARM_CSP_IMG_ORIGINS");
    expect(PROXY_SOURCE).toContain("GOATFARM_CSP_CONNECT_ORIGINS");
    expect(EDGE_ENTRYPOINT_SOURCE).toContain("validate_csp_sources");
    expect(EDGE_ENTRYPOINT_SOURCE).toContain("screening is enabled but");
    expect(EDGE_ENTRYPOINT_SOURCE).toContain("unsafe for an nginx header");
    expect(EDGE_ENTRYPOINT_SOURCE).toContain("edge-proxy.template");
    // No CSP is emitted by either nginx template (nonce policies cannot be
    // produced there) and no unsafe-inline escape hatch survives anywhere.
    const templates = DEV_EDGE_TEMPLATE_SOURCE + PRODUCTION_EDGE_TEMPLATE_SOURCE;
    expect(templates).not.toContain("Content-Security-Policy");
    expect(templates).not.toContain("unsafe-inline");
    // The edge still covers its OWN generated responses (429s, error pages).
    expect(templates).toContain("add_header X-Content-Type-Options nosniff always;");
    expect(templates).toContain("add_header Referrer-Policy no-referrer always;");
    expect(PRODUCTION_EDGE_TEMPLATE_SOURCE).toContain("add_header Strict-Transport-Security");
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
    const workerLayout = readFileSync(
      join(SRC_ROOT, "app", "worker", "layout.tsx"),
      "utf8",
    );
    expect(workerLayout).toContain('TABLET_FARM_STORAGE_KEY = "herdly.tabletFarm"');
    const offlineQueue = readFileSync(join(SRC_ROOT, "lib", "offline-queue.ts"), "utf8");
    expect(offlineQueue).toContain('OFFLINE_QUEUE_STORAGE_KEY = "goatfarm:offlineQueue:v1"');
    const i18n = readFileSync(join(SRC_ROOT, "lib", "i18n", "index.tsx"), "utf8");
    expect(i18n).toContain('LANGUAGE_STORAGE_KEY = "herdly.language"');
    for (const file of files) {
      const text = readFileSync(file, "utf8");
      for (const match of text.matchAll(/\.setItem\(\s*([^,)]+)/g)) {
        expect(
          match[1].trim(),
          `${file} writes storage key ${match[1]}`,
        ).toMatch(
          /^(FARM_STORAGE_KEY|TABLET_FARM_STORAGE_KEY|OFFLINE_QUEUE_STORAGE_KEY|IDEMPOTENCY_SESSION_STORAGE_KEY|LANGUAGE_STORAGE_KEY)$/,
        );
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
