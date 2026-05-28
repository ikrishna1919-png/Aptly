"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
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

function SignInInner() {
  const params = useSearchParams();
  const next = params.get("next") || "/";
  // `?error=oauth` arrives via the callback handler when the OAuth
  // exchange fails (token expired, code already used, etc.). Don't
  // surface the raw error — just a friendly retry message.
  const errorCode = params.get("error");

  return (
    <main className="container max-w-md py-20">
      <Card>
        <CardHeader>
          <CardTitle>Sign in to Aptly</CardTitle>
          <CardDescription>
            We use Google sign-in only. The first time you sign in
            with the email the owner configured (`INITIAL_USER_EMAIL`),
            you&apos;ll see all the existing profile data — that&apos;s
            how the single-user phase migrates over.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {errorCode && (
            <p className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              Sign-in didn&apos;t complete. Try again — usually just a stale OAuth code.
            </p>
          )}
          <Button asChild className="w-full">
            <a href={googleSignInUrl(next)}>
              <GoogleMark /> Sign in with Google
            </a>
          </Button>
          <p className="text-xs text-muted-foreground">
            By signing in you agree to use Aptly for personal job-search
            purposes. We don&apos;t share your data.
          </p>
          <p className="text-xs text-muted-foreground">
            <Link href="/" className="hover:text-foreground">
              ← Back to home
            </Link>
          </p>
        </CardContent>
      </Card>
    </main>
  );
}

export default function SignInPage() {
  // `useSearchParams` requires a Suspense boundary in the App Router
  // — without it the page bails out of static rendering with a
  // build-time warning.
  return (
    <Suspense fallback={<main className="container py-20">Loading…</main>}>
      <SignInInner />
    </Suspense>
  );
}

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
