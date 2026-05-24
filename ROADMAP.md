# Aptly — Build Roadmap (the real product)

This is the plan for turning the prototype into a strong, real product over
time. It's sequenced so each phase ships something usable and de-risks the
next. Don't try to do it all at once — each phase is roughly 1–3 weeks of
focused work.

> **Mindset:** You are NOT rebuilding LinkedIn. LinkedIn's strength is its
> network and data, built by hundreds of engineers over 15+ years. Your wedge
> is narrower and winnable: **aggregate real jobs → filter for things that
> matter (sponsorship, location) → AI-tailor a resume per role → assist the
> apply.** Compete on that, not on cloning a social network.

---

## The stack (production-grade, not over-engineered)

- **Backend:** FastAPI (Python) — keep it; it's already structured for this.
- **Database:** PostgreSQL via a managed host (Neon or Supabase free tier).
- **Frontend:** Next.js (React) + Tailwind + shadcn/ui. This is the realistic
  path to a polished, "real product" look. It replaces the single-file HTML.
- **Auth:** Clerk or Supabase Auth. Do NOT roll your own auth.
- **Background jobs:** a scheduled worker for ingestion (cron to start; RQ /
  Celery later).
- **AI:** Claude API for resume tailoring and resume parsing.
- **Hosting:** Vercel (frontend) + Railway/Render/Fly.io (backend) + managed
  Postgres. All have usable free tiers to start.

Tooling: do this in a **real git repo with Claude Code**, not in chat. Chat is
good for one-off pieces; a product gets built file-by-file in the repo.

---

## Data strategy (the make-or-break)

**Never scrape LinkedIn / Indeed / Glassdoor directly.** Aggressive anti-bot
defenses + real legal risk.

Three tiers, add them in order:

1. **Public ATS APIs (free, legal, no auth)** — start here.
   - Greenhouse: `https://boards-api.greenhouse.io/v1/boards/{token}/jobs`
   - Lever: `https://api.lever.co/v0/postings/{company}?mode=json`
   - Also: Ashby, Workable, Recruitee.
   - Curate a list of 50–150 company board tokens (the companies you care
     about — e.g. ones known to sponsor visas). This alone gives real,
     current, structured jobs.
2. **Aggregator API for breadth** — Adzuna (free tier, reputable, salary data).
3. **Paid multi-source at scale** — TheirStack or similar (~$0.0015–0.039/job,
   dedupes across boards + ATS). Add only when you have users and need volume.

Design for data quality from day one: **deduplicate** across sources (hash of
company+title+location), track a **freshness** timestamp, and flag likely
**ghost jobs** (very old `updated_at`, reposted repeatedly).

---

## Phases

### Phase 0 — Foundation (repo + deploy early)
Get the skeleton into a proper repo and deployed *before* it's impressive, so
deployment is never the scary unknown later.
- Move the prototype into a clean repo (backend + a fresh Next.js frontend).
- Stand up managed Postgres; run migrations (Alembic).
- Deploy a "hello world" backend + frontend to your hosts. Wire CI (GitHub
  Actions: lint + test on push).
- **Ships:** a live URL that does almost nothing — but the pipeline works.

### Phase 1 — Real data
The single biggest credibility jump. Replace sample jobs with live ones.
- Build a `GreenhouseSource` and `LeverSource` (subclass the existing
  `JobSource` interface — the prototype is already built for this).
- Curate your company list. Ingest on a schedule (cron every few hours).
- Add dedup + freshness + ghost-job flagging in the ingestion step.
- **Ships:** a board showing hundreds of real, current jobs you can filter.

### Phase 2 — Accounts & profiles
- Add auth (Clerk/Supabase). Scope all data per user.
- **Resume upload + AI parsing:** user uploads PDF/DOCX → Claude parses it into
  the structured candidate shape (the schema already exists).
- Saved jobs, dismissed jobs.
- **Ships:** real users with real profiles, persisted.

### Phase 3 — The polished frontend
This is where it stops looking "basic."
- Rebuild the UI in Next.js + Tailwind + shadcn/ui.
- Proper job-detail pages, server-side filtering + pagination, fast search
  (Postgres full-text first; Typesense/Meilisearch later if needed).
- Responsive, accessible, loading/empty/error states everywhere.
- **Ships:** a product that feels real.

### Phase 4 — AI tailoring at quality
- Per-job JD analysis, match scoring, gap questions, ATS-optimized rewrite
  (the prototype has the skeleton — harden the prompts and add caching).
- DOCX + PDF export (DOCX already works via python-docx).
- Cache analyses; batch where possible to control API cost.
- **Ships:** the core differentiator, polished.

### Phase 5 — Assisted apply (tread carefully)
**This is the biggest legal/ToS landmine — do it the safe way.**
- Store the user's answers to common application questions.
- For ATS-powered postings, prefer **apply-by-API / pre-filled apply URLs**
  over browser automation.
- Keep a **human in the loop** for submission. Do NOT build bots that
  auto-submit through sites that forbid automation — that gets users' accounts
  banned and exposes you to liability.
- **Ships:** one-click *prepared* applications, user confirms send.

### Phase 6 — Scale & differentiate
- Add a paid aggregator for volume + coverage.
- **Sponsorship intelligence** (your wedge): enrich postings with visa-sponsor
  data (e.g. public H-1B disclosure datasets) so "actually sponsors" is
  trustworthy, not guessed.
- Job alerts (email), saved searches, application tracking, basic analytics.

---

## Known hard parts (so they don't surprise you)
- **Data freshness & ghost jobs** — ~1 in 5 postings is stale/fake. Filter
  aggressively or users lose trust.
- **Dedup across sources** — the same job appears many times.
- **Auto-apply legality** — the #1 risk. Stay human-in-the-loop.
- **AI cost at scale** — cache analyses, batch, and don't re-tailor unchanged
  inputs.
- **Operational drift** — sources change formats and break silently. Add
  monitoring + alerts on ingestion volume.

---

## Suggested first move
Phase 0 then Phase 1. The highest-leverage single task is the **Greenhouse +
Lever ingestion adapter** — that's what turns this from a demo into a real
job board. Everything else builds on having real data flowing in.
