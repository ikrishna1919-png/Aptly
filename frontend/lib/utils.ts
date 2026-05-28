import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const RELATIVE = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

/** "3h ago", "yesterday", "2d ago", or a fallback if the timestamp is bad. */
export function formatRelative(iso: string | null): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const deltaSec = Math.round((then - Date.now()) / 1000);
  const abs = Math.abs(deltaSec);
  if (abs < 60) return "just now";
  if (abs < 3600) return RELATIVE.format(Math.round(deltaSec / 60), "minute");
  if (abs < 86400) return RELATIVE.format(Math.round(deltaSec / 3600), "hour");
  return RELATIVE.format(Math.round(deltaSec / 86400), "day");
}

/** "May 25, 2026" — used on the detail page. */
export function formatLongDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/** Strip HTML tags + collapse whitespace to plain text (for card snippets
 * and meta). Not for rendering back into the DOM — display only. */
export function stripTags(html: string | null): string {
  if (!html) return "";
  return html
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/\s+/g, " ")
    .trim();
}

/** Title-case a work-model token ("remote" → "Remote"). */
export function workModelLabel(wm: string | null): string | null {
  if (!wm) return null;
  return wm.charAt(0).toUpperCase() + wm.slice(1);
}
