/** Same-origin routes that Next proxies to the FastAPI service. */

/**
 * The proxy forwards bearer tokens and the httpOnly refresh cookie. Restrict every
 * backend scheme to loopback or a single-label service on the private Compose
 * network; TLS alone does not make a public destination safe.
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
  // WHATWG URL keeps brackets on IPv6 hostnames. The label pattern also
  // accepts localhost and excludes public domains and numeric hosts.
  const loopback = host === "127.0.0.1" || host === "[::1]";
  const internalServiceName = /^[a-z][a-z0-9-]*$/i.test(host);
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
