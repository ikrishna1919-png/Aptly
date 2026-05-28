"use client";

import { useCallback } from "react";
import { useRouter } from "next/navigation";

/**
 * Returns `openLogin(next?)` — opens the login modal by setting
 * `?login=1` on the CURRENT url (so it's shareable + back-button
 * friendly). An optional `next` is stashed as `?next=…` so the
 * modal's Google button can route the user back to where they were
 * headed after OAuth completes.
 *
 * Reads `window.location` at click time rather than `useSearchParams`
 * on render — that keeps this hook usable in always-mounted chrome
 * (header, footer) WITHOUT forcing every static page under a Suspense
 * boundary. The reactive read of `?login=1` lives in `LoginModal`,
 * which is wrapped in Suspense in the root layout.
 */
export function useOpenLogin() {
  const router = useRouter();
  return useCallback(
    (next?: string) => {
      if (typeof window === "undefined") return;
      const url = new URL(window.location.href);
      url.searchParams.set("login", "1");
      if (next) url.searchParams.set("next", next);
      router.push(`${url.pathname}${url.search}`);
    },
    [router],
  );
}
