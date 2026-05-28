"use client";

import { useCallback } from "react";
import { useRouter } from "next/navigation";

import { useAuth } from "@/lib/auth-context";

/**
 * Returns `openLogin(next?, reason?)` — opens the login modal by setting
 * `?login=1` on the CURRENT url (shareable + back-button friendly).
 *   * `next`   — stashed as `?next=…` so the modal's Google button routes
 *     the user back to where they were after OAuth.
 *   * `reason` — a short code (`apply` | `tailor` | `track` | `save`) the
 *     modal maps to contextual copy ("Sign in to apply", etc.).
 *
 * Reads `window.location` at click time rather than `useSearchParams` on
 * render, so it's usable in always-mounted chrome without forcing pages
 * under a Suspense boundary. The reactive read of `?login=1` lives in
 * `LoginModal`.
 */
export function useOpenLogin() {
  const router = useRouter();
  return useCallback(
    (next?: string, reason?: string) => {
      if (typeof window === "undefined") return;
      const url = new URL(window.location.href);
      url.searchParams.set("login", "1");
      if (next) url.searchParams.set("next", next);
      if (reason) url.searchParams.set("reason", reason);
      else url.searchParams.delete("reason");
      router.push(`${url.pathname}${url.search}`);
    },
    [router],
  );
}

/**
 * Action-level auth gate. Returns `gate(reason?)`:
 *   * returns `true` when the action may proceed (signed in, OR auth is in
 *     an "error" state where we optimistically trust the cookie + server —
 *     consistent with the four-state auth model);
 *   * returns `false` and opens the login modal (with contextual copy) when
 *     the user is definitively signed out;
 *   * returns `false` and does nothing while auth is still "loading".
 *
 * Pages stay public; this is how their primary actions (Apply, Tailor,
 * Track) require sign-in only at the moment of acting.
 */
export function useAuthGate() {
  const { status } = useAuth();
  const openLogin = useOpenLogin();
  return useCallback(
    (reason?: string): boolean => {
      if (status === "loading") return false;
      if (status === "unauthenticated") {
        openLogin(undefined, reason);
        return false;
      }
      // authenticated | error → let the action proceed; the backend is the
      // real authorization gate.
      return true;
    },
    [status, openLogin],
  );
}
