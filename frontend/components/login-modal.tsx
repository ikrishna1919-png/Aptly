"use client";

import { useCallback, useEffect } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { googleSignInUrl } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

/**
 * Global sign-in modal, mounted once in the root layout so it's
 * reachable from every page.
 *
 * Open state is driven entirely by the URL: `?login=1` opens it,
 * removing the param closes it. That makes the open state shareable
 * and back-button friendly, and lets the route guard (`middleware.ts`)
 * and nav links open it by simply adding the param.
 *
 * Centering + a11y come from the standard shadcn `DialogContent`
 * (fixed + translate(-50%,-50%), focus trap, scroll lock, Escape,
 * overlay-click dismiss, built-in X). We intentionally do NOT animate
 * the panel with Framer Motion: an inline `transform` from Framer would
 * override the centering translate and push the modal off-center.
 *
 * Google is the only working sign-in path for now (manual email/password
 * signup isn't built), so the "Create one" line is a disabled
 * placeholder, not a link.
 *
 * Copy adapts to the `?reason=` an action passed (via `useAuthGate`), so a
 * gated "Apply" click reads "Sign in to apply", etc. Plain sign-ins get
 * the generic default.
 */
const DEFAULT_COPY = {
  title: "Sign in to Aptly",
  body: "Continue with Google to find jobs that sponsor visas and tailor your applications. We only read your name & email.",
};
const REASON_COPY: Record<string, { title: string; body: string }> = {
  apply: {
    title: "Sign in to apply",
    body: "Sign in with Google to apply to this role and keep track of it. We only read your name & email.",
  },
  tailor: {
    title: "Sign in to tailor your resume",
    body: "Sign in with Google to tailor your resume to this job. We only read your name & email.",
  },
  track: {
    title: "Sign in to track your applications",
    body: "Sign in with Google to track your job applications in one place. We only read your name & email.",
  },
  save: {
    title: "Sign in to save jobs",
    body: "Sign in with Google to save jobs and come back to them later. We only read your name & email.",
  },
  profile: {
    title: "Sign in to set up your profile",
    body: "Sign in with Google to build your profile and tailor your resume. We only read your name & email.",
  },
  subscription: {
    title: "Sign in to manage your subscription",
    body: "Sign in with Google to view and manage your plan. We only read your name & email.",
  },
};

export function LoginModal() {
  const searchParams = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();
  const { user, loading } = useAuth();

  const open = searchParams.get("login") === "1";
  const nextParam = searchParams.get("next") || "/jobs";
  const hadError = searchParams.get("error") === "oauth";
  // Contextual reason → copy. Set by `useAuthGate` when an action triggers
  // the modal; absent for a plain sign-in (generic copy).
  const reason = searchParams.get("reason");
  const message = REASON_COPY[reason ?? ""] ?? DEFAULT_COPY;

  // Strip the modal's query params without touching the rest of the
  // URL. `replace` (not push) so closing doesn't add a history entry.
  const close = useCallback(() => {
    const params = new URLSearchParams(searchParams.toString());
    params.delete("login");
    params.delete("next");
    params.delete("error");
    params.delete("reason");
    const qs = params.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  }, [router, pathname, searchParams]);

  // A signed-in user has no business seeing the modal — if `?login=1`
  // somehow lands while authenticated, clean it up.
  useEffect(() => {
    if (open && !loading && user) close();
  }, [open, loading, user, close]);

  if (user) return null;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) close();
      }}
    >
      <DialogContent>
        <div className="space-y-2 text-center">
          <DialogTitle>{message.title}</DialogTitle>
          <DialogDescription>{message.body}</DialogDescription>
        </div>

        <div className="mt-2 space-y-4">
          {hadError && (
            <p
              role="alert"
              className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive"
            >
              Sign-in didn&apos;t complete. Please try again.
            </p>
          )}

          <Button asChild size="lg" className="w-full font-semibold">
            <a href={googleSignInUrl(nextParam)}>
              <GoogleMark /> Sign in with Google
            </a>
          </Button>

          {/* Placeholder for manual signup, which isn't built yet. The
              "Create one" text LOOKS like a link but is intentionally
              non-interactive (disabled), with a muted "(coming soon)". */}
          <p className="text-center text-sm text-muted-foreground">
            Don&apos;t have an account?{" "}
            <span
              aria-disabled="true"
              title="Manual signup is coming soon"
              className="cursor-not-allowed font-medium text-primary/70 underline decoration-dotted underline-offset-4"
            >
              Create one
            </span>{" "}
            <span className="text-xs text-muted-foreground/70">(coming soon)</span>
          </p>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** Google "G" mark — identical to the one the old sign-in page used,
 * so the button is visually unchanged. */
function GoogleMark() {
  return (
    <svg viewBox="0 0 18 18" width="16" height="16" aria-hidden="true" className="mr-2">
      <path
        fill="#4285F4"
        d="M17.64 9.205c0-.639-.057-1.252-.164-1.841H9v3.481h4.844a4.14 4.14 0 0 1-1.796 2.716v2.258h2.908c1.702-1.567 2.684-3.875 2.684-6.614z"
      />
      <path
        fill="#34A853"
        d="M9 18c2.43 0 4.467-.806 5.956-2.181l-2.908-2.258c-.806.54-1.836.859-3.048.859-2.344 0-4.328-1.583-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18z"
      />
      <path
        fill="#FBBC05"
        d="M3.964 10.71A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.71V4.958H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.042l3.007-2.332z"
      />
      <path
        fill="#EA4335"
        d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.958L3.964 7.29C4.672 5.163 6.656 3.58 9 3.58z"
      />
    </svg>
  );
}
