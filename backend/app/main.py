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
    # touches `request.session`.
    #
    # Cookie attrs are environment-dependent:
    #   * Production: SameSite=None + Secure=True. The Vercel
    #     frontend and the Render backend are on different origins,
    #     so the session cookie must be sent on cross-site fetches —
    #     `Lax` silently drops the cookie on the cross-origin AJAX
    #     call from the frontend's auth bootstrap, leaving the user
    #     looking permanently signed out. `None` requires `Secure`,
    #     which is fine on HTTPS.
    #   * Local dev: SameSite=Lax + Secure=False so `next dev` over
    #     plain HTTP at `http://localhost:3000` works. Cookies with
    #     `Secure` won't ride on http:// hops; cookies with
    #     `SameSite=none` REQUIRE Secure, so lax is the only option
    #     that survives the localhost flow.
    is_dev = settings.environment == "development"
    same_site = "lax" if is_dev else "none"
    https_only = not is_dev
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        same_site=same_site,
        https_only=https_only,
        # Two-week sessions: long enough that a daily user rarely
        # re-auths, short enough that a lost device's cookie
        # eventually goes stale.
        max_age=60 * 60 * 24 * 14,
    )
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
    app.include_router(profile_router, prefix="/api")
    return app


app = create_app()
