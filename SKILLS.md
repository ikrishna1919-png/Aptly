# Aptly SKILLS — Hard-won patterns for fast, safe shipping

Read alongside CLAUDE.md. These are patterns that have repeatedly worked (or whose absence has repeatedly bitten) across many PRs. Reuse them; don't re-derive them.

## Diagnostic-first prompt pattern

Every non-trivial Claude Code prompt begins with a DIAGNOSE FIRST section. Read the current state of the relevant code/database/config, report findings, and STOP if the diagnosis reveals something materially different from what the prompt assumes. Only THEN apply changes.

This has prevented several major bug-introductions in this codebase. Always include it.

Template:

```
=== DIAGNOSE FIRST (mandatory) ===

1. Read [specific file/path]. Report [specific facts].
2. Check [specific data/state]. Report.
3. Identify [specific assumption to verify].

REPORT findings before making any changes. If [stated assumption]
turns out to be wrong, STOP and report rather than ploughing forward.
```

A real example: a task said "delete the orphan Vercel project `aptly-buvg`" — diagnosis showed `aptly-buvg` is the LIVE project. Reporting before acting avoided deleting production.

## Honest framing rules

- Never advertise coming-soon features as live in any UI copy.
- Never invent metrics, stats, or scores. "JD keyword coverage %" (deterministic overlap) is acceptable; "ATS score 87/100" (invented) is not.
- Never fabricate user testimonials or social proof.
- Show coming-soon features clearly labeled, not hidden.
- When a requested value can't be verified (e.g. a dashboard plan/price), say so and ask — don't fill in a plausible-looking number.

## PR sizing rules

- Migrations ship in their own PR. Never combined with cosmetic or feature work. **Only ONE migration-containing PR open at a time, ever** — multi-head Alembic conflicts happen even with sequential merges (branches cut from the same ancestor diverge into two heads; fix is an empty merge revision).
- Auth changes ship separately from UI changes.
- "Cosmetic + small backend tweak" can combine. "Cosmetic + auth change" cannot.
- After merging, verify in incognito with a hard refresh BEFORE starting the next PR.

## Common LLM / Anthropic patterns

- Use **prompt-based JSON output, NOT strict structured output** for complex schemas. "Grammar compilation timed out" errors come from strict mode + complex schemas.
- Always **retry ONCE on JSONDecodeError** with a corrective prompt ("Your previous response had syntax error X. Return only the corrected JSON, no fences, no prose."). On a second failure, surface a clean user-facing message — never a traceback.
- **Strip markdown fences** before parsing LLM JSON (```json ... ```), and tolerate incidental prose by slicing the outermost `{...}`.
- Use **Haiku** for fast preprocessing/classification, **Sonnet** for generation. Don't use a more expensive model than needed.
- **Prompt caching:** structure the system prompt as a list with `cache_control: {"type":"ephemeral"}` on the static portion (rules + candidate fingerprint). Saves on repeat calls within the cache window.
- Generation can be slow on Render free tier — run it as a background job that writes a terminal status; stream partial snapshots if a UI is polling.

## Build pipeline patterns

- Chrome extension MV3 content scripts MUST be bundled as **IIFE**. ESM `import` works in the popup/background but NOT content scripts. `extension/scripts/build.mjs` inlines the relative import graph (no external bundler needed).
- **Always check the actual built output, not just the source.** Grep for `^import` in the bundled file to verify bundling worked: `grep "^import" extension/content/greenhouse.js` must be empty.
- Run checks locally before push to save CI round-trips:
  `cd backend && black --check app tests && ruff check app tests && pytest`
  `cd frontend && npx tsc --noEmit && npm run build`
  `cd extension && npm run build && npm test`

## Render & Vercel patterns

- Render free-tier cold starts are ~30s. Account for this in latency expectations.
- A failed migration crashes the deploy and Render holds the prior good deploy live (`set -euo pipefail` in `start.sh`). The live backend stays up while you fix the migration — fix forward, don't panic-roll-back.
- Vercel free tier has a daily build rate limit; pushing many PRs in a day can exhaust it.
- Two Vercel projects on the same repo (`aptly` + `aptly-buvg`) is a known footgun. **Live is `aptly-buvg`.** Be EXTREMELY careful before deleting either; confirm in the dashboard first.

## Common debugging patterns

- "I don't see the changes live" → almost always (a) PR not merged, (b) Vercel pointing at the wrong deploy/project, or (c) browser cache. Hard-refresh in incognito to rule out (c) before debugging anything else.
- Extension `SyntaxError` on content-script load → bundling didn't inline ESM imports. Fix the build, not the code.
- Backend "test failure" 17–20s after start → almost certainly a lint or pytest collection/import error, not a real assertion failure. Check ruff/black and imports first.
- A green local run that contradicts CI → check the venv/tooling actually activated (e.g. `ruff`/`pytest` "not found" silently passing). Invoke tools by explicit path if unsure.

## Don't-rebuild-existing-features pattern

Before adding new functionality, check what already exists and reuse it:
- Format renderers (docx/pdf) live in `backend/app/services/{docx_export,pdf_export}.py` — extend with a FormatSpec, don't reimplement.
- The tailor pipeline (`tailor.py` / `ats.py`) already exists — extend it; reuse `_extract_json_object`, the sanitizer, contact reconciliation, the page-measurer.
- Shared predicates for extension content scripts live in `extension/src/content/shared.js` — add new ones there for reuse, then `npm run build` to re-bundle.
- Default-format storage + the AI-chooses heuristic live in `backend/app/services/default_formats.py`.

## Strategic patterns

- Validate with real users before building. Founders' guess at user priorities is wrong about half the time. 3–5 students using the working core is the highest-leverage activity at this stage.
- Don't scrape job boards (LinkedIn/Indeed/Glassdoor/JobRight). Hard rule. Won't be reversed.
- The moat is **sponsorship intelligence from public DOL/LCA data.** Breadth of listings is not the moat. Aggregation gets us to parity; sponsorship signals get us a moat.
