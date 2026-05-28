"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useMemo, useTransition } from "react";

import { cn } from "@/lib/utils";

/**
 * Pagination control for the jobs list. Renders prev / next chevrons
 * plus a windowed page-number row that elides distant pages with
 * ellipses so the bar stays compact on small viewports.
 *
 * Page state lives in the URL via the `offset` query param (the same
 * cursor the backend's `limit`/`offset` pagination uses). Filter
 * changes elsewhere already clear `offset`, which resets us to page 1
 * — no separate state to keep in sync.
 *
 * Single-page result sets render nothing (no point in a control with
 * one disabled button on each side).
 */
export function JobsPagination({
  page,
  totalPages,
  limit,
}: {
  page: number;
  totalPages: number;
  limit: number;
}) {
  const router = useRouter();
  const params = useSearchParams();
  const [pending, startTransition] = useTransition();

  // Hide entirely when there's at most one page — a single-page
  // control is just noise. The `useMemo` below must run on every
  // render to satisfy hooks-order rules, so compute first and bail
  // afterwards.
  const pages = useWindowedPages(page, totalPages);
  if (totalPages <= 1) return null;

  function go(p: number) {
    const next = new URLSearchParams(params.toString());
    const offset = (p - 1) * limit;
    if (offset > 0) next.set("offset", String(offset));
    else next.delete("offset");
    startTransition(() => router.push(`/jobs?${next.toString()}`));
  }

  return (
    <nav
      aria-label="Jobs pagination"
      className={cn(
        "mt-2 flex flex-wrap items-center justify-between gap-3",
        pending && "opacity-90",
      )}
    >
      <p className="text-xs text-muted-foreground">
        Page <span className="font-medium text-foreground">{page}</span> of{" "}
        <span className="font-medium text-foreground">{totalPages}</span>
      </p>

      <div className="flex items-center gap-1">
        <ArrowButton
          ariaLabel="Previous page"
          disabled={page <= 1}
          onClick={() => go(page - 1)}
        >
          <ChevronLeft />
        </ArrowButton>

        {pages.map((p, i) =>
          p === "…" ? (
            <span
              key={`gap-${i}`}
              aria-hidden="true"
              className="px-1 text-sm text-muted-foreground"
            >
              …
            </span>
          ) : (
            <PageButton
              key={p}
              active={p === page}
              onClick={() => go(p)}
              ariaLabel={`Page ${p}`}
              ariaCurrent={p === page ? "page" : undefined}
            >
              {p}
            </PageButton>
          ),
        )}

        <ArrowButton
          ariaLabel="Next page"
          disabled={page >= totalPages}
          onClick={() => go(page + 1)}
        >
          <ChevronRight />
        </ArrowButton>
      </div>
    </nav>
  );
}

// ── Windowed page-number list ───────────────────────────────────────────────


/** Returns the visible page row with `…` gaps. Always shows page 1
 * and `totalPages`, plus a 3-wide window around the current page.
 * Example output for `(7, 12)` → `[1, '…', 6, 7, 8, '…', 12]`. */
function useWindowedPages(current: number, total: number): (number | "…")[] {
  return useMemo<(number | "…")[]>(() => {
    if (total <= 7) {
      return Array.from({ length: total }, (_, i) => i + 1);
    }
    const window: (number | "…")[] = [];
    const around = new Set([current - 1, current, current + 1]);
    for (let i = 1; i <= total; i++) {
      if (i === 1 || i === total || around.has(i)) {
        window.push(i);
      } else if (
        (i === current - 2 && current - 2 > 1) ||
        (i === current + 2 && current + 2 < total)
      ) {
        window.push("…");
      }
    }
    return window;
  }, [current, total]);
}

// ── Buttons ─────────────────────────────────────────────────────────────────


function PageButton({
  active,
  onClick,
  ariaLabel,
  ariaCurrent,
  children,
}: {
  active: boolean;
  onClick: () => void;
  ariaLabel: string;
  ariaCurrent?: "page";
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={ariaLabel}
      aria-current={ariaCurrent}
      className={cn(
        "inline-flex h-8 min-w-8 items-center justify-center rounded-md border px-2 text-xs font-medium leading-none transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        active
          ? "border-primary bg-primary text-primary-foreground shadow-sm"
          : "border-border bg-background text-foreground hover:bg-secondary",
      )}
    >
      {children}
    </button>
  );
}

function ArrowButton({
  disabled,
  onClick,
  ariaLabel,
  children,
}: {
  disabled: boolean;
  onClick: () => void;
  ariaLabel: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={ariaLabel}
      className={cn(
        "inline-flex h-8 w-8 items-center justify-center rounded-md border border-border bg-background text-foreground transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        "disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-background",
      )}
    >
      {children}
    </button>
  );
}

function ChevronLeft() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="14"
      height="14"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="m15 18-6-6 6-6" />
    </svg>
  );
}

function ChevronRight() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="14"
      height="14"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="m9 18 6-6-6-6" />
    </svg>
  );
}
