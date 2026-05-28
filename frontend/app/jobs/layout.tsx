"use client";

import type { ReactNode } from "react";

import { RequireProfile } from "@/lib/auth-context";

/**
 * Gates every `/jobs/**` route behind a real, saved profile. A
 * brand-new user is routed to `/profile` first; after they save,
 * `RequireProfile` re-checks `/me`, sees `profile_saved=true`,
 * and renders the feed.
 *
 * Lives at the layout level rather than the page level because
 * the underlying `/jobs/page.tsx` + `/jobs/[id]/page.tsx` are
 * server components, and the gating helper depends on the
 * client-side auth context. Wrapping both at the layer above
 * keeps the page files unchanged.
 */
export default function JobsLayout({ children }: { children: ReactNode }) {
  return <RequireProfile>{children}</RequireProfile>;
}
