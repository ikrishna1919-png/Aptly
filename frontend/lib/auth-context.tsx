"use client";

/**
 * Client-side auth context — fetches `/api/auth/me` on mount and
 * exposes the result to any descendant via `useAuth()`. Used by
 * the layout to render the header (signed-in name + sign-out
 * link) and by `RequireAuth` to redirect unauthenticated visitors
 * to the sign-in page.
 *
 * We deliberately do NOT persist anything to localStorage — the
 * session cookie is the source of truth. Any client-side cache
 * would lie when the cookie expires.
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

type AuthState = {
  user: CurrentUser | null;
  // `loading=true` is the in-flight state before the first
  // `/api/auth/me` resolves. We render a placeholder while it's true
  // so the header doesn't flicker between "signed out" and "signed in".
  loading: boolean;
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  const refresh = useCallback(async () => {
    try {
      const u = await fetchCurrentUser();
      setUser(u);
    } catch (e) {
      // Network error fetching /me — treat as signed-out and let
      // the user retry. Don't throw on the layout's first render.
      console.error("auth: fetchCurrentUser failed:", e);
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const signOut = useCallback(async () => {
    await signOutApi();
    setUser(null);
    router.push("/sign-in");
  }, [router]);

  return (
    <AuthContext.Provider value={{ user, loading, refresh, signOut }}>
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

/** Wraps a route that requires a signed-in user. Redirects to
 * `/sign-in?next=<current-path>` when the auth check resolves
 * un-authenticated. Renders nothing until the first /me call
 * settles — avoids the "flash of unauthenticated content" that
 * would otherwise leak protected-page chrome to anonymous
 * viewers before the redirect lands. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!loading && !user) {
      const next = encodeURIComponent(pathname || "/");
      router.replace(`/sign-in?next=${next}`);
    }
  }, [loading, user, pathname, router]);

  if (loading || !user) return null;
  return <>{children}</>;
}

/** Like `RequireAuth`, but additionally requires the user to have
 * SAVED their profile at least once (`profile_saved=true` on the
 * `/me` response).
 *
 * Brand-new accounts get auto-seeded with a demo profile so the
 * editor has a shape to render; without this gate they'd be able
 * to browse `/jobs` and trigger tailoring against the demo
 * template, producing nonsense output. Routes them to `/profile`
 * first; once they save, `refresh()` flips the flag and they can
 * navigate back to `/jobs` normally.
 *
 * Older backends without the field default to `profile_saved=true`
 * so a deploy version skew doesn't soft-lock users out of their feed. */
export function RequireProfile({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (loading) return;
    if (!user) {
      const next = encodeURIComponent(pathname || "/");
      router.replace(`/sign-in?next=${next}`);
      return;
    }
    const saved = user.profile_saved ?? true;
    if (!saved) {
      // Stash where the user was headed so the profile page's
      // post-save redirect can bring them back. Falls back to
      // `/jobs` when the user navigated to `/` directly.
      const dest = pathname && pathname !== "/" ? pathname : "/jobs";
      router.replace(`/profile?next=${encodeURIComponent(dest)}`);
    }
  }, [loading, user, pathname, router]);

  if (loading || !user) return null;
  if (!(user.profile_saved ?? true)) return null;
  return <>{children}</>;
}
