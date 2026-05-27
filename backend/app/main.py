from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.api.profile import router as profile_router
from app.api.tailor import router as tailor_router
from app.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Aptly API", version="0.1.0")

    # SessionMiddleware must wrap the request before any handler that
    # touches `request.session`. SameSite=lax is the right default
    # for an OAuth callback flow: Google → /api/auth/google/callback
    # is a cross-site GET and `lax` allows the cookie on that hop
    # while blocking it on the more dangerous cross-site POST /
    # script contexts. `https_only` is on outside development so the
    # cookie can't ride along on plain-HTTP fetches in production;
    # off in dev so `next dev` over HTTP works.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        same_site="lax",
        https_only=settings.environment != "development",
        # Two-week sessions: long enough that a daily user rarely
        # re-auths, short enough that a lost device's cookie
        # eventually goes stale.
        max_age=60 * 60 * 24 * 14,
    )
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
    app.include_router(profile_router, prefix="/api")
    return app


app = create_app()
