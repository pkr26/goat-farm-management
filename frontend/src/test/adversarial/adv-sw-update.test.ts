/**
 * ADVERSARIAL AUDIT — service-worker update safety (2026-09-28 audit, H1/W7).
 *
 * public/sw.js is a static file with zero build-time checking: a regression
 * here wedges every field tablet at once. These pins lock the invariants the
 * 2026-09-28 audit found violated:
 *   H1  the shell must be network-first — a constant cache name must never
 *       pin a tablet to its install-time build;
 *   H1  a failed precache must fail the install, never activate a worker
 *       with no cached shell;
 *   W7  client-navigation RSC requests (?_rsc=…) are never intercepted or
 *       cached;
 *   —   /api stays network-only: no cache read, no cache write.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const SW_SOURCE = readFileSync(join(import.meta.dirname, "..", "..", "..", "public", "sw.js"), "utf8");

/** The raw block between two marker substrings, for scoped assertions. */
function between(startMarker: string, endMarker: string): string {
  const start = SW_SOURCE.indexOf(startMarker);
  const end = SW_SOURCE.indexOf(endMarker, start + startMarker.length);
  expect(start, `marker present: ${startMarker}`).toBeGreaterThan(-1);
  expect(end, `marker present after ${startMarker}: ${endMarker}`).toBeGreaterThan(start);
  return SW_SOURCE.slice(start, end);
}

describe("service worker update safety", () => {
  it("versions its cache name so an update can retire stale entries", () => {
    expect(SW_SOURCE).toMatch(/const CACHE = "herdly-worker-v\d+"/);
    // activate must only ever delete OTHER cache names.
    expect(SW_SOURCE).toContain("keys.filter((key) => key !== CACHE)");
  });

  it("keeps /api network-only — no cache read or write in that branch", () => {
    const apiBranch = between('url.pathname.startsWith("/api/")', 'url.pathname.startsWith("/_next/static/")');
    expect(apiBranch).toContain("event.respondWith(fetch(request))");
    expect(apiBranch).not.toContain("caches.");
  });

  it("serves the shell network-first: fetch before any cache read", () => {
    const guard = SW_SOURCE.indexOf('SHELL.includes(url.pathname) && url.search === ""');
    expect(guard).toBeGreaterThan(-1);
    const shellBranch = SW_SOURCE.slice(guard);
    const fetchPos = shellBranch.indexOf("fetch(request)");
    const cachePos = shellBranch.indexOf("caches.match(request)");
    expect(fetchPos).toBeGreaterThan(-1);
    expect(cachePos).toBeGreaterThan(fetchPos);
  });

  it("never intercepts client-navigation RSC requests (query-string guard)", () => {
    expect(SW_SOURCE).toContain('SHELL.includes(url.pathname) && url.search === ""');
  });

  it("fails the install when the precache fails (no swallowed rejection)", () => {
    const installBlock = between('addEventListener("install"', 'addEventListener("activate"');
    expect(installBlock).not.toContain(".catch(");
  });
});
