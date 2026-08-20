/** Same-origin routes that Next proxies to the FastAPI service. */
export function backendRewrites(backendUrl: string) {
  return [
    { source: "/healthz", destination: `${backendUrl}/healthz` },
    { source: "/readyz", destination: `${backendUrl}/readyz` },
    { source: "/api/:path*", destination: `${backendUrl}/api/:path*` },
  ];
}
