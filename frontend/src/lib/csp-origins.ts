/** Build-time CSP origin additions for direct-to-S3 screening traffic. */

/**
 * The disease-screening walkthrough uploads photo bytes straight to a
 * presigned S3 PUT URL and renders presigned S3 GET URLs as <img src>. Both
 * hosts are cross-origin in every real deployment (AWS S3 or a MinIO
 * endpoint), while the production CSP pins `connect-src 'self'` and
 * `img-src 'self' data:` — without these additions the browser blocks the
 * feature end-to-end (upload AND display). Origins are supplied at BUILD
 * time (the standalone server evaluates next.config headers() during
 * `next build`), comma-separated, e.g.
 *
 *   GOATFARM_CSP_CONNECT_ORIGINS=https://bucket.s3.ap-south-1.amazonaws.com
 *   GOATFARM_CSP_IMG_ORIGINS=https://bucket.s3.ap-south-1.amazonaws.com
 *
 * Malformed entries fail the build fail-closed (same posture as
 * assertSafeBackendUrl): a typo'd CSP origin must not silently disable the
 * screening feature or ship a policy nobody reviewed.
 */
export function parseCspExtraOrigins(raw: string | undefined): string[] {
  if (!raw) return [];
  const origins: string[] = [];
  for (const candidate of raw.split(",")) {
    const trimmed = candidate.trim();
    if (!trimmed) continue;
    let parsed: URL;
    try {
      parsed = new URL(trimmed);
    } catch {
      throw new Error(
        `CSP extra origins must be absolute http(s) URLs (got ${JSON.stringify(trimmed)}); ` +
          "the browser would otherwise silently ignore the entry and block the " +
          "screening upload/display traffic it was added for",
      );
    }
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      throw new Error(
        `CSP extra origins must use http(s) (got ${JSON.stringify(trimmed)})`,
      );
    }
    origins.push(parsed.origin);
  }
  return [...new Set(origins)];
}
