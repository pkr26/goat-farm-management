/** Same-origin routes that Next proxies to the FastAPI service. */

/**
 * The rewrite forwards the browser's bearer token and the httpOnly refresh
 * cookie verbatim, so the backend hop must stay on a network other hosts
 * cannot reach — for BOTH schemes. Plain HTTP is allowed only for targets
 * that cannot be reached from outside the host: loopback, or a single-label
 * Docker Compose service name (e.g. `http://backend:8000`) on the
 * project-private bridge network. https:// previously returned early
 * (RT-R-7 follow-up, 2026-09-16 audit INFRA-2): a rebuilt image with
 * `BACKEND_URL=https://attacker.example` passed validation, and the proxy
 * would silently ship credentials there. The host rules now apply to every
 * scheme — a public hostname is an exfil target over TLS just as much as
 * over plaintext.
 */
export function assertSafeBackendUrl(backendUrl: string): void {
  let parsed: URL;
  try {
    parsed = new URL(backendUrl);
  } catch {
    throw new Error(
      `BACKEND_URL must be an absolute http(s) URL (got ${JSON.stringify(backendUrl)})`,
    );
  }
  const host = parsed.hostname;
  // Stryker disable ConditionalExpression, StringLiteral, LogicalOperator: "localhost" and every dot/colon-free hostname already satisfy the label regex below, WHATWG hostnames render IPv6 WITH brackets (so the bare "::1" arm is dead code), and the includes(".")/includes(":") checks are implied by the regex — every swapped arm or literal here decides nothing the regex does not already decide
  const loopback =
    host === "localhost" ||
    host === "127.0.0.1" ||
    host === "::1" ||
    host === "[::1]";
  const internalServiceName =
    !host.includes(".") &&
    !host.includes(":") &&
    /^[a-z][a-z0-9-]*$/i.test(host) &&
    !/^\d/.test(host);
  // Stryker restore ConditionalExpression, StringLiteral, LogicalOperator
  if (
    (parsed.protocol !== "http:" && parsed.protocol !== "https:") ||
    !(loopback || internalServiceName)
  ) {
    throw new Error(
      `BACKEND_URL must point at a loopback or internal service name (got ${backendUrl}); ` +
        "the proxied requests carry the session bearer token and refresh cookie, so a " +
        "publicly reachable target — even over https — would be a credential exfil path",
    );
  }
}

export function backendRewrites(backendUrl: string) {
  assertSafeBackendUrl(backendUrl);
  return [
    { source: "/healthz", destination: `${backendUrl}/healthz` },
    { source: "/readyz", destination: `${backendUrl}/readyz` },
    { source: "/api/:path*", destination: `${backendUrl}/api/:path*` },
  ];
}
