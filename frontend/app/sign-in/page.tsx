"use client";

import { useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

/**
 * `/sign-in` is now a thin redirect to the landing page with the
 * global login modal open (`?login=1`). The standalone sign-in page
 * was replaced by the modal; this stub keeps old bookmarks and links
 * working. Any `next` / `error` params are carried through so the
 * modal can route the user correctly and surface OAuth retry copy.
 */
function SignInRedirect() {
  const router = useRouter();
  const params = useSearchParams();

  useEffect(() => {
    const out = new URLSearchParams();
    out.set("login", "1");
    const next = params.get("next");
    const error = params.get("error");
    if (next) out.set("next", next);
    if (error) out.set("error", error);
    router.replace(`/?${out.toString()}`);
  }, [router, params]);

  return (
    <main className="container py-20 text-center text-sm text-muted-foreground">
      Redirecting to sign in…
    </main>
  );
}

export default function SignInPage() {
  return (
    <Suspense
      fallback={<main className="container py-20 text-center">Loading…</main>}
    >
      <SignInRedirect />
    </Suspense>
  );
}
