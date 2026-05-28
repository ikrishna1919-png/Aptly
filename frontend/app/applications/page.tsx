"use client";

import { ClipboardList } from "lucide-react";

import { ComingSoon } from "@/components/coming-soon";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth-context";
import { useOpenLogin } from "@/lib/use-login-modal";
import { ApplicationTrackerPreview } from "./preview";

/**
 * Application Tracker is publicly viewable. Logged-out visitors see a
 * friendly sign-in prompt (the tracker is personal data); signed-in
 * visitors see the (coming-soon) feature preview. The page itself always
 * loads — we never redirect away.
 */
export default function ApplicationsPage() {
  const { status } = useAuth();
  const openLogin = useOpenLogin();

  if (status === "unauthenticated") {
    return (
      <main className="container max-w-2xl py-16 sm:py-24">
        <div className="mx-auto max-w-md rounded-2xl border border-border/70 bg-card p-8 text-center shadow-sm">
          <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-primary-soft text-primary">
            <ClipboardList className="h-5 w-5" aria-hidden />
          </span>
          <h1 className="mt-4 font-display text-2xl font-medium tracking-tight text-foreground">
            Track your job applications
          </h1>
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
            Sign in to track your job applications in one place — what&apos;s
            applied, interviewing, an offer, or needs a follow-up.
          </p>
          <Button
            size="lg"
            className="mt-5 font-semibold"
            onClick={() => openLogin(undefined, "track")}
          >
            Sign in to track your applications
          </Button>
        </div>
      </main>
    );
  }

  return (
    <ComingSoon
      eyebrow="Application Tracker"
      title="Every application, every status, in one place."
      blurb="Right now most job-seekers track their pipeline in a spreadsheet that goes stale within a week. Aptly will keep it current by linking each saved job to its application status — so you can see what's pending, what needs a follow-up, and what's already a no, without leaving the app."
      bullets={[
        "Kanban or table view, by status: applied, screen, interviewing, offer, rejected.",
        "Per-application notes — recruiter name, screen date, follow-up dates.",
        "Auto-detect a stalled application after N days and prompt a follow-up.",
        "Export the whole tracker to CSV when you want a record outside Aptly.",
      ]}
      preview={<ApplicationTrackerPreview />}
    />
  );
}
