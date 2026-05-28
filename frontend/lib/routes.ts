/**
 * Single source of truth for which route prefixes require a signed-in
 * user. Consumed by:
 *   * `middleware.ts` — server-side redirect of a logged-out direct
 *     hit to `/?login=1&next=<path>` (no flash of gated chrome).
 *   * the header / settings nav — to decide whether a logged-out
 *     click should open the login modal instead of navigating.
 *
 * Note: this is a UX gate, NOT the security boundary. The real access
 * controls are the backend `/api/auth/me` check + the per-endpoint
 * `require_*_user` dependencies. Keep them in sync conceptually, but
 * the backend is authoritative.
 *
 * Corrections vs. a naive list (verified against the route tree):
 *   * `/support` is PUBLIC (no guard) — not gated.
 *   * "Contact us" lives at `/settings/contact`, which IS gated
 *     (covered by the `/settings` prefix below).
 */
export const GATED_PREFIXES = [
  "/jobs",
  "/applications",
  "/interview-prep",
  "/ats",
  "/email-finder",
  "/profile",
  "/settings",
  "/admin",
] as const;

/** True when `pathname` is under a gated prefix (exact match or a
 * sub-path). `/jobs` matches `/jobs` and `/jobs/123` but not
 * `/jobsearch`. */
export function isGatedPath(pathname: string): boolean {
  return GATED_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}
