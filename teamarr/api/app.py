"""FastAPI application factory - Clean V2 API with React UI."""

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from teamarr.api.routes import (
    ai,
    aliases,
    backup,
    cache,
    channels,
    dispatcharr,
    epg,
    groups,
    health,
    keywords,
    matching,
    migration,
    presets,
    settings,
    sort_priorities,
    stats,
    teams,
    templates,
    variables,
)
from teamarr.api.startup_state import StartupPhase, get_startup_state
from teamarr.utilities.logging import setup_logging

logger = logging.getLogger(__name__)


def _cleanup_orphaned_xmltv(conn) -> None:
    """Clean up XMLTV content for disabled or deleted teams/groups.

    Called on startup to ensure no stale XMLTV data persists.
    """
    try:
        # Delete XMLTV for inactive teams
        cursor = conn.execute("""
            DELETE FROM team_epg_xmltv
            WHERE team_id IN (SELECT id FROM teams WHERE active = 0)
        """)
        if cursor.rowcount > 0:
            logger.info("[STARTUP] Cleaned up XMLTV for %d disabled teams", cursor.rowcount)

        # Delete XMLTV for disabled groups
        cursor = conn.execute("""
            DELETE FROM event_epg_xmltv
            WHERE group_id IN (SELECT id FROM event_epg_groups WHERE enabled = 0)
        """)
        if cursor.rowcount > 0:
            logger.info("[STARTUP] Cleaned up XMLTV for %d disabled groups", cursor.rowcount)

        # Delete orphaned XMLTV (team/group no longer exists)
        cursor = conn.execute("""
            DELETE FROM team_epg_xmltv
            WHERE team_id NOT IN (SELECT id FROM teams)
        """)
        if cursor.rowcount > 0:
            logger.info("[STARTUP] Cleaned up %d orphaned team XMLTV entries", cursor.rowcount)

        cursor = conn.execute("""
            DELETE FROM event_epg_xmltv
            WHERE group_id NOT IN (SELECT id FROM event_epg_groups)
        """)
        if cursor.rowcount > 0:
            logger.info("[STARTUP] Cleaned up %d orphaned group XMLTV entries", cursor.rowcount)
    except Exception as e:
        # Log actual error for diagnosis, but don't crash startup
        logger.warning("[STARTUP] XMLTV cleanup failed: %s", e)


def _run_ufc_segment_migration(db_factory, migration_name: str = "ufc_segment_fix_v1"):
    """One-time migration to fix UFC segment handling.

    Clears UFC event cache and managed channels so they're recreated
    with proper segment_times and segment-aware event_ids.

    Uses a migrations table to track completion (runs only once per migration_name).
    """
    try:
        with db_factory() as conn:
            # Create migrations table if it doesn't exist
            conn.execute("""
                CREATE TABLE IF NOT EXISTS migrations (
                    name TEXT PRIMARY KEY,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Check if migration already ran
            cursor = conn.execute(
                "SELECT 1 FROM migrations WHERE name = ?", (migration_name,)
            )
            if cursor.fetchone():
                return  # Already migrated

            # Clear UFC event cache entries
            cursor = conn.execute(
                "DELETE FROM service_cache WHERE cache_key LIKE 'events:ufc:%'"
            )
            events_cleared = cursor.rowcount

            # Delete UFC managed channels (will be recreated with segment IDs)
            cursor = conn.execute(
                "DELETE FROM managed_channels WHERE league = 'ufc'"
            )
            channels_cleared = cursor.rowcount

            # Clear UFC fingerprint cache
            cursor = conn.execute(
                "DELETE FROM stream_match_cache WHERE league = 'ufc'"
            )
            fingerprints_cleared = cursor.rowcount

            # Mark migration as done
            conn.execute(
                "INSERT INTO migrations (name) VALUES (?)", (migration_name,)
            )
            conn.commit()

            if events_cleared or channels_cleared or fingerprints_cleared:
                logger.info(
                    "[MIGRATION] %s: cleared %d events, %d channels, %d fingerprints",
                    migration_name, events_cleared, channels_cleared, fingerprints_cleared,
                )
    except Exception as e:
        logger.warning("[MIGRATION] %s failed: %s", migration_name, e)


def _run_startup_tasks():
    """Run startup tasks in background thread."""
    from teamarr.database import get_db
    from teamarr.database.settings import get_scheduler_settings
    from teamarr.dispatcharr import get_factory
    from teamarr.providers import ProviderRegistry
    from teamarr.services import (
        create_cache_service,
        create_scheduler_service,
        init_league_mapping_service,
    )

    startup_state = get_startup_state()

    try:
        # Initialize services and providers with dependencies
        startup_state.set_phase(StartupPhase.INITIALIZING)
        league_mapping_service = init_league_mapping_service(get_db)
        ProviderRegistry.initialize(league_mapping_service)
        logger.info("[STARTUP] League mapping service and providers initialized")

        # One-time migrations: Clear UFC caches for segment fixes
        # v1: Initial segment_times and segment-aware event_ids
        # v2: Cross-group consolidation and event_date fixes
        # v3: Switch from app API to scoreboard (correct times)
        _run_ufc_segment_migration(get_db, "ufc_segment_fix_v1")
        _run_ufc_segment_migration(get_db, "ufc_segment_fix_v2")
        _run_ufc_segment_migration(get_db, "ufc_segment_fix_v3")

        # Refresh team/league cache based on startup_cache_max_age_days setting
        # 0 = disabled (never auto-refresh), >0 = refresh if older than N days
        startup_state.set_phase(StartupPhase.REFRESHING_CACHE)
        from teamarr.database.settings import get_all_settings

        with get_db() as conn:
            all_settings = get_all_settings(conn)
        max_age_days = all_settings.api.startup_cache_max_age_days

        cache_service = create_cache_service(get_db)
        stats = cache_service.get_stats()

        if max_age_days == 0:
            # Disabled - only refresh if cache is completely empty
            if stats.is_empty:
                logger.info("[STARTUP] Cache is empty, refreshing...")
                cache_service.refresh()
                logger.info("[STARTUP] Team/league cache refreshed")
            else:
                logger.info("[STARTUP] Startup cache refresh disabled, skipping (cache has %d teams)", stats.teams_count)
        else:
            # Check if cache is older than max_age_days
            from datetime import datetime, timedelta, timezone
            is_stale = False
            if stats.last_refresh:
                age = datetime.now(timezone.utc) - stats.last_refresh
                is_stale = age > timedelta(days=max_age_days)

            if stats.is_empty or is_stale:
                logger.info("[STARTUP] Cache is stale (max age: %d days) or empty, refreshing...", max_age_days)
                cache_service.refresh()
                logger.info("[STARTUP] Team/league cache refreshed")
            else:
                logger.info("[STARTUP] Cache is fresh (last refresh: %s, max age: %d days), skipping refresh", stats.last_refresh, max_age_days)

        # Reload league mapping service to pick up new league names from cache
        league_mapping_service.reload()

        # Load display settings from database into config cache
        startup_state.set_phase(StartupPhase.LOADING_SETTINGS)
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
        logger.info("[STARTUP] Display settings loaded into config cache")

        # Initialize Dispatcharr factory (lazy connection)
        startup_state.set_phase(StartupPhase.CONNECTING_DISPATCHARR)
        try:
            factory = get_factory(get_db)
            if factory.is_configured:
                logger.info("[STARTUP] Dispatcharr configured, connection will be established on first use")
            else:
                logger.info("[STARTUP] Dispatcharr not configured")
        except Exception as e:
            logger.warning("[STARTUP] Failed to initialize Dispatcharr factory: %s", e)

        # Start background scheduler if enabled
        startup_state.set_phase(StartupPhase.STARTING_SCHEDULER)
        from teamarr.database.settings import get_epg_settings

        with get_db() as conn:
            scheduler_settings = get_scheduler_settings(conn)
            epg_settings = get_epg_settings(conn)

        if scheduler_settings.enabled:
            try:
                # Get Dispatcharr connection for scheduler (may be None)
                # Must use get_connection() to get the full DispatcharrConnection
                # with .m3u, .channels, .epg managers (not just the raw client)
                connection = None
                try:
                    factory = get_factory()
                    connection = factory.get_connection()
                except Exception as e:
                    logger.debug("[STARTUP] Dispatcharr connection unavailable for scheduler: %s", e)

                scheduler_service = create_scheduler_service(get_db, connection)
                cron_expr = epg_settings.cron_expression or "0 * * * *"
                started = scheduler_service.start(cron_expression=cron_expr)
                if started:
                    logger.info("[STARTUP] Background scheduler started (cron: %s)", cron_expr)
                # Store scheduler service reference for shutdown
                _app_state["scheduler_service"] = scheduler_service
            except Exception as e:
                logger.warning("[STARTUP] Failed to start scheduler: %s", e)
        else:
            logger.info("[STARTUP] Background scheduler disabled")

        startup_state.set_phase(StartupPhase.READY)
        logger.info("[STARTUP] Teamarr V2 ready")

    except Exception as e:
        logger.exception("[STARTUP] Failed: %s", e)
        startup_state.set_error(str(e))


# Store app-level state for cleanup
_app_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler - runs on startup and shutdown."""
    from teamarr.database import get_db, init_db
    from teamarr.database.connection import is_v1_database_detected
    from teamarr.dispatcharr import close_dispatcharr

    # Startup - minimal blocking, then background tasks
    setup_logging()
    logger.info("[STARTUP] Starting Teamarr V2...")

    # Initialize database (fast) - this also detects V1 databases
    init_db()

    # If V1 database detected, skip V2 initialization - only serve migration endpoints
    if is_v1_database_detected():
        logger.warning("[STARTUP] V1 database detected - running in migration mode only")
        yield
        logger.info("[SHUTDOWN] Teamarr V2 stopped (migration mode)")
        return

    # Normal V2 startup continues...
    # Cleanup any stuck processing runs from previous crashes
    from teamarr.database.stats import cleanup_stuck_runs

    with get_db() as conn:
        cleanup_stuck_runs(conn)

        # Clean up orphaned XMLTV content (disabled teams/groups, deleted entries)
        _cleanup_orphaned_xmltv(conn)

    # Start background startup tasks (cache refresh, etc.)
    startup_thread = threading.Thread(target=_run_startup_tasks, daemon=True)
    startup_thread.start()

    yield

    # Shutdown
    logger.info("[SHUTDOWN] Shutting down Teamarr V2...")

    # Stop scheduler
    scheduler_service = _app_state.get("scheduler_service")
    if scheduler_service:
        scheduler_service.stop()

    # Close Dispatcharr connection
    close_dispatcharr()

    logger.info("[SHUTDOWN] Teamarr V2 stopped")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    from teamarr.config import BASE_VERSION
    from teamarr.database.connection import is_v1_database_detected

    app = FastAPI(
        title="Teamarr API",
        description="Sports EPG generation service - V2 Architecture",
        version=BASE_VERSION,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # Migration mode middleware - block all API routes except migration endpoints
    @app.middleware("http")
    async def migration_mode_middleware(request: Request, call_next):
        if is_v1_database_detected():
            path = request.url.path
            # Allow these routes in migration mode:
            # - /health (health check)
            # - /api/v1/migration/* (migration endpoints)
            # - Static files and SPA routes (no /api prefix)
            if path.startswith("/api/") and not path.startswith("/api/v1/migration"):
                return JSONResponse(
                    status_code=503,
                    content={
                        "detail": "V1 database detected - migration required",
                        "migration_mode": True,
                    },
                )
        return await call_next(request)

    # Include API routers - clean V2 API
    app.include_router(health.router, tags=["Health"])
    app.include_router(teams.router, prefix="/api/v1", tags=["Teams"])
    app.include_router(templates.router, prefix="/api/v1", tags=["Templates"])
    app.include_router(presets.router, prefix="/api/v1/presets", tags=["Condition Presets"])
    app.include_router(groups.router, prefix="/api/v1/groups", tags=["Event Groups"])
    app.include_router(aliases.router, prefix="/api/v1", tags=["Team Aliases"])
    app.include_router(epg.router, prefix="/api/v1", tags=["EPG"])
    app.include_router(matching.router, prefix="/api/v1", tags=["Matching"])
    app.include_router(keywords.router, prefix="/api/v1/keywords", tags=["Exception Keywords"])
    app.include_router(cache.router, prefix="/api/v1", tags=["Cache"])
    app.include_router(channels.router, prefix="/api/v1/channels", tags=["Channels"])
    app.include_router(settings.router, prefix="/api/v1", tags=["Settings"])
    app.include_router(sort_priorities.router, prefix="/api/v1", tags=["Sort Priorities"])
    app.include_router(stats.router, prefix="/api/v1/stats", tags=["Stats"])
    app.include_router(variables.router, prefix="/api/v1", tags=["Variables"])
    app.include_router(dispatcharr.router, prefix="/api/v1", tags=["Dispatcharr"])
    app.include_router(migration.router, prefix="/api/v1", tags=["Migration"])
    app.include_router(backup.router, prefix="/api/v1", tags=["Backup"])
    app.include_router(ai.router, prefix="/api/v1/ai", tags=["AI"])

    # Serve React UI static files
    frontend_dist = Path(__file__).parent.parent.parent / "frontend" / "dist"
    if frontend_dist.exists():
        # Serve static assets (JS, CSS, etc.)
        app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

        # Serve index.html for all non-API routes (SPA routing)
        @app.get("/{path:path}", include_in_schema=False)
        async def serve_spa(path: str):
            # IMPORTANT: Never serve SPA for API routes - let them 404 naturally
            # This prevents the catch-all from hijacking API requests
            if path.startswith("api/"):
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail="Not found")

            # Serve static files if they exist (favicon, etc.)
            file_path = frontend_dist / path
            if file_path.exists() and file_path.is_file():
                return FileResponse(file_path)

            # Fall back to index.html for SPA routing
            return FileResponse(frontend_dist / "index.html")

        logger.info("[STARTUP] Serving React UI from %s", frontend_dist)
    else:
        logger.warning("[STARTUP] Frontend dist not found at %s - UI not available", frontend_dist)

    return app


app = create_app()
