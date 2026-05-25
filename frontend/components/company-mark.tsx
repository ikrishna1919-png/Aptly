import { cn } from "@/lib/utils";

/**
 * A small letter-tile for the company name. Color is derived
 * deterministically from the name so the same company always gets the
 * same tile color across the feed — a low-effort substitute for real
 * company logos.
 */

const PALETTE = [
  "bg-violet-500",
  "bg-emerald-500",
  "bg-sky-500",
  "bg-amber-500",
  "bg-rose-500",
  "bg-indigo-500",
  "bg-teal-500",
  "bg-fuchsia-500",
  "bg-orange-500",
  "bg-cyan-600",
];

function hashName(name: string): number {
  let h = 0;
  for (let i = 0; i < name.length; i++) {
    h = (h * 31 + name.charCodeAt(i)) >>> 0;
  }
  return h;
}

export function CompanyMark({
  name,
  size = "md",
  className,
}: {
  name: string;
  size?: "sm" | "md" | "lg";
  className?: string;
}) {
  const initial = name.trim().charAt(0).toUpperCase() || "·";
  const color = PALETTE[hashName(name) % PALETTE.length];
  return (
    <span
      aria-hidden="true"
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-md font-semibold text-white shadow-sm ring-1 ring-inset ring-black/5",
        color,
        size === "sm" && "h-6 w-6 text-xs",
        size === "md" && "h-9 w-9 text-sm",
        size === "lg" && "h-12 w-12 text-base",
        className,
      )}
    >
      {initial}
    </span>
  );
}
