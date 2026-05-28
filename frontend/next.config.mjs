/**
 * Next.js config — local-dev API rewrite.
 *
 * Production
 * ──────────
 * Browser → backend calls go DIRECTLY to `https://api.aptly.fyi` via
 * `NEXT_PUBLIC_API_URL` on Vercel. Frontend (`aptly.fyi`) and backend
 * (`api.aptly.fyi`) share the parent domain `aptly.fyi`, so the
 * session cookie is set with `Domain=.aptly.fyi` and works first-
 * party on both subdomains — no proxy required. The rewrite below is
 * a no-op in prod because the API client uses absolute URLs.
 *
 * Local dev
 * ─────────
 * `NEXT_PUBLIC_API_URL` is unset, so the API client emits relative
 * `/api/...` paths. The rewrite catches them and forwards to
 * `http://localhost:8000` (the local FastAPI server). Same-origin
 * from the browser's perspective so the local session cookie also
 * works first-party.
 *
 * Env vars
 * ────────
 *   * `API_PROXY_TARGET` — full backend URL the rewrite forwards
 *     to. Defaults to `http://localhost:8000` for `next dev`.
 *     Production deploys can leave it unset; the rewrite is
 *     unreachable when the API client uses absolute URLs.
 */

const proxyTarget =
  process.env.API_PROXY_TARGET ||
  process.env.NEXT_PUBLIC_API_URL ||
  "http://localhost:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        // `:path*` preserves the full sub-path AND the query string,
        // so `/api/jobs?limit=10` → `${target}/api/jobs?limit=10`.
        destination: `${proxyTarget}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
