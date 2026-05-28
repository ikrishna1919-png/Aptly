# How to Start Fresh Tomorrow — Aptly Workflow Guide

You've been in one long thread for 4 days. Starting fresh is the right move — long threads get slow and lose focus. Here's how to restart cleanly so nothing is missing, plus how to use the chat-LLM and Claude Code together efficiently.

---

## STEP 0 — One-time setup (do this now, tonight)
Commit these four files to your repo so they travel with the project and any tool can read them:
- `CLAUDE.md` → repo root (Claude Code reads this automatically every session)
- `ROADMAP.md` → repo root
- `PRD.md` → repo root (or `/docs`)
- Keep `golden_parse_reference.json` at `backend/tests/fixtures/`

This is the single most important habit: **the repo is the source of truth, not the chat.** As long as these are committed and kept current, you can throw away any conversation and lose nothing.

---

## HOW TO START A FRESH CHAT (here / LLM) TOMORROW

Open a new conversation and paste this as your first message:

> I'm building Aptly — a job platform for international students needing visa sponsorship (jobs aggregation + sponsorship signals + AI resume tailoring). It's live at aptly.fyi (frontend on Vercel) and api.aptly.fyi (backend FastAPI on Render), DB on Neon, repo github.com/ikrishna1919-png/Aptly. I work via Claude Code on web; I review PRs (base: main), merge, deploy, test live, and report back.
>
> I'm pasting my CLAUDE.md, ROADMAP.md, and PRD below so you have full context. Today I want to work on: [TODAY'S GOAL].
>
> Please act as my product/engineering partner: help me diagnose from logs/ground-truth before fixing, keep risky changes (auth/migrations) in separate PRs, be honest about tradeoffs, and refuse scraping. When ready, give me precise Claude Code task prompts ending with "Open a PR base: main, merge main first if conflicts, summarize, then stop."
>
> [paste CLAUDE.md]
> [paste ROADMAP.md]
> [paste PRD.md]

Then state your one goal for the day. Keep each chat focused on a theme (e.g. "parsing gaps" or "user testing prep") — start a new chat when you switch themes. Shorter, focused threads stay fast and accurate.

---

## HOW TO START FRESH IN CLAUDE CODE TOMORROW

Claude Code auto-reads `CLAUDE.md` from the repo, so if you committed it (Step 0), it already has context. Start with:

> Read CLAUDE.md, ROADMAP.md, and PRD.md for full context. Today's task: [paste the precise task prompt the chat-LLM gave you]. Diagnose before changing code (reproduce locally / check logs). Open a PR base: main, merge main first if conflicts, summarize what changed and any env vars/migrations needed, then stop.

---

## THE OPTIMIZED LOOP (how to use both tools well)

Think of it as two roles:
- **This chat (LLM) = your architect / product partner.** Use it to think, diagnose, decide what to build, weigh tradeoffs, and WRITE the precise task prompts. Don't have it write big code.
- **Claude Code = your engineer.** Give it the precise prompt; it edits the repo and opens a PR.

The loop:
1. In chat: describe the problem/goal → get a diagnosis + a precise Claude Code task prompt.
2. In Claude Code: paste the task → review the PR (base: main) → merge → deploy.
3. Test live. Grab ground truth if something's wrong (Render logs, `raw_llm_output` query, `npm run build`, browser).
4. Back in chat: paste the error/log/result → get the next precise fix. Repeat.

---

## HABITS THAT SAVE YOU TIME (learned the hard way over 4 days)
1. **Diagnose from ground truth before fixing.** Logs, `raw_llm_output`, local `npm run build`. Guessing burned multiple cycles. When something breaks, get the actual error first.
2. **One concern per PR.** Especially keep auth and migrations separate from cosmetic changes — when something breaks, you can tell what did it.
3. **Update CLAUDE.md / ROADMAP.md as you go.** When state changes (new feature, fixed bug, new env var), update the docs in the same PR. Future-you (and every fresh chat) depends on it.
4. **After a failed Vercel build, fix the build — don't just roll back.** A broken build in `main` blocks the next deploy.
5. **Migrations must be Postgres-valid.** A bad one crashes the whole deploy.
6. **Test the real loop after each merge:** deploy green → click through the affected flow → confirm before moving on.
7. **Don't over-polish.** You've built a real MVP. The highest-value next step is real users, not more UI passes.

---

## TOMORROW'S SUGGESTED FIRST GOALS (pick ONE per chat)
- **A. Finish/verify the logo + favicon PR** (if not merged) and disconnect the stale `aptly-buvg` Vercel project.
- **B. End-to-end test pass:** click the whole flow (land → sign in → profile → jobs → tailor a resume against a real job). Note every gap. Fix the top 2–3.
- **C. Parsing gaps:** for any specific resume that parses wrong, pull `raw_llm_output` and fix that one precise thing against the golden reference.
- **D. (Biggest value) User-testing prep:** get aptly.fyi ready to show 3–5 real international students — write what to ask them, what to watch, a simple feedback form.

My honest recommendation: do B (end-to-end test) first to confirm the product actually works front-to-back, then D (get real users). The building is in good shape; validation is what's missing.

---

## REMEMBER
The repo + these docs are the source of truth. Keep them current and you can start fresh anytime, indefinitely, without losing a thing. Good luck tomorrow.
