"use client";

import { useId } from "react";

import { cn } from "@/lib/utils";

/**
 * Aptly brand icon — a light-blue rounded square holding a stylized
 * "A" / upward-chevron fused with a checkmark crossbar (the
 * "right fit, confirmed" idea). Inline SVG so it stays crisp at
 * every size and ships in the same bundle (no image request).
 *
 * The gradient (#3B9EFF → #1E6FE0) is the brand's signature light
 * blue and is baked in, so the mark reads identically on light and
 * dark surfaces. `useId` namespaces the gradient so multiple marks
 * on one page (header + footer) don't collide on a shared `id`.
 *
 * Pair with the `Logo` lockup for the full icon + wordmark.
 */
export function BrandMark({ className }: { className?: string }) {
  const gid = useId();
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 64 64"
      className={cn("h-8 w-8", className)}
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      <defs>
        <linearGradient
          id={`${gid}-bg`}
          x1="8"
          y1="4"
          x2="56"
          y2="60"
          gradientUnits="userSpaceOnUse"
        >
          <stop stopColor="#3B9EFF" />
          <stop offset="1" stopColor="#1E6FE0" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="15" fill={`url(#${gid}-bg)`} />
      {/* "A" / upward chevron — the forward-and-up motion. */}
      <path
        d="M15 45 L32 17 L49 45"
        stroke="#FFFFFF"
        strokeWidth="6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {/* Checkmark crossbar — the "right fit, confirmed" signal. */}
      <path
        d="M23.5 38 L29.5 44 L41 30"
        stroke="#FFFFFF"
        strokeWidth="6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
