"use client";

import { useCallback, useEffect } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";

import {
  Dialog,
  DialogClose,
  DialogContentPrimitive,
  DialogDescription,
  DialogOverlay,
  DialogPortal,
  DialogTitle,
  DialogCloseIcon,
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
 * The modal wraps the EXISTING Google sign-in action unchanged — same
 * `googleSignInUrl()` link the old `/sign-in` page used. Per product:
 * Google is the only option for now (manual email/password signup is
 * not built), so there's deliberately no "create an account" link.
 *
 * Accessibility + behaviour come from Radix (focus trap, scroll lock,
 * Escape, click-outside); Framer Motion drives the enter/exit so the
 * panel feels like the rest of the light-blue design system. We use
 * `forceMount` + `AnimatePresence` so the exit animation actually runs
 * before Radix unmounts the content.
 */
export function LoginModal() {
  const searchParams = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();
  const { user, loading } = useAuth();
  const reduced = useReducedMotion();

  const open = searchParams.get("login") === "1";
  const nextParam = searchParams.get("next") || "/jobs";
  const hadError = searchParams.get("error") === "oauth";

  // Strip the modal's query params without touching the rest of the
  // URL. `replace` (not push) so closing doesn't add a history entry.
  const close = useCallback(() => {
    const params = new URLSearchParams(searchParams.toString());
    params.delete("login");
    params.delete("next");
    params.delete("error");
    const qs = params.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  }, [router, pathname, searchParams]);

  // A signed-in user has no business seeing the modal — if `?login=1`
  // somehow lands while authenticated, clean it up.
  useEffect(() => {
    if (open && !loading && user) close();
  }, [open, loading, user, close]);

  if (user) return null;

  const panelMotion = reduced
    ? {
        initial: { opacity: 0 },
        animate: { opacity: 1 },
        exit: { opacity: 0 },
        transition: { duration: 0.15 },
      }
    : {
        initial: { opacity: 0, y: 12, scale: 0.97 },
        animate: { opacity: 1, y: 0, scale: 1 },
        exit: { opacity: 0, y: 8, scale: 0.98 },
        transition: { duration: 0.22, ease: [0.22, 1, 0.36, 1] as const },
      };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) close();
      }}
    >
      <AnimatePresence>
        {open && (
          <DialogPortal forceMount>
            <DialogOverlay asChild forceMount>
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.18 }}
                className="fixed inset-0 z-50 bg-foreground/40 backdrop-blur-sm"
              />
            </DialogOverlay>

            <DialogContentPrimitive asChild forceMount>
              <motion.div
                {...panelMotion}
                className="fixed left-1/2 top-1/2 z-50 w-[calc(100vw-2rem)] max-w-md -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-2xl border border-border/70 bg-card p-6 shadow-elevated focus:outline-none sm:p-8"
              >
                {/* Soft brand wash so the modal reads as part of the
                    light-blue system rather than a plain sheet. */}
                <div
                  aria-hidden="true"
                  className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-28 bg-gradient-to-b from-primary/[0.08] to-transparent"
                />

                <DialogClose
                  className="absolute right-4 top-4 rounded-md p-1 text-muted-foreground transition-colors duration-fast hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  aria-label="Close"
                >
                  <DialogCloseIcon className="h-4 w-4" aria-hidden />
                </DialogClose>

                <div className="space-y-2 text-center">
                  <DialogTitle>Sign in to Aptly</DialogTitle>
                  <DialogDescription>
                    Continue with Google to find jobs that sponsor visas and
                    tailor your applications. We only read your name & email.
                  </DialogDescription>
                </div>

                <div className="mt-6 space-y-4">
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

                  <p className="text-center text-xs leading-relaxed text-muted-foreground">
                    Email sign-up is coming soon. By continuing you agree to
                    let Aptly use your profile only for the features you use.
                  </p>
                </div>
              </motion.div>
            </DialogContentPrimitive>
          </DialogPortal>
        )}
      </AnimatePresence>
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
