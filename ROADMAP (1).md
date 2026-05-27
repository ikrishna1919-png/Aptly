# Aptly — Roadmap

Living document. Reflects the current state of the build and what's next.

## Phase status

- **Phase 0 — Foundation & deploy.** ✅ Done.
- **Phase 1 — Real job data (multi-ATS).** ✅ Greenhouse, Lever, Workday, SmartRecruiters live. Sources table with validation + auto-prune. Bulk-loaded ~700 candidate Greenhouse/Lever rows from `infra/company_seed.tsv`.
- **Phase 2 — Accounts & auth.** ⏳ Next. Google sign-in only for v1; existing single-profile data preserved as user #1 via `INITIAL_USER_EMAIL`.
- **Phase 3 — Polish.** ✅ First pass (HTML-formatted job descriptions, tailoring questions capped at 6, error handling). Ongoing.
- **Phase 4 — AI tailoring.** ✅ Single-user version working. Custom ATS-expert prompt for analyze + generate. 2-page max DOCX with right-aligned tab stops (no tables — ATS-safe).
- **Phase 5 — Assisted auto-apply.** Not started. Post-MVP.
- **Phase 6 — Scale, sponsorship intelligence, paid feeds.** Sponsorship intelligence is the next big MVP item (see queue below). Paid feeds (TheirStack/Adzuna/etc.) are post-MVP / post-revenue.

## Current MVP queue (run in order)

These are the remaining items to reach a real MVP — something to put in front of international students.

1. **Ashby adapter + bulk seed.** See `infra/tasks/01-ashby-adapter.md`.
2. **Resume-parse background job.** Fixes the 502 on resume upload. See `infra/tasks/02-resume-parse-background.md`.
3. **Google sign-in + multi-user accounts.** Biggest piece. See `infra/tasks/03-google-auth.md`. **Run alone. Test in incognito before merging.**
4. **Sponsorship intelligence via DOL H-1B/LCA data.** The differentiating feature. See `infra/tasks/04-sponsorship-intelligence.md`. Requires Task 3 merged first.

**Workflow:** run one task at a time. Wait for each PR to land and deploy cleanly before starting the next. Tasks 1 and 2 are safe to run in parallel. Tasks 3 and 4 must each run alone in their own cycle.

## Post-MVP (not now)

- Phase 5 — assisted auto-apply (agent-style form fill).
- Phase 6 — paid data feeds for the long tail (TheirStack free tier is the first experiment, gated by license-terms check).
- Render plan upgrade once free tier strains under real user traffic.
- Async + concurrency tuning on ingestion (deferred — bottleneck is network, current setup is acceptable).
- Widen `HOURS_WINDOW` from 48h to 7d once overall coverage is good.
- More ATS adapters (Jobvite, iCIMS, Workable, Recruitee, BambooHR) as demand surfaces.

## Decisions made — do not relitigate

- **Strategy:** own the international-student / H-1B-sponsorship niche; do not chase LinkedIn breadth.
- **Auth (Phase 2):** Google sign-in only for v1. Email/password not in v1 scope.
- **Sponsorship signal:** show two distinct labeled signals (conservative + inclusive). Never label any company "does not sponsor."
- **Existing single-profile data:** preserved as the first user account when auth launches, keyed by an `INITIAL_USER_EMAIL` env var.
- **Paid data feeds:** Phase 6 (post-revenue), not now.
- **JobRight / LinkedIn:** never ingested from. See `CLAUDE.md` "Hard data-sourcing rules."
- **Jobs table layout:** single `jobs` table for all source types. Do not split per source.
