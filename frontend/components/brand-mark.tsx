import { cn } from "@/lib/utils";

/**
 * Aptly brand mark — a small "A" in a rounded square. Inline SVG so it
 * inherits currentColor and scales crisply at every size. Pair with the
 * "Aptly" wordmark in headers.
 */
export function BrandMark({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 32 32"
      className={cn("h-7 w-7", className)}
    >
      <rect width="32" height="32" rx="8" fill="hsl(var(--primary))" />
      <path
        d="M10 22 L16 10 L22 22 M12.5 18 H19.5"
        stroke="hsl(var(--primary-foreground))"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />
    </svg>
  );
}
