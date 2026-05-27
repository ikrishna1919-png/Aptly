/**
 * Render an ATS-sourced job description as formatted HTML.
 *
 * The backend stores the description with HTML entities DECODED (real
 * `<p>` / `<ul>` / `<strong>` rather than `&lt;p&gt;` text), and the
 * job-detail page hands the raw string in here. Because the content is
 * third-party (Greenhouse / Lever / SmartRecruiters), we sanitize via
 * isomorphic-dompurify BEFORE rendering — never inject untrusted HTML
 * directly into the DOM, even when it comes from "trusted" upstream
 * APIs (they sit behind partner integrations the recruiters configure).
 *
 * The component runs on the server (sanitization happens at request
 * time) so no extra JS bundle is shipped to the client for the
 * common case where the page is server-rendered.
 */

import DOMPurify from "isomorphic-dompurify";

// Allow the structural tags a JD typically uses; everything else
// (scripts, iframes, event handlers, javascript: URLs, etc.)
// DOMPurify strips by default. Explicit allow-list rather than the
// (more generous) default keeps the surface small and predictable.
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

export function JobDescription({ html }: { html: string }) {
  const safe = DOMPurify.sanitize(html, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    // Drop entire content of any disallowed tag (e.g. <script>) instead
    // of leaving stray text behind.
    KEEP_CONTENT: false,
  });

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
