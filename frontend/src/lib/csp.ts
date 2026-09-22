/** Nonce-based Content-Security-Policy construction (M-1, 2026-09-20 audit).
 *
 * The policy replaces the former edge header whose script allow-list carried
 * the inline escape hatch: a per-request nonce (see `src/proxy.ts`) lets
 * Next.js's own bootstrap scripts run while any injected inline script is
 * blocked. Deployment-owned S3/MinIO origins arrive as runtime env
 * (`GOATFARM_CSP_IMG_ORIGINS`, `GOATFARM_CSP_CONNECT_ORIGINS`) so the
 * generic registry image stays deployment-neutral; the edge entrypoint
 * validates the same values at boot, and this parser re-validates before
 * anything is spliced into a header.
 */

/** ASCII DNS name (one or more labels; URL lowercases before we see it). */
const CSP_HOSTNAME_RE =
  /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$/i;

/** Bracketed IPv6 literal grammar (Node keeps the brackets in hostname). */
const CSP_IPV6_RE = /^\[[0-9A-Fa-f:.]{2,45}\]$/;

function isLoopbackHttpOrigin(url: URL): boolean {
  return (
    url.protocol === "http:" &&
    (url.hostname === "localhost" || url.hostname === "127.0.0.1" || url.hostname === "[::1]")
  );
}

function parseOneOrigin(token: string): string | null {
  let url: URL;
  try {
    url = new URL(token);
  } catch {
    return null;
  }
  // Exactly an origin: WHATWG URL keeps pathname "/" for a bare authority,
  // so anything with a path, query, fragment, or embedded credentials is a
  // URL and is rejected.
  if (url.pathname !== "/" || url.search !== "" || url.hash !== "") return null;
  if (url.username !== "" || url.password !== "") return null;
  // HTTPS everywhere, except the documented loopback dev exception.
  if (url.protocol !== "https:" && !isLoopbackHttpOrigin(url)) return null;
  const host = url.hostname;
  if (host.startsWith("[")) {
    if (!CSP_IPV6_RE.test(host)) return null;
  } else if (!CSP_HOSTNAME_RE.test(host)) {
    return null;
  }
  if (url.port !== "") {
    const port = Number(url.port);
    if (!Number.isInteger(port) || port <= 0 || port > 65535) return null;
  }
  return token;
}

/**
 * Validate one space-separated origin list, mirroring the grammar
 * `docker/edge-entrypoint.sh` enforces at edge boot.
 *
 * Returns only entries that pass; anything ambiguous (commas, paths, queries,
 * credentials, non-ASCII, control characters, non-HTTPS non-loopback schemes)
 * is dropped rather than trusted inside a response header. A value that is
 * entirely invalid therefore yields an empty list, never a malformed policy.
 */
export function parseCspOrigins(value: string | undefined): string[] {
  if (!value) return [];
  // Reject the whole list on commas early: CSP grammar is space-separated,
  // and a CSV spelling means the operator misunderstood the variable.
  if (value.includes(",")) return [];
  // The header must stay single-line printable ASCII — check the RAW value,
  // before trimming: a trailing newline or tab is rejected wholesale (the
  // edge validator's sentinel rule), never silently normalized away.
  if (!/^[\x20-\x7e]*$/.test(value)) return [];
  const trimmed = value.trim();
  if (!trimmed) return [];
  return trimmed
    .split(/ +/)
    .map((token) => parseOneOrigin(token))
    .filter((origin): origin is string => origin !== null);
}

export interface CspOptions {
  nonce: string;
  isDev: boolean;
  imgOrigins?: string[];
  connectOrigins?: string[];
}

/**
 * Build the single-line CSP header value. Mirrors the directives the former
 * edge header enforced, except `script-src` now carries the nonce plus
 * `'strict-dynamic'` instead of `'unsafe-inline'`, and development adds
 * `'unsafe-eval'` (React's debug tooling needs it; production never does).
 * `style-src` keeps `'unsafe-inline'`: React style attributes and Tailwind
 * utility styling rely on it, and style injection is not a script-execution
 * primitive.
 */
export function buildContentSecurityPolicy(options: CspOptions): string {
  const { nonce, isDev } = options;
  // The nonce travels verbatim into a quoted header value; confine it to the
  // base64 alphabet the generator emits so no future caller can smuggle
  // header syntax through this seam.
  if (!/^[A-Za-z0-9+/=]{16,128}$/.test(nonce)) {
    throw new Error("CSP nonce must be 16-128 base64 characters");
  }
  const imgOrigins = options.imgOrigins ?? [];
  const connectOrigins = options.connectOrigins ?? [];
  const scriptSrc = [
    "'self'",
    `'nonce-${nonce}'`,
    "'strict-dynamic'",
    ...(isDev ? ["'unsafe-eval'"] : []),
  ].join(" ");
  const directives = [
    "default-src 'self'",
    `script-src ${scriptSrc}`,
    "style-src 'self' 'unsafe-inline'",
    `img-src 'self' data: blob:${imgOrigins.length ? ` ${imgOrigins.join(" ")}` : ""}`,
    "font-src 'self' data:",
    `connect-src 'self'${connectOrigins.length ? ` ${connectOrigins.join(" ")}` : ""}`,
    "object-src 'none'",
    // Worker-tablet PWA (ITEM 2): the service worker and manifest are
    // first-party; no third-party worker or manifest may ever load.
    "worker-src 'self'",
    "manifest-src 'self'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ];
  return directives.join("; ");
}
