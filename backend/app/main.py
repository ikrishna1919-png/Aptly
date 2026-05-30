from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.api.admin import router as admin_router
from app.api.ats import router as ats_router
from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.api.profile import router as profile_router
from app.api.tailor import router as tailor_router
from app.config import get_settings
from app.services.profile_parser import sweep_orphaned_parse_runs
from app.services.tailor_runs import sweep_orphaned_tailor_runs


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Reap rows left at a non-terminal status by a process that died
    # mid-job — the worker's `finally` can't write a terminal status if
    # the process itself is killed. Running this once per boot means a
    # stuck row can't outlive the restart that orphaned it, so the
    # polling client never waits out its full ceiling on a dead job.
    sweep_orphaned_parse_runs()
    sweep_orphaned_tailor_runs()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Aptly API", version="0.1.0", lifespan=_lifespan)

    # SessionMiddleware must wrap the request before any handler that
    # touches `request.session`.
    #
    # Cookie attribute rules of thumb:
    #
    #   * `SameSite=Lax` everywhere. Correct for the production
    #     setup (frontend on `aptly.fyi`, backend on
    #     `api.aptly.fyi` — same site, so Lax considers them
    #     same-site for cookie purposes). Survives Safari ITP and
    #     Chrome / Firefox incognito (which both block the
    #     `SameSite=None` third-party cookies we'd otherwise need).
    #     `None` is never the right answer once same-site cookies
    #     work.
    #   * `Secure` tracks the environment — HTTPS-only in
    #     production, plain HTTP for local `next dev`.
    #   * `Domain=COOKIE_DOMAIN` (when set) lets the SAME session
    #     cookie cover both subdomains, so the user's `aptly.fyi`
    #     browser session is recognised when JS / browser navigates
    #     to `api.aptly.fyi`. Leave the env var empty for local
    #     dev OR for the legacy Vercel-rewrite-proxy setup (host-
    #     only is correct when the browser never touches the
    #     backend origin directly).
    is_dev = settings.environment == "development"
    same_site = "lax"
    https_only = not is_dev
    session_kwargs: dict[str, Any] = {
        "secret_key": settings.session_secret,
        "same_site": same_site,
        "https_only": https_only,
        # Two-week sessions: long enough that a daily user rarely
        # re-auths, short enough that a lost device's cookie
        # eventually goes stale.
        "max_age": 60 * 60 * 24 * 14,
    }
    if settings.cookie_domain:
        session_kwargs["domain"] = settings.cookie_domain
    app.add_middleware(SessionMiddleware, **session_kwargs)
    # CORS must explicitly list the frontend origin — browsers reject
    # `Access-Control-Allow-Origin: *` together with
    # `Access-Control-Allow-Credentials: true`. The `cors_origin_list`
    # property splits `CORS_ORIGINS` on commas; make sure
    # `FRONTEND_URL` is in there for prod.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router, prefix="/api")
    app.include_router(auth_router, prefix="/api")
    app.include_router(jobs_router, prefix="/api")
    app.include_router(admin_router, prefix="/api")
    app.include_router(tailor_router, prefix="/api")
    app.include_router(ats_router, prefix="/api")
    app.include_router(profile_router, prefix="/api")
    return app


app = create_app()
