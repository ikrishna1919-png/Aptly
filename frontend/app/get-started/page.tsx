"use client";

import Link from "next/link";
import { Suspense } from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { googleSignInUrl } from "@/lib/api";

/**
 * `/get-started` — the manual "Create an account" page.
 *
 * ORPHANED ON PURPOSE: nothing in the current navigable flow links
 * here. The live auth path is Google-only via the global login modal
 * (`?login=1`), which is where the landing-page CTAs and the header
 * "Sign in" now point. This page is the future home of email/password
 * signup; it's kept in the codebase (not deleted) so it can be wired
 * up to a manual-signup entry point when that ships.
 *
 * A small, centred card that mirrors the create-account layouts most
 * modern SaaS sign-ups use: Google up top, a divider, then the
 * email-sign-up section underneath. Email/password sign-up is NOT
 * implemented yet — per the brief, don't ship a password field that
 * doesn't create an account, so the email section reads as
 * "coming soon" rather than a fake form.
 */
export default function GetStartedPage() {
  return (
    <Suspense fallback={<main className="container py-20">Loading…</main>}>
      <GetStartedInner />
    </Suspense>
  );
}

function GetStartedInner() {
  return (
    <main className="container max-w-md py-16 sm:py-20">
      <Card className="border-border/70 shadow-card">
        <CardHeader className="space-y-2 text-center">
          <CardTitle className="font-display text-2xl font-medium tracking-tight">
            Create an account
          </CardTitle>
          <CardDescription>
            A minute to set up. Aptly only uses what you provide here — no
            scraping, no auto-enrichment.
          </CardDescription>
        </CardHeader>

        <CardContent className="space-y-5">
          <Button asChild size="lg" className="w-full font-semibold">
            <a href={googleSignInUrl("/profile")}>
              <GoogleMark /> Sign up with Google
            </a>
          </Button>

          <div className="flex items-center gap-3 text-[11px] uppercase tracking-[0.16em] text-muted-foreground">
            <span className="h-px flex-1 bg-border" />
            <span>or</span>
            <span className="h-px flex-1 bg-border" />
          </div>

          {/* Honest placeholder — see the file's leading docstring
              for why we DON'T ship a non-functional password field
              here. Mirrors the visual structure most users expect
              while making clear that Google is the path today. */}
          <div className="space-y-2 rounded-lg border border-dashed border-border/70 bg-secondary/30 p-4 text-center">
            <p className="text-sm font-medium text-foreground">
              Email sign-up — coming soon.
            </p>
            <p className="text-xs leading-relaxed text-muted-foreground">
              We&apos;re finishing email/password auth. In the meantime, use
              Google above to get started — it takes one click and we only
              read your name + email.
            </p>
          </div>

          <p className="pt-1 text-center text-xs text-muted-foreground">
            Already signed up?{" "}
            <Link
              href="/sign-in"
              className="font-medium text-primary underline-offset-4 hover:underline"
            >
              Sign in
            </Link>
          </p>
        </CardContent>
      </Card>

      <p className="mt-6 text-center text-xs text-muted-foreground">
        <Link href="/" className="hover:text-foreground">
          ← Back to home
        </Link>
      </p>
    </main>
  );
}

function GoogleMark() {
  return (
    <svg
      viewBox="0 0 18 18"
      width="16"
      height="16"
      aria-hidden="true"
      className="mr-2"
    >
      <path
        fill="#4285F4"
        d="M17.64 9.205c0-.639-.057-1.252-.164-1.841H9v3.481h4.844a4.14 4.14 0 0 1-1.796 2.716v2.258h2.908c1.702-1.567 2.684-3.875 2.684-6.614z"
      />
      <path
        fill="#34A853"
        d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18z"
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
