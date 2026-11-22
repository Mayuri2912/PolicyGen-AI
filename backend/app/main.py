"""FastAPI application entry point.

Run with:  uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import routes_auth, routes_analysis, routes_files, routes_jobs, routes_policies, routes_reports, routes_system
from app.core.config import BACKEND_DIR, get_settings
from app.core.errors import NotFoundError, register_error_handlers
from app.core.logging import configure_logging, get_logger
from app.core.security import ApiGuardMiddleware, RateLimiter
from app.database.session import init_db
from app.services import jobs, retention

logger = get_logger("app")
rate_limiter = RateLimiter()

# A production build of the frontend, when present, is served by the backend
# so the whole application runs from one process.
FRONTEND_DIST = BACKEND_DIR.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    init_db()
    jobs.recover_interrupted_jobs()
    retention.sweep()
    retention.start()
    logger.info("application started", extra={"environment": settings.environment, "version": __version__})
    yield
    retention.stop()
    jobs.runner.shutdown()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="PolicyGen AI",
        version=__version__,
        description="Upload organizational documents, analyze them with AI and generate policy documents.",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.add_middleware(ApiGuardMiddleware, settings=settings, limiter=rate_limiter)
    # Added last so it is the outermost layer: CORS headers are then present
    # on every response, including 401 and 429 produced by the guard.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["Content-Disposition", "Retry-After"],
    )
    register_error_handlers(app)
    for module in (routes_auth, routes_system, routes_files, routes_analysis, routes_policies, routes_reports, routes_jobs):
        app.include_router(module.router, prefix="/api")

    if (FRONTEND_DIST / "index.html").is_file():
        _serve_frontend(app, FRONTEND_DIST)
    return app


def _serve_frontend(app: FastAPI, dist: Path) -> None:
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise NotFoundError("Unknown API endpoint.")
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(dist / "index.html")


app = create_app()
