"use client";

import type { LucideIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useOpenLogin } from "@/lib/use-login-modal";

/**
 * Logged-out empty state for a personal-data page (Profile, Subscription).
 * The page itself stays public and loads — this renders instead of the
 * user-specific UI, with a button that opens the login modal (carrying a
 * contextual `reason` for the modal copy).
 */
export function SignInRequired({
  icon: Icon,
  title,
  body,
  reason,
  cta,
}: {
  icon: LucideIcon;
  title: string;
  body: string;
  reason: string;
  cta: string;
}) {
  const openLogin = useOpenLogin();
  return (
    <main className="container max-w-2xl py-16 sm:py-24">
      <div className="mx-auto max-w-md rounded-2xl border border-border/70 bg-card p-8 text-center shadow-sm">
        <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-primary-soft text-primary">
          <Icon className="h-5 w-5" aria-hidden />
        </span>
        <h1 className="mt-4 font-display text-2xl font-medium tracking-tight text-foreground">
          {title}
        </h1>
        <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{body}</p>
        <Button
          size="lg"
          className="mt-5 font-semibold"
          onClick={() => openLogin(undefined, reason)}
        >
          {cta}
        </Button>
      </div>
    </main>
  );
}
