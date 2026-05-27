"use client";

/**
 * Render an ATS-sourced job description as formatted, sanitized HTML.
 *
 * The backend stores the description with HTML entities decoded (real
 * `<p>` / `<ul>` / `<strong>` rather than `&lt;p&gt;` text), but the
 * content is still third-party (Greenhouse / Lever / SmartRecruiters
 * boards configured by external recruiters) — never inject untrusted
 * HTML into the DOM. We sanitize with DOMPurify before rendering it
 * via `dangerouslySetInnerHTML`.
 *
 * Why a client component?
 *
 *   The previous server-side sanitizer pulled in `jsdom` (transitively
 *   via `isomorphic-dompurify`). Vercel's serverless runtime can't
 *   load jsdom from a Next.js server function — it surfaces as
 *   `ERR_REQUIRE_ESM` deep inside `html-encoding-sniffer` and crashes
 *   the dynamic `/jobs/[id]` route before any app code runs. Moving
 *   the sanitizer to the browser (where a real DOM exists, so plain
 *   `dompurify` works without jsdom) removes the entire class of bug.
 *
 * Server-render path
 *
 *   `"use client"` components are still rendered once on the server
 *   for the first paint. On that pass, DOMPurify isn't usable (no DOM
 *   yet), so we render a *stripped-text* version of the description.
 *   That gives crawlers something to index, gives the user something
 *   to read pre-hydration, and avoids the "blank box" that a
 *   `useEffect` swap would cause. After hydration `useEffect` swaps
 *   in the sanitized HTML for the formatted view.
 */

import DOMPurify from "dompurify";
import { useEffect, useState } from "react";

// Tags a JD typically uses. Everything else (script, iframe, on*
// event handlers, javascript: URLs, etc.) DOMPurify strips by
// default — and the strict allow-list keeps the surface small.
const ALLOWED_TAGS = [
  "p",
  "br",
  "hr",
  "strong",
  "em",
  "b",
  "i",
  "u",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "ul",
  "ol",
  "li",
  "a",
  "blockquote",
  "pre",
  "code",
  "span",
  "div",
  "table",
  "thead",
  "tbody",
  "tr",
  "th",
  "td",
];
const ALLOWED_ATTR = ["href", "title", "target", "rel"];

const PROSE_CLASSES =
  "prose prose-sm max-w-none text-foreground/90 prose-headings:font-semibold prose-headings:text-foreground prose-a:text-primary prose-strong:text-foreground";

/** Strip tags via regex — no DOM needed, safe to call on the server. */
function stripTags(html: string): string {
  return html
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function JobDescription({ html }: { html: string | null | undefined }) {
  // First-paint fallback: the same text content the server would emit.
  // After mount we replace it with the sanitized HTML.
  const [safeHtml, setSafeHtml] = useState<string | null>(null);

  useEffect(() => {
    if (!html) {
      setSafeHtml("");
      return;
    }
    // `addHook` would let us tighten further (e.g. force noopener on
    // every anchor), but the default config + our allow-list already
    // strips scripts / on* handlers / `javascript:` URLs.
    const clean = DOMPurify.sanitize(html, {
      ALLOWED_TAGS,
      ALLOWED_ATTR,
      // Keep text content inside disallowed tags (default), so the
      // reader doesn't see paragraphs vanish if the JD uses a tag
      // that's not on our list.
      KEEP_CONTENT: true,
      // Belt-and-braces: explicitly forbid the obvious dangerous tags
      // even though the allow-list already excludes them.
      FORBID_TAGS: ["script", "style", "iframe", "object", "embed"],
      FORBID_ATTR: ["onerror", "onload", "onclick", "onmouseover"],
    });
    setSafeHtml(clean);
  }, [html]);

  if (!html || !html.trim()) {
    return (
      <p className="text-sm text-muted-foreground">No description provided.</p>
    );
  }

  if (safeHtml === null) {
    // Server render + pre-hydration: show the stripped-text view so
    // the page has indexable content and the user has something to
    // read before JS lands. `suppressHydrationWarning` silences the
    // expected text-vs-HTML mismatch when we swap in below.
    return (
      <div className={PROSE_CLASSES} suppressHydrationWarning>
        {stripTags(html)}
      </div>
    );
  }

  if (!safeHtml.trim()) {
    // Sanitizer dropped everything (e.g. JD was nothing but a script
    // tag) — fall back to the placeholder instead of showing an
    // empty container.
    return (
      <p className="text-sm text-muted-foreground">No description provided.</p>
    );
  }

  return (
    <div
      className={PROSE_CLASSES}
      suppressHydrationWarning
      // Sanitized one frame above; React's auto-escape would
      // otherwise show literal `<p>` markup.
      dangerouslySetInnerHTML={{ __html: safeHtml }}
    />
  );
}
