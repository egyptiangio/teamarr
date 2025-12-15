"""FastAPI application factory - Clean V2 API only."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from teamarr.api.routes import cache, channels, epg, health, matching, teams, templates
from teamarr.utilities.logging import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler - runs on startup and shutdown."""
    from teamarr.database import get_db, init_db

    # Startup
    setup_logging()

    # Initialize database
    init_db()

    yield

    # Shutdown - nothing to clean up for now


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Teamarr API",
        description="Sports EPG generation service - V2 Architecture",
        version="2.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # Include API routers - clean V2 API
    app.include_router(health.router, tags=["Health"])
    app.include_router(teams.router, prefix="/api/v1", tags=["Teams"])
    app.include_router(templates.router, prefix="/api/v1", tags=["Templates"])
    app.include_router(epg.router, prefix="/api/v1", tags=["EPG"])
    app.include_router(matching.router, prefix="/api/v1", tags=["Matching"])
    app.include_router(cache.router, prefix="/api/v1", tags=["Cache"])
    app.include_router(channels.router, prefix="/api/v1/channels", tags=["Channels"])

    return app


app = create_app()
