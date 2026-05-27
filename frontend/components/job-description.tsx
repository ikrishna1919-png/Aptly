/**
 * Render an ATS-sourced job description as formatted HTML.
 *
 * The backend stores the description with HTML entities decoded (real
 * `<p>` / `<ul>` / `<strong>` rather than `&lt;p&gt;` text), and the
 * job-detail page hands the raw string in here. Because the content is
 * third-party (Greenhouse / Lever / SmartRecruiters), we sanitize with
 * `sanitize-html` BEFORE rendering — never inject untrusted HTML
 * directly into the DOM, even when it comes from "trusted" upstream
 * APIs (they sit behind partner integrations that recruiters configure).
 *
 * `sanitize-html` is a Node-native sanitizer (no JSDOM, no virtual DOM)
 * so it runs cleanly in Next.js's serverless runtime — `isomorphic-
 * dompurify` was the previous choice and its `jsdom` dependency failed
 * to load in production, throwing a 500 from the rendering path.
 */

import sanitizeHtml from "sanitize-html";

// Allow the structural tags a JD typically uses; everything else
// (scripts, iframes, event handlers, javascript: URLs, etc.)
// sanitize-html strips by default for safety. Explicit allow-list
// rather than the (more generous) default keeps the surface small
// and predictable.
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

const SANITIZE_CONFIG: sanitizeHtml.IOptions = {
  allowedTags: ALLOWED_TAGS,
  allowedAttributes: {
    a: ["href", "title", "target", "rel"],
  },
  // Only allow http(s) + mailto: in href — strips `javascript:` and
  // data: URLs that could carry script payloads.
  allowedSchemes: ["http", "https", "mailto"],
  // Force `rel="noopener noreferrer"` on outbound links so a
  // sanitized JD can't grab `window.opener`.
  transformTags: {
    a: sanitizeHtml.simpleTransform("a", {
      rel: "noopener noreferrer",
      target: "_blank",
    }),
  },
};

/**
 * Strip every tag from `html`, returning plain text. Useful for
 * `<meta name="description">` and other text-only contexts.
 */
export function htmlToPlainText(html: string): string {
  return sanitizeHtml(html, { allowedTags: [], allowedAttributes: {} })
    .replace(/\s+/g, " ")
    .trim();
}

export function JobDescription({ html }: { html: string | null | undefined }) {
  // Be liberal about what the caller hands us — both `null` from the
  // API and accidental empty strings render the same fallback rather
  // than calling into the sanitiser with a non-string.
  if (!html || !html.trim()) {
    return (
      <p className="text-sm text-muted-foreground">
        No description provided.
      </p>
    );
  }

  const safe = sanitizeHtml(html, SANITIZE_CONFIG);
  if (!safe.trim()) {
    // Sanitiser stripped everything (e.g. JD was just a `<script>`):
    // show the fallback rather than an empty container so the page
    // still has something there.
    return (
      <p className="text-sm text-muted-foreground">
        No description provided.
      </p>
    );
  }

  return (
    <div
      // Tailwind Typography handles paragraph spacing, list indents,
      // bold/italic, and heading sizes. `prose-sm` keeps the JD from
      // dwarfing the surrounding chrome on the detail page.
      className="prose prose-sm max-w-none text-foreground/90 prose-headings:font-semibold prose-headings:text-foreground prose-a:text-primary prose-strong:text-foreground"
      // The HTML is freshly sanitised one line above; rendering it via
      // `dangerouslySetInnerHTML` is the whole reason for the
      // sanitiser. React's auto-escape would otherwise show literal
      // `<p>` markup.
      dangerouslySetInnerHTML={{ __html: safe }}
    />
  );
}
