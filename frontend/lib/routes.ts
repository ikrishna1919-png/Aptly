/**
 * Single source of truth for which route prefixes require a signed-in
 * user. Consumed by `middleware.ts` for a server-side redirect of a
 * logged-out direct hit to `/?login=1&next=<path>`.
 *
 * Strategic shift: pages are PUBLIC; we gate ACTIONS (apply, tailor,
 * track) instead — see `useAuthGate`. Only routes that render
 * user-specific data stay page-gated:
 *   * `/profile`     — the user's career data.
 *   * `/settings`    — subscription/billing + per-user preferences.
 *   * `/admin`       — admin-only surface (also backend-gated).
 *
 * Everything else (`/jobs`, `/jobs/[id]`, `/applications`,
 * `/interview-prep`, `/ats`, `/email-finder`, `/support`) is publicly
 * viewable; their primary actions open the login modal when needed.
 *
 * This is a UX gate, NOT the security boundary — the backend
 * `/api/auth/me` check + per-endpoint `require_*_user` dependencies are
 * authoritative.
 */
export const GATED_PREFIXES = ["/profile", "/settings", "/admin"] as const;

/** True when `pathname` is under a gated prefix (exact match or a
 * sub-path). `/jobs` matches `/jobs` and `/jobs/123` but not
 * `/jobsearch`. */
export function isGatedPath(pathname: string): boolean {
  return GATED_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}
