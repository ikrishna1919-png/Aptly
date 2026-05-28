import { NextResponse, type NextRequest } from "next/server";

import { isGatedPath } from "@/lib/routes";

/**
 * Route guard for direct hits to gated URLs.
 *
 * A logged-out visitor who types/bookmarks a gated URL (e.g. `/jobs`)
 * is redirected to `/?login=1&next=<path>` so the landing page opens
 * the sign-in modal with their original destination preserved. This
 * happens server-side, before any gated chrome renders — no flash.
 *
 * Auth signal: the backend Starlette session cookie (`session`). In
 * production it's set with `Domain=.aptly.fyi`, so it's present on the
 * frontend origin too; in local dev the proxy makes it same-origin.
 * We only check PRESENCE here — this is a UX gate, not the security
 * boundary. An expired/partial cookie still passes middleware, then
 * the client `RequireAuth`/`RequireProfile` guard (which calls
 * `/api/auth/me`) catches it and opens the same modal. The real
 * access controls are the backend endpoint dependencies.
 */
export function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl;

  if (!isGatedPath(pathname)) return NextResponse.next();

  const hasSession = request.cookies.has("session");
  if (hasSession) return NextResponse.next();

  const url = request.nextUrl.clone();
  url.pathname = "/";
  // Preserve the originally-requested path (incl. its query) so the
  // modal's Google button can route there after OAuth. The backend
  // OAuth callback honours this `next` over its `/jobs` default.
  const next = `${pathname}${search}`;
  url.search = "";
  url.searchParams.set("login", "1");
  url.searchParams.set("next", next);
  return NextResponse.redirect(url);
}

export const config = {
  /**
   * Run only on the gated route prefixes — keeps middleware off
   * static assets, the API proxy, and public pages. Kept in sync with
   * `GATED_PREFIXES` in `lib/routes.ts`.
   */
  matcher: [
    "/jobs/:path*",
    "/applications/:path*",
    "/interview-prep/:path*",
    "/ats/:path*",
    "/email-finder/:path*",
    "/profile/:path*",
    "/settings/:path*",
    "/admin/:path*",
  ],
};
