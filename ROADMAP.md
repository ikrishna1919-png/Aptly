# ROADMAP & STATUS — Aptly

_Last updated: handoff after ~4 days of build._

## DONE (working in production)
- Custom domain live: aptly.fyi (frontend) + api.aptly.fyi (backend), first-party cookie.
- Google sign-in working (cookie saga resolved by the shared domain).
- `prompt=select_account` so sign-in offers account choice (no silent auto-resume) — verify shipped.
- Multi-ATS job aggregation: Greenhouse, Lever, Workday, SmartRecruiters, Ashby.
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
- Two Vercel projects (`aptly`, `aptly-buvg`) both build per PR. Disconnect `aptly-buvg` once confirmed unused.
- HOURS_WINDOW still 48 — widen to surface more jobs.
- Verify pagination pulls ALL jobs per company (Workday/SmartRecruiters especially).

## NEXT FEATURES (roadmap — currently "coming soon" pages)
1. Job-posting notifications/alerts (notify on new posting).
2. Semi-auto apply via Chrome extension. (Build only after core MVP + real users. Respect platform terms.)
3. Recruiter/contact email finder. (Privacy/terms care needed.)
4. Interview prep (role + sponsorship-specific).
5. Application Tracker (likely the highest-value of the unbuilt set).
6. Sponsorship intelligence from DOL/LCA public data (the differentiator — task previously drafted).

## INFRA / OPS TODO
- Upgrade Render to Starter (~$7/mo) before real users (kills cold starts).
- Set Anthropic billing alert (usage-based cost is the wild card).
- Privacy policy + terms of service before real users (handling resumes, Google data, visa info). Not legal advice — consult a professional when you have users.

## THE BIG STRATEGIC NEXT STEP
Get Aptly in front of **3–5 real international students** and watch them use the working core (jobs + sponsorship signals + tailoring). That feedback — not more building — tells you what to build next. You've built a real MVP; now validate it.
