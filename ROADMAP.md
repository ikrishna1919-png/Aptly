# ROADMAP & STATUS — Aptly

_Last updated: after the ingestion-reliability + ATS-autofill cycle (PRs #81–97 merged)._

## DONE (working in production)
- Custom domain live: aptly.fyi (frontend) + api.aptly.fyi (backend), first-party cookie.
- Google sign-in working (cookie saga resolved by the shared domain).
- `prompt=select_account` so sign-in offers account choice (no silent auto-resume) — verify shipped.
- Multi-ATS job aggregation: Greenhouse, Lever, Workday, SmartRecruiters, Ashby.
- **Ingestion now COMPLETES reliably (this cycle):** fixed the idle-in-transaction crash (don't hold a tx across the per-batch HTTP fetches); per-source heartbeat + `last_progress_at`; stale `running` rows reported `stale` and reaped to `failed`; `INGEST_MAX_PER_RUN=0` = all enabled sources (always-on only); optional `INGEST_RUN_BUDGET_SECONDS`; `HOURS_WINDOW` default 720. **The remaining lever for full coverage is upgrading Render to Starter (always-on)** — then set `INGEST_MAX_PER_RUN=0`.
- **Resume tailoring, two modes (this cycle):** saved `default_resume_format.source` routes BOTH the Jobs CTA and `/ats/generate`. `"ai"` = from-scratch generate; `"resume"` = in-place, **steer-only** keyword edits on the saved DOCX (rewrites existing text across runs, preserves per-run formatting, never inserts lines). 5 gap questions + free text steer wording choice; selective revert + custom output filename on the review screen.
- **Chrome extension autofill (this cycle, dev-install only):** Greenhouse/Lever/Ashby standard fields from profile; SmartRecruiters detection + apply-workflow host; compliance/EEO answers on the profile (Form-filling guide), EEO filled only when explicitly set; Greenhouse react-select sponsorship/EEO driver; "Add to Chrome extension" sets the active autofill run. Resume auto-attach wired.
- `sources` table observability (per-source status, counts, rotation, async fetch).
- Resume parsing (PDF via Anthropic document input, DOCX via python-docx, text box). Improved; minor gaps remain.
- Manual profile entry (primary) with all sections; resume upload optional.
- AI resume + cover-letter tailoring.
- Profile sections: experience (multi-role per company), education, skills (categorized), projects, certifications, achievements, languages + others.
- Landing page (original, light-blue design system, motion).
- App nav shell: Jobs, Application Tracker, Interview Prep, ATS, Email Finder, Support + Settings menu (Profile, Subscription, Language, Contact Us, About Us, Sign Out).
- Admin-only gating for manual entries (server-side via ADMIN_EMAILS).
- Light-blue design system applied across pages.
- Logo + favicon (in progress / last PR).

## KNOWN ISSUES / GAPS (address during end-to-end testing)
- Resume parsing: a few field-level gaps remain on some resumes. Diagnose any specific failure via `raw_llm_output` query (extraction vs display). Golden reference fixture exists.
- Rare `redirect_uri_mismatch` (~1/10) — stale/cached OAuth request. Mitigation: generate OAuth request fresh on click; friendly retry UI. Remove old onrender.com redirect URI from Google Console.
- Two Vercel projects (`aptly`, `aptly-buvg`) both build per PR. Disconnect the orphan `aptly` once confirmed unused (live is `aptly-buvg` — do NOT touch it).
- Verify pagination pulls ALL jobs per company (Workday/SmartRecruiters especially).
- _(Resolved this cycle: HOURS_WINDOW widened to 720; ingestion idle-in-transaction crash fixed.)_

## OPEN — next up (extension autofill is the active workstream)
1. **Greenhouse react-select dropdown fill + resume auto-attach — VERIFY on a live posting.** Both shipped but were verified failing on stale bundles before the fixes; confirm on a real Greenhouse application (e.g. the Rockstar posting) after `git pull → npm run build → reload extension → reload tab`. Report whether the resume actually attaches or the uploader needs a manual drop.
2. **Semantic layer for company-specific / referral / free-text questions** ("worked here before?", "Why are you interested?", data-privacy consent). Currently left yellow ("answer manually") by design — never guessed. This is the AI-answer PR.
3. **Chrome Web Store listing** (still dev-install / "Load unpacked" only).
4. **iCIMS adapter** — feasibility spike FIRST (iCIMS DOM/auth differ; confirm it's tractable before committing to an adapter).

## NEXT FEATURES (still "coming soon" pages — honest framing: not live)
1. Job-posting notifications/alerts.
2. Recruiter/contact email finder. (Privacy/terms care needed.)
3. Interview prep (role + sponsorship-specific).
4. Application Tracker (likely the highest-value of the unbuilt set).
5. Sponsorship intelligence from DOL/LCA public data (the differentiator — task previously drafted).

## INFRA / OPS TODO
- **Upgrade Render to Starter (~$7/mo) — now the #1 infra lever.** It kills cold starts AND is the prerequisite for full ingestion coverage: only when always-on is it safe to set `INGEST_MAX_PER_RUN=0` (+ widen `HOURS_WINDOW`) so one pass covers every source. On the free tier a long unbounded pass still gets the worker killed (now surfaced honestly as `stale`/`failed`, not a stuck `running`).
- Set Anthropic billing alert (usage-based cost is the wild card).
- Privacy policy + terms of service before real users (handling resumes, Google data, visa info). Not legal advice — consult a professional when you have users.

## THE BIG STRATEGIC NEXT STEP
Get Aptly in front of **3–5 real international students** and watch them use the working core (jobs + sponsorship signals + tailoring). That feedback — not more building — tells you what to build next. You've built a real MVP; now validate it.
