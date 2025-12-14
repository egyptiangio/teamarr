"""FastAPI application factory."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from teamarr.api.routes import cache, channels, epg, frontend, health, matching, teams, templates
from teamarr.ui import router as ui_router
from teamarr.utilities.logging import setup_logging

# Static files directory
STATIC_DIR = Path(__file__).parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler - runs on startup and shutdown."""
    from teamarr.consumers import start_lifecycle_scheduler, stop_lifecycle_scheduler
    from teamarr.database import get_db

    # Startup
    setup_logging()

    # Start background scheduler
    try:
        start_lifecycle_scheduler(get_db)
    except Exception as e:
        # Log but don't fail startup if scheduler fails
        import logging

        logging.getLogger(__name__).warning(f"Failed to start lifecycle scheduler: {e}")

    yield

    # Shutdown
    stop_lifecycle_scheduler()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Teamarr API",
        description="Sports EPG generation service",
        version="2.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # Mount static files
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Include API routers
    app.include_router(health.router, tags=["Health"])
    app.include_router(teams.router, prefix="/api/v1", tags=["Teams"])
    app.include_router(templates.router, prefix="/api/v1", tags=["Templates"])
    app.include_router(epg.router, prefix="/api/v1", tags=["EPG"])
    app.include_router(matching.router, prefix="/api/v1", tags=["Matching"])
    app.include_router(cache.router, prefix="/api/v1", tags=["Cache"])
    app.include_router(channels.router, prefix="/api/v1/channels", tags=["Channels"])

    # Frontend API routes (serves UI's /api/* calls using V2 infrastructure)
    app.include_router(frontend.router, tags=["Frontend"])

    # Include UI routes (must be last to avoid conflicts with API routes)
    app.include_router(ui_router, tags=["UI"])

    return app


app = create_app()
