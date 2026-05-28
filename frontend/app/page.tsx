import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { LandingPage } from "@/components/landing/landing-page";

export const dynamic = "force-dynamic";

// Server-side fetches need an absolute URL — there's no "origin" on
// the Next.js server. The rest of the app uses relative paths via
// the rewrite proxy (see `next.config.mjs`), so the public
// `NEXT_PUBLIC_API_URL` is no longer set in production; use the
// server-only `API_PROXY_TARGET` (which IS set, for the rewrite),
// then fall back to the legacy public var, then localhost for dev.
const API_URL =
  process.env.API_PROXY_TARGET ??
  process.env.NEXT_PUBLIC_API_URL ??
  "http://localhost:8000";

/**
 * Public landing route. Signed-in users get routed straight into the
 * app; the marketing surface is for logged-out visitors only.
 *
 * Routing rules:
 *   * Not signed in → render the landing page.
 *   * Signed in + profile saved → bounce to `/jobs` (the feed).
 *   * Signed in + profile NOT saved → bounce to `/profile` so the
 *     newcomer fills it in before the tailoring flow could run
 *     against the auto-seeded demo template.
 *
 * The check happens on the server (forwards the inbound cookie to
 * `/api/auth/me`) so signed-in users never see a flash of landing
 * content before the redirect lands. A network error reaching the
 * backend degrades to "landing page renders" rather than throwing
 * a 500 on the home route.
 */
type MeResponse = { profile_saved?: boolean };

async function loadMe(): Promise<MeResponse | null> {
  const cookieHeader = cookies().toString();
  if (!cookieHeader) return null;
  try {
    const res = await fetch(`${API_URL}/api/auth/me`, {
      headers: { cookie: cookieHeader },
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as MeResponse;
  } catch {
    return null;
  }
}

export default async function HomePage() {
  const me = await loadMe();
  if (me) {
    // `redirect` throws a NEXT_REDIRECT — control doesn't fall
    // through. New accounts (profile_saved=false) go to `/profile`
    // first; returning users go straight to the feed.
    if (me.profile_saved === false) {
      redirect("/profile?next=%2Fjobs");
    }
    redirect("/jobs");
  }
  return <LandingPage />;
}
