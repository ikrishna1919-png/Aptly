"use client";

import { CreditCard } from "lucide-react";

import { SignInRequired } from "@/components/auth/sign-in-required";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useAuth } from "@/lib/auth-context";

export default function SubscriptionPage() {
  // Public page. Logged-out visitors get a sign-in empty state (billing is
  // personal); everyone else sees the plan.
  const { status } = useAuth();
  if (status === "unauthenticated") {
    return (
      <SignInRequired
        icon={CreditCard}
        title="Sign in to manage your subscription"
        body="Aptly is free while in early access. Sign in to view your plan and manage billing when paid tiers arrive."
        reason="subscription"
        cta="Sign in to manage your subscription"
      />
    );
  }
  if (status === "loading") return null;
  return <SubscriptionInner />;
}

function SubscriptionInner() {
  return (
    <main className="container max-w-3xl space-y-8 py-12 sm:py-16">
      <header className="space-y-3">
        <Badge
          variant="outline"
          className="border-primary/30 bg-primary/5 text-xs font-medium uppercase tracking-[0.16em] text-primary"
        >
          Settings · Subscription
        </Badge>
        <h1 className="font-display text-3xl font-medium tracking-tight text-foreground sm:text-4xl">
          Subscription
        </h1>
        <p className="max-w-2xl text-base leading-relaxed text-muted-foreground">
          Aptly is free while in early access. We&apos;ll publish the paid
          tiers below before we start charging, and existing users stay free
          until that switch is announced explicitly.
        </p>
      </header>

      <Card className="border-primary/30 bg-primary/5 shadow-sm">
        <CardHeader className="flex flex-row items-start justify-between gap-4">
          <div className="space-y-1">
            <CardTitle className="font-display text-xl font-medium tracking-tight">
              Free plan
            </CardTitle>
            <CardDescription>
              Everything that&apos;s live today, included.
            </CardDescription>
          </div>
          <Badge className="font-medium">Current</Badge>
        </CardHeader>
        <CardContent>
          <ul className="space-y-2 text-sm text-foreground">
            <Feature label="Full ATS-sourced job feed with sponsorship signals" />
            <Feature label="AI résumé tailoring + DOCX export" />
            <Feature label="Profile editor with résumé parsing" />
            <Feature label="No usage caps in early access" />
          </ul>
        </CardContent>
      </Card>

      <Card className="border-dashed border-border/70 shadow-none">
        <CardHeader>
          <CardTitle className="font-display text-lg font-medium tracking-tight">
            Pro <span className="text-xs font-normal text-muted-foreground">· coming soon</span>
          </CardTitle>
          <CardDescription>
            Heavier tooling for active job-seekers. Pricing TBD; current users
            keep free access through the announcement window.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="space-y-2 text-sm text-muted-foreground">
            <Feature label="Full Application Tracker with follow-up reminders" muted />
            <Feature label="Interview Prep packs + mock-interview drills" muted />
            <Feature label="ATS Checker against any job" muted />
            <Feature label="Email Finder with sane outreach limits" muted />
          </ul>
          <div className="mt-5">
            <Button variant="outline" size="sm" disabled>
              Notify me when this lands
            </Button>
            <p className="mt-2 text-xs text-muted-foreground">
              We&apos;ll DM-blast nobody — opt-in only when the button works.
            </p>
          </div>
        </CardContent>
      </Card>

      <p className="text-xs text-muted-foreground">
        Need an invoice or have a billing question? Reach us via{" "}
        <a
          href="/support"
          className="font-medium text-primary underline-offset-4 hover:underline"
        >
          Support
        </a>
        .
      </p>
    </main>
  );
}

function Feature({ label, muted }: { label: string; muted?: boolean }) {
  return (
    <li className="flex items-start gap-3">
      <span
        aria-hidden="true"
        className={`mt-2 h-1.5 w-1.5 shrink-0 rounded-full ${
          muted ? "bg-muted-foreground/40" : "bg-primary"
        }`}
      />
      <span>{label}</span>
    </li>
  );
}
