import type { NextConfig } from "next";

/**
 * The port the FastAPI backend serves on: `uvicorn api_server:app --port 8001`.
 * Override with NEXT_PUBLIC_API_URL when the backend runs elsewhere.
 */
export const BACKEND_DEFAULT_URL = "http://localhost:8001";

const nextConfig: NextConfig = {
  // API requests to /api/* are proxied to the FastAPI backend during development.
  async rewrites() {
    // R9 (arc 5, F-02): the default must be the port the backend actually
    // serves on. It was :8000; uvicorn runs api_server on :8001 (README,
    // CLAUDE.md §3), so on a fresh clone — which has no git-ignored
    // frontend/.env.local — every /api/* rewrite missed.
    const apiBase =
      process.env.NEXT_PUBLIC_API_URL ?? BACKEND_DEFAULT_URL;
    return [
      {
        source: "/api/:path*",
        destination: `${apiBase}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
