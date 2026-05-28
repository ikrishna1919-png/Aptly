/**
 * Single source of truth for which route prefixes require a signed-in
 * user. Consumed by `middleware.ts` for a server-side redirect of a
 * logged-out direct hit to `/?login=1&next=<path>`.
 *
 * Strategic stance: pages are PUBLIC; we gate ACTIONS (apply, tailor,
 * track) via `useAuthGate`, and personal-data pages (Profile,
 * Subscription) render a "sign in" empty state rather than redirecting.
 * The ONLY page-gated prefix left is `/admin` — an admin-only surface
 * that's also enforced by the backend.
 */
export const GATED_PREFIXES = ["/admin"] as const;

/** True when `pathname` is under a gated prefix (exact match or a
 * sub-path). `/jobs` matches `/jobs` and `/jobs/123` but not
 * `/jobsearch`. */
export function isGatedPath(pathname: string): boolean {
  return GATED_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}
