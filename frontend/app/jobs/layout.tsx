"use client";

import type { ReactNode } from "react";

import { JobsShell } from "@/components/jobs/jobs-shell";

/**
 * `/jobs/**` is PUBLIC — anyone can browse jobs and read a posting. The
 * split-pane shell (filters + job list) lives HERE rather than in the page
 * so it persists across `/jobs` ↔ `/jobs/[id]` — selecting a job only swaps
 * the detail pane (`children`); the list never remounts.
 *
 * Actions on these pages (Apply, Tailor) are gated individually via
 * `useAuthGate`, not the page — so a logged-out visitor sees the jobs but
 * is prompted to sign in only when they try to act.
 */
export default function JobsLayout({ children }: { children: ReactNode }) {
  return <JobsShell>{children}</JobsShell>;
}
