/**
 * Next.js config — same-origin API proxy to the backend.
 *
 * Why the proxy exists
 * ────────────────────
 * The frontend (Vercel) and the backend (Render) live on different
 * domains in production. That made the session cookie a third-party
 * cookie from the browser's perspective, which:
 *
 *   * Safari blocks by default (Intelligent Tracking Prevention).
 *   * Chrome / Firefox block in private / incognito modes.
 *
 * Sign-in worked for Chrome users on their main profile and failed
 * everywhere else. The fix is to make every browser → backend call
 * SAME-ORIGIN by proxying `/api/*` through the Vercel domain — the
 * `Set-Cookie` then lands as a first-party cookie for the frontend
 * origin and survives ITP.
 *
 * The backend learns nothing about this. From its perspective the
 * request still arrives at its public URL; it just arrives via the
 * Vercel edge instead of directly from the user's browser.
 *
 * Env vars
 * ────────
 *   * `API_PROXY_TARGET` — full backend URL the rewrites forward to.
 *     Used at build-time / on the Vercel edge; NOT exposed to the
 *     browser (intentionally — the browser only ever talks to the
 *     Vercel origin). Defaults to `http://localhost:8000` so local
 *     `next dev` works without any extra setup.
 *
 * Notes
 * ─────
 *   * The legacy `NEXT_PUBLIC_API_URL` is no longer used by the
 *     API client (`frontend/lib/api.ts` calls relative paths now).
 *     It's still respected as a fallback target for backward
 *     compatibility with environments that haven't switched yet —
 *     see the default below.
 *   * Vercel rewrites stream the response and preserve `Set-Cookie`
 *     and other headers; the backend's session cookie reaches the
 *     browser unchanged.
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
