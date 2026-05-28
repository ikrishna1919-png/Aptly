"use client";

/**
 * Client-side auth context — fetches `/api/auth/me` on mount and
 * exposes the result to any descendant via `useAuth()`.
 *
 * Four-state model (this is load-bearing — see the history below):
 *
 *   "loading"        first `/me` hasn't resolved yet.
 *   "authenticated"  `/me` returned a user.
 *   "unauthenticated" `/me` returned 401 — definitively signed out.
 *   "error"          `/me` could not be reached (network / 5xx / CORS /
 *                    cold-start timeout). We DON'T know the user's
 *                    status, so we must NOT treat this as signed out.
 *
 * Why the "error" state exists: previously a failed `/me` was caught and
 * collapsed into `user = null`, which every consumer read as "logged
 * out" — so a logged-in user whose `/me` timed out (Render free-tier
 * cold start) got the login modal on every gated nav click and got
 * bounced off `/jobs` on reload. `unauthenticated` now means ONLY a real
 * 401; transient failures are `error` and are retried.
 *
 * We deliberately do NOT persist anything to localStorage — the session
 * cookie is the source of truth.
 */

import { usePathname, useRouter } from "next/navigation";
import {
  ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";

import { CurrentUser, fetchCurrentUser, signOut as signOutApi } from "@/lib/api";

export type AuthStatus = "loading" | "authenticated" | "unauthenticated" | "error";

type AuthState = {
  user: CurrentUser | null;
  /** The authoritative auth state. Prefer this over `user`/`loading`
   * when deciding to intercept a click or redirect a route. */
  status: AuthStatus;
  /** Convenience: `status === "loading"`. Kept for existing call sites. */
  loading: boolean;
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

// Retry the `/me` probe a few times before giving up — rides out a
// Render free-tier cold start (~30s) without declaring the user logged
// out. Backoff is deliberately gentle and bounded.
const ME_RETRY_DELAYS_MS = [1500, 3000, 5000];

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [status, setStatus] = useState<AuthStatus>("loading");
  const router = useRouter();

  const refresh = useCallback(async () => {
    for (let attempt = 0; attempt <= ME_RETRY_DELAYS_MS.length; attempt++) {
      try {
        const u = await fetchCurrentUser();
        if (u) {
          setUser(u);
          setStatus("authenticated");
        } else {
          // A clean 401 — genuinely signed out.
          setUser(null);
          setStatus("unauthenticated");
        }
        return;
      } catch (e) {
        // Network / 5xx / CORS / timeout. We do NOT know if the user is
        // signed in. Retry; only after exhausting retries do we settle
        // on "error" — never on "unauthenticated".
        console.error(`auth: /api/auth/me attempt ${attempt + 1} failed:`, e);
        const delay = ME_RETRY_DELAYS_MS[attempt];
        if (delay !== undefined) {
          await new Promise((r) => setTimeout(r, delay));
          continue;
        }
        setStatus("error");
        return;
      }
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const signOut = useCallback(async () => {
    await signOutApi();
    setUser(null);
    setStatus("unauthenticated");
    // Land on the public home page (logged out) rather than the login
    // modal — signing out shouldn't immediately nag a sign-in.
    router.push("/");
  }, [router]);

  return (
    <AuthContext.Provider
      value={{ user, status, loading: status === "loading", refresh, signOut }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be called inside <AuthProvider>");
  }
  return ctx;
}

/** Redirect target for a definitively-signed-out visitor: the landing
 * page with the login modal open (`?login=1`) and the originally-
 * requested path preserved as `next`. Centralised so every guard opens
 * sign-in the same way. */
function loginRedirect(pathname: string | null): string {
  const next = pathname && pathname !== "/" ? pathname : "/jobs";
  return `/?login=1&next=${encodeURIComponent(next)}`;
}

/** Wraps a route that requires a signed-in user.
 *
 *   loading        → render nothing (brief; avoids a flash).
 *   unauthenticated→ redirect to the login modal.
 *   error          → render children optimistically. The user almost
 *                    certainly IS signed in (middleware only lets a
 *                    cookie-bearing request reach a gated route), and
 *                    the backend endpoints are the real authorization
 *                    gate — so a transient `/me` failure must not lock
 *                    them out.
 *   authenticated  → render children.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (status === "unauthenticated") {
      router.replace(loginRedirect(pathname));
    }
  }, [status, pathname, router]);

  if (status === "loading" || status === "unauthenticated") return null;
  // authenticated | error → show the page.
  return <>{children}</>;
}

/** Like `RequireAuth`, but additionally requires admin. The BACKEND
 * `require_admin_user` dependency is the real gate; this only hides the
 * admin surface. On "error" we render nothing (don't expose admin UI
 * under uncertainty). */
export function RequireAdmin({ children }: { children: ReactNode }) {
  const { user, status } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (status === "unauthenticated") {
      router.replace(loginRedirect(pathname));
      return;
    }
    if (status === "authenticated" && user && !user.is_admin) {
      router.replace("/profile");
    }
  }, [status, user, pathname, router]);

  if (status !== "authenticated" || !user || !user.is_admin) return null;
  return <>{children}</>;
}

/** Like `RequireAuth`, but additionally requires a saved profile.
 *
 * Brand-new accounts are auto-seeded with a demo profile; without this
 * gate they could trigger tailoring against the template. Routes them to
 * `/profile` first; once saved, `refresh()` flips the flag.
 *
 *   loading        → nothing.
 *   unauthenticated→ login modal.
 *   error          → render children (optimistic — see RequireAuth).
 *   authenticated  → if profile not saved, go to `/profile`; else render.
 *
 * Older backends without the field default to `profile_saved=true` so a
 * version skew doesn't soft-lock users out of their feed.
 */
export function RequireProfile({ children }: { children: ReactNode }) {
  const { user, status } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (status === "unauthenticated") {
      router.replace(loginRedirect(pathname));
      return;
    }
    if (status === "authenticated" && user && !(user.profile_saved ?? true)) {
      const dest = pathname && pathname !== "/" ? pathname : "/jobs";
      router.replace(`/profile?next=${encodeURIComponent(dest)}`);
    }
  }, [status, user, pathname, router]);

  if (status === "loading" || status === "unauthenticated") return null;
  if (status === "authenticated" && user && !(user.profile_saved ?? true)) {
    return null;
  }
  // authenticated + saved, OR error (optimistic) → render.
  return <>{children}</>;
}
