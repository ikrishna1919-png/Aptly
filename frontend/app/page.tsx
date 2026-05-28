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
 * Public landing route. Logged-in visitors are redirected straight
 * into the app (`/jobs`); the marketing surface is for logged-out
 * users only.
 *
 * The auth check happens on the server — we forward the inbound
 * cookie to the backend's `/api/auth/me` and treat a 200 response
 * as authenticated. Doing this on the server (rather than the
 * existing client-side `useAuth` + `RequireAuth` flow) avoids a
 * flash of landing-page content before a signed-in user gets
 * bounced.
 *
 * A network error reaching the backend is treated as "anonymous"
 * so a backend outage degrades to "landing page renders" rather
 * than a 500 on the home route.
 */
async function isAuthenticated(): Promise<boolean> {
  const cookieHeader = cookies().toString();
  if (!cookieHeader) return false;
  try {
    const res = await fetch(`${API_URL}/api/auth/me`, {
      headers: { cookie: cookieHeader },
      cache: "no-store",
    });
    return res.ok;
  } catch {
    return false;
  }
}

export default async function HomePage() {
  if (await isAuthenticated()) {
    // `redirect` throws a NEXT_REDIRECT — control doesn't fall
    // through. The signed-in user goes straight to the feed.
    redirect("/jobs");
  }
  return <LandingPage />;
}
