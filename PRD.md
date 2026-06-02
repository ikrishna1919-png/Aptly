# Aptly — Product Requirements Document (PRD)

## 1. Overview
**Aptly** is a job-search platform built specifically for **international students and workers who need visa (H-1B) sponsorship** to work in the US. It aggregates tech jobs that sponsor visas, surfaces sponsorship signals, and uses AI to tailor resumes and cover letters to specific jobs.

## 2. Problem
International students face a job search that's harder than for domestic candidates:
- Most job boards don't tell you which employers actually sponsor visas, so applicants waste enormous effort on roles that will never sponsor.
- Tailoring a resume per job is time-consuming, and the stakes are higher (visa timelines, fewer second chances).
- Sponsorship information exists in public DOL/LCA data but is scattered and hard to use.

## 3. Target user
Primary: international students (and recent grads / early-career workers) in tech, on or seeking F-1/OPT → H-1B paths, applying in the US. High-stakes, time-pressured, trust-sensitive.

## 4. Value proposition / differentiation
Not competing on breadth with LinkedIn/Indeed. Competing on **fit for the sponsorship-seeking user**:
- Jobs from employers that sponsor, in one place.
- Sponsorship intelligence derived from free public DOL/LCA data (the moat).
- AI tailoring that's accurate and honest (never fabricates).

## 5. Core features

### Available now (MVP)
1. **Sponsorship-aware job aggregation** — pulls tech jobs directly from ATS origins (Greenhouse, Lever, Workday, SmartRecruiters, Ashby), one searchable list, with sponsorship signals. Ingestion completes reliably; full single-pass coverage requires an always-on host (Render Starter).
2. **AI resume & cover-letter tailoring (two modes, user's choice via saved default format):**
   - **AI-generated** — builds a tailored, ATS-safe resume from the profile + job description; asks follow-up questions only about genuine gaps; truthful, never fabricated; DOCX/PDF.
   - **Match my resume format** — in-place, **steer-only** keyword edits on the user's saved DOCX: rewrites only wording that already exists (preserving the original formatting/layout, adding no new lines); 5 gap questions steer which existing phrasing to strengthen; per-edit revert + custom filename. The saved choice drives both the Jobs "Tailor" CTA and the ATS generator.
3. **Profile** — manual entry (primary) of full career data; optional resume upload (DOCX saved as the autofill/tailoring source; PDF/text parsed for reference). Includes a Form-filling guide for compliance/EEO answers. All fields user-editable.
4. **Chrome extension (dev-install today; user-initiated, never auto-submits)** — autofills standard application fields (Greenhouse/Lever/Ashby; SmartRecruiters detection; Workday experimental) and sponsorship/EEO dropdowns from the profile, EEO only when explicitly set; attaches the active tailored resume. Not yet on the Chrome Web Store.

### Roadmap (coming soon)
5. **Job alerts** — notify on new matching postings.
6. **Smarter autofill** — AI/semantic answers for company-specific & free-text application questions (today those are flagged for manual answer, never guessed); Chrome Web Store listing. (Must respect platform terms.)
7. **Email finder** — surface recruiter/hiring-manager contacts for outreach. (Must respect privacy/terms.)
8. **Interview prep** — role- and sponsorship-specific preparation.
9. **Application tracker** — track applications and statuses end-to-end.
10. **Sponsorship intelligence dashboard** — richer insights from DOL/LCA data.

## 6. Key flows
- **Onboarding:** Landing page → Get Started → Google sign-in → Profile (create/save) → Jobs unlocked.
- **Tailoring:** Pick a job → the saved default-format choice routes to either AI-generate (profile + JD + follow-up answers) or in-place keyword edits on the saved DOCX → tailored resume + cover letter → download (and optionally set as the extension's autofill resume).
- **Admin:** admin-only manual data entry (gated server-side by ADMIN_EMAILS).

## 7. Principles & constraints
- **Honesty:** never fabricate resume content; never advertise unbuilt features as live. Distinguish "available now" vs "coming soon" in all copy.
- **Trust & data care:** handling resumes, Google accounts, visa-related info → privacy policy/ToS needed before launch; careful with any contact-data/auto-apply features.
- **No scraping** of LinkedIn/Indeed/Glassdoor/JobRight. ATS origins only. Paid feeds are a later, post-revenue option.
- **Trustworthy > flashy.** Performance and reliability matter to this audience.

## 8. Tech architecture (summary)
- Frontend: Next.js (App Router), TS, Tailwind, shadcn/ui, Framer Motion. On Vercel at aptly.fyi.
- Backend: FastAPI, SQLAlchemy, Alembic, Anthropic SDK. On Render at api.aptly.fyi.
- DB: Postgres (Neon). Auth: Google OAuth, first-party cookie on `.aptly.fyi`.
- Ingestion: per-ATS adapters → `sources` table → one `jobs` table; async fetch, rotation, per-source heartbeat + terminal status; transaction cleared before the network phase (no idle-in-transaction). `INGEST_MAX_PER_RUN=0` = all sources (always-on only); `HOURS_WINDOW` default 720.
- Extension: MV3, plain JS, IIFE-bundled content scripts; separate bearer-token auth (`extension_sessions`); dev-install ("Load unpacked") only.

## 9. Success metrics (early)
- Users completing a profile.
- Tailored resumes generated per user.
- Qualitative: do real international students find the sponsorship-filtered jobs + tailoring genuinely useful? (Primary signal at this stage — get 3–5 real users.)

## 10. Monetization (later)
Free at MVP. Future: subscription tiers (Subscription page already stubbed). Validate value with real users before charging.

## 11. Out of scope (for now)
Auto-apply at scale, paid job-data feeds, non-tech roles, non-US markets, mobile native apps.
