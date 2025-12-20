"""FastAPI application factory - Clean V2 API with React UI."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from teamarr.api.routes import (
    cache,
    channels,
    dispatcharr,
    epg,
    groups,
    health,
    keywords,
    matching,
    presets,
    settings,
    stats,
    teams,
    templates,
    variables,
)
from teamarr.utilities.logging import setup_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler - runs on startup and shutdown."""
    from teamarr.consumers import start_lifecycle_scheduler, stop_lifecycle_scheduler
    from teamarr.database import get_db, init_db
    from teamarr.database.settings import get_scheduler_settings
    from teamarr.dispatcharr import close_dispatcharr, get_factory
    from teamarr.providers import ProviderRegistry
    from teamarr.services import init_league_mapping_service

    # Startup
    setup_logging()
    logger.info("Starting Teamarr V2...")

    # Initialize database
    init_db()

    # Initialize services and providers with dependencies
    league_mapping_service = init_league_mapping_service(get_db)
    ProviderRegistry.initialize(league_mapping_service)
    logger.info("League mapping service and providers initialized")

    # Auto-refresh team/league cache if empty or stale
    from teamarr.consumers.cache import CacheRefresher

    cache_refresher = CacheRefresher(get_db)
    if cache_refresher.refresh_if_needed(max_age_days=7):
        logger.info("Team/league cache refreshed on startup")

    # Load display settings from database into config cache
    from teamarr.config import set_display_settings, set_timezone
    from teamarr.database.settings import get_display_settings, get_epg_settings

    with get_db() as conn:
        # Load timezone
        epg_settings = get_epg_settings(conn)
        set_timezone(epg_settings.epg_timezone)

        # Load display settings
        display = get_display_settings(conn)
        set_display_settings(
            time_format=display.time_format,
            show_timezone=display.show_timezone,
            channel_id_format=display.channel_id_format,
            xmltv_generator_name=display.xmltv_generator_name,
            xmltv_generator_url=display.xmltv_generator_url,
        )
    logger.info("Display settings loaded into config cache")

    # Initialize Dispatcharr factory (lazy connection)
    try:
        factory = get_factory(get_db)
        if factory.is_configured:
            logger.info("Dispatcharr configured, connection will be established on first use")
        else:
            logger.info("Dispatcharr not configured")
    except Exception as e:
        logger.warning(f"Failed to initialize Dispatcharr factory: {e}")

    # Start background scheduler if enabled
    with get_db() as conn:
        scheduler_settings = get_scheduler_settings(conn)

    if scheduler_settings.enabled:
        try:
            # Get Dispatcharr client for scheduler (may be None)
            client = None
            try:
                factory = get_factory()
                client = factory.get_client()
            except Exception:
                pass

            started = start_lifecycle_scheduler(
                db_factory=get_db,
                interval_minutes=scheduler_settings.interval_minutes,
                dispatcharr_client=client,
            )
            if started:
                interval = scheduler_settings.interval_minutes
                logger.info(f"Background scheduler started (interval: {interval} min)")
        except Exception as e:
            logger.warning(f"Failed to start scheduler: {e}")
    else:
        logger.info("Background scheduler disabled")

    logger.info("Teamarr V2 ready")

    yield

    # Shutdown
    logger.info("Shutting down Teamarr V2...")

    # Stop scheduler
    stop_lifecycle_scheduler()

    # Close Dispatcharr connection
    close_dispatcharr()

    logger.info("Teamarr V2 stopped")


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
    app.include_router(presets.router, prefix="/api/v1/presets", tags=["Condition Presets"])
    app.include_router(groups.router, prefix="/api/v1/groups", tags=["Event Groups"])
    app.include_router(epg.router, prefix="/api/v1", tags=["EPG"])
    app.include_router(matching.router, prefix="/api/v1", tags=["Matching"])
    app.include_router(keywords.router, prefix="/api/v1/keywords", tags=["Exception Keywords"])
    app.include_router(cache.router, prefix="/api/v1", tags=["Cache"])
    app.include_router(channels.router, prefix="/api/v1/channels", tags=["Channels"])
    app.include_router(settings.router, prefix="/api/v1", tags=["Settings"])
    app.include_router(stats.router, prefix="/api/v1/stats", tags=["Stats"])
    app.include_router(variables.router, prefix="/api/v1", tags=["Variables"])
    app.include_router(dispatcharr.router, prefix="/api/v1", tags=["Dispatcharr"])

    # Serve React UI static files
    frontend_dist = Path(__file__).parent.parent.parent / "frontend" / "dist"
    if frontend_dist.exists():
        # Serve static assets (JS, CSS, etc.)
        app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

        # Serve index.html for all non-API routes (SPA routing)
        # Note: This catch-all route has lowest priority since it's added last
        @app.get("/{path:path}", include_in_schema=False)
        async def serve_spa(path: str):
            # Serve static files if they exist (favicon, etc.)
            file_path = frontend_dist / path
            if file_path.exists() and file_path.is_file():
                return FileResponse(file_path)

            # Fall back to index.html for SPA routing
            return FileResponse(frontend_dist / "index.html")

        logger.info(f"Serving React UI from {frontend_dist}")
    else:
        logger.warning(f"Frontend dist not found at {frontend_dist} - UI not available")

    return app


app = create_app()
