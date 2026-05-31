from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://aptly:aptly@localhost:5432/aptly",
        alias="DATABASE_URL",
    )
    cors_origins: str = Field(default="http://localhost:3000", alias="CORS_ORIGINS")
    environment: str = Field(default="development", alias="ENVIRONMENT")

    # Rolling-window size for the job feed. Anything older than this is
    # deleted on each ingest pass. Default 720h (30 days). NOTE: the window
    # only takes effect once a run actually FINISHES — the expiry/cleanup
    # step runs at the END of `run_ingest`, so a pass killed mid-run never
    # reaches it. Widening the window therefore does nothing until the host
    # is always-on (Render Starter) and a full pass can complete.
    hours_window: int = Field(default=720, alias="HOURS_WINDOW")

    # Shared secret required by the admin ingest endpoint. The scheduled
    # GitHub Actions workflow sends it in the X-Admin-Token header.
    admin_token: str = Field(default="", alias="ADMIN_TOKEN")

    # Anthropic API key for resume tailoring (Phase 4). Empty string puts the
    # tailoring endpoints into "demo mode" — they return deterministic mock
    # data so nothing crashes when the key isn't configured.
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    # After this many consecutive `last_status='error'` outcomes for a
    # source row, the ingest loop flips `enabled=False` on it so the
    # dead board stops eating a per-source timeout every pass. Tuned
    # for the once-daily scheduled ingest: 3 days of failures is enough
    # signal to park a token, and the operator can re-enable it by hand.
    source_failure_threshold: int = Field(default=3, alias="SOURCE_FAILURE_THRESHOLD")

    # Max number of source fetches in flight at once during the async
    # network phase of `run_ingest`. The bottleneck is network latency,
    # not CPU; 10 is enough to overlap most of the waits without
    # hammering the upstream ATSes. Crank it up for boards that don't
    # rate-limit; lower it if a vendor starts 429ing.
    ingest_concurrency: int = Field(default=10, alias="INGEST_CONCURRENCY")

    # Maximum source rows processed per `run_ingest` invocation, picked
    # `last_run_at ASC NULLS FIRST` so never-checked rows run first and
    # the run rotates through the table over successive scheduled
    # invocations. Bounds wall-clock per run so a single pass always
    # finishes within the scheduled budget — important once `sources`
    # has hundreds of rows. Set to a very large number to disable
    # the cap, or to <= 0 to process ALL enabled sources in one pass
    # (use once the host is always-on so one run can cover everything).
    ingest_max_per_run: int = Field(default=150, alias="INGEST_MAX_PER_RUN")

    # Within a single run, sources are processed in batches: each
    # batch is async-fetched, then sync-written + committed before
    # the next batch's fetch starts. Smaller = more frequent
    # checkpoints (more crash-resilient); larger = better connection
    # pool reuse and slightly less event-loop churn. 25 keeps every
    # 25-source unit of work durable on disk before the next one
    # starts so a mid-run timeout never wipes the whole pass.
    ingest_batch_size: int = Field(default=25, alias="INGEST_BATCH_SIZE")

    # Optional soft wall-clock budget for ONE run, in seconds. 0 (default)
    # = unlimited. When > 0, run_ingest stops STARTING new batches once the
    # budget is exceeded, finishes the batch already in flight, and writes a
    # normal `success` with `stats.budget_truncated = true`; rotation
    # (last_run_at ASC) covers the remaining sources on the next run. Lets an
    # unbounded pass (INGEST_MAX_PER_RUN=0) end CLEANLY on a time-limited /
    # free-tier host instead of being killed mid-fetch (which leaves the run
    # `stale`). Leave at 0 once the host is always-on.
    ingest_run_budget_seconds: int = Field(default=0, alias="INGEST_RUN_BUDGET_SECONDS")

    # A background ingest run writes a heartbeat (progress snapshot +
    # `last_progress_at` timestamp) onto its IngestRun row after every
    # batch. If a run is still `running` but its last heartbeat (or, with
    # no heartbeat yet, its `started_at`) is older than this many minutes,
    # it's treated as dead: the status endpoints REPORT it as `stale`, and
    # the next `POST /admin/ingest` reaps it to `failed` ("superseded/stale").
    # Guards against a sleepy free-tier host killing the worker thread
    # mid-run and leaving the row stuck at `running` forever. 15 min
    # comfortably exceeds a healthy full pass.
    stale_run_minutes: int = Field(default=15, alias="STALE_RUN_MINUTES")

    # ── Google sign-in (Phase 5 multi-user auth) ───────────────────────
    # Client credentials issued in the Google Cloud console. Without
    # these the auth router still loads but the OAuth start endpoint
    # returns a 503 — the rest of the app keeps working for local
    # dev.
    google_client_id: str = Field(default="", alias="GOOGLE_CLIENT_ID")
    google_client_secret: str = Field(default="", alias="GOOGLE_CLIENT_SECRET")
    # The full callback URL Google should redirect to after the
    # consent screen — must match the URI you registered in the
    # Google Cloud console exactly (scheme + host + path).
    # Example: `https://api.aptly.app/api/auth/google/callback`.
    google_redirect_uri: str = Field(default="", alias="GOOGLE_REDIRECT_URI")
    # Signing key for the session cookie. MUST be set to a long
    # random string in production — without it the cookie is signed
    # with a constant default and anyone can forge a session.
    session_secret: str = Field(default="dev-insecure-session-secret", alias="SESSION_SECRET")
    # Where to send the user after a successful OAuth callback. In
    # production this is the deployed frontend (Vercel); in local
    # dev it's the Next.js dev server. **NO default.** Defaulting to
    # `http://localhost:3000` in production caused
    # ERR_CONNECTION_REFUSED on prod sign-ins; the auth callback
    # now raises a clear 500 if this isn't set rather than silently
    # bouncing the user at localhost.
    frontend_url: str = Field(default="", alias="FRONTEND_URL")
    # Optional `Domain` attribute for the session cookie. Set to a
    # parent domain (e.g. `.aptly.fyi`) when the frontend and the
    # backend live on sibling subdomains (`aptly.fyi` +
    # `api.aptly.fyi`) and you want the SAME session cookie to be
    # first-party for both. Leave empty for host-only cookies (the
    # default — correct for local `next dev` and for the legacy
    # Vercel-rewrite-proxy setup where the backend never sees a
    # browser request directly).
    #
    # A leading `.` is preserved on set + on delete so the browser
    # treats both operations as targeting the SAME cookie — without
    # that, `delete_cookie` writes a different scope and the old
    # cookie survives sign-out, breaking re-login.
    cookie_domain: str = Field(default="", alias="COOKIE_DOMAIN")
    # Email of the operator who should inherit the existing
    # pre-multi-user data on first Google sign-in. The migration
    # writes this same value into `users.email` for the bootstrap
    # row. Match this to the Google address you'll sign in with.
    initial_user_email: str = Field(default="owner@example.com", alias="INITIAL_USER_EMAIL")
    # Comma-separated list of Google emails that may use the
    # human-facing admin features (manual-entry job creation /
    # deletion). Empty by default — no users are admins until the
    # operator explicitly enrols them. Compared case-insensitively
    # via `is_admin_email`.
    #
    # NOTE: distinct from `ADMIN_TOKEN`, which is the shared secret
    # the scheduled ingest cron uses. The token gates the
    # server-to-server endpoints (`/admin/ingest`); `ADMIN_EMAILS`
    # gates the user-facing endpoints (`/admin/jobs`).
    admin_emails: str = Field(default="", alias="ADMIN_EMAILS")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def admin_email_list(self) -> list[str]:
        """Normalised list of admin emails, lowercased + stripped.
        Empty list means "no admins" — every admin-gated endpoint
        will 403."""
        return [e.strip().lower() for e in self.admin_emails.split(",") if e.strip()]

    def is_admin_email(self, email: str | None) -> bool:
        """Case-insensitive membership check against `ADMIN_EMAILS`.
        None / empty email → False so a bug in the auth path
        defaults closed."""
        if not email:
            return False
        return email.strip().lower() in self.admin_email_list

    @property
    def has_anthropic_key(self) -> bool:
        return bool(self.anthropic_api_key.strip())

    @property
    def has_google_oauth(self) -> bool:
        """Both halves of the OAuth flow need to be configured before
        we let a user start sign-in: the credentials + redirect URI
        Google needs, AND the `frontend_url` we'll bounce the user
        to after the callback. Without the latter the user would
        complete OAuth and then 500 on the callback — fail at the
        start endpoint instead so the failure mode is "Sign in
        button shows an error" rather than "you've authorized
        Aptly's Google app but can't actually sign in"."""
        return bool(
            self.google_client_id
            and self.google_client_secret
            and self.google_redirect_uri
            and self.frontend_url
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
