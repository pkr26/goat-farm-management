const nextConfig = {
  // Dev proxy: the SPA calls same-origin /api/* and Next forwards to the
  // FastAPI backend — no CORS friction, and the refresh cookie stays
  // first-party.
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.BACKEND_URL ?? "http://localhost:8000"}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
