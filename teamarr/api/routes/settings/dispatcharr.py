"""Dispatcharr settings and connection endpoints."""

from fastapi import APIRouter

from teamarr.database import get_db

from .models import (
    ConnectionTestRequest,
    ConnectionTestResponse,
    DispatcharrSettingsModel,
    DispatcharrSettingsUpdate,
)

router = APIRouter()


@router.get("/settings/dispatcharr", response_model=DispatcharrSettingsModel)
def get_dispatcharr_settings():
    """Get Dispatcharr integration settings."""
    from teamarr.database.settings import get_dispatcharr_settings

    with get_db() as conn:
        settings = get_dispatcharr_settings(conn)

    return DispatcharrSettingsModel(
        enabled=settings.enabled,
        url=settings.url,
        username=settings.username,
        password="********" if settings.password else None,
        epg_id=settings.epg_id,
        default_channel_profile_ids=settings.default_channel_profile_ids,
        default_stream_profile_id=settings.default_stream_profile_id,
        cleanup_unused_logos=settings.cleanup_unused_logos,
    )


@router.put("/settings/dispatcharr", response_model=DispatcharrSettingsModel)
def update_dispatcharr_settings(update: DispatcharrSettingsUpdate):
    """Update Dispatcharr integration settings."""
    from teamarr.database.settings import (
        get_dispatcharr_settings,
        update_dispatcharr_settings,
    )
    from teamarr.dispatcharr import get_factory

    with get_db() as conn:
        update_dispatcharr_settings(
            conn,
            enabled=update.enabled,
            url=update.url,
            username=update.username,
            password=update.password,
            epg_id=update.epg_id,
            default_channel_profile_ids=update.default_channel_profile_ids,
            default_stream_profile_id=update.default_stream_profile_id,
            cleanup_unused_logos=update.cleanup_unused_logos,
        )

    # Trigger reconnect on next use
    try:
        factory = get_factory()
        factory.reconnect()
    except Exception:
        pass  # Factory may not be initialized yet

    # Return updated settings
    with get_db() as conn:
        settings = get_dispatcharr_settings(conn)

    return DispatcharrSettingsModel(
        enabled=settings.enabled,
        url=settings.url,
        username=settings.username,
        password="********" if settings.password else None,
        epg_id=settings.epg_id,
        default_channel_profile_ids=settings.default_channel_profile_ids,
        default_stream_profile_id=settings.default_stream_profile_id,
        cleanup_unused_logos=settings.cleanup_unused_logos,
    )


@router.post("/dispatcharr/test", response_model=ConnectionTestResponse)
def test_dispatcharr_connection(request: ConnectionTestRequest | None = None):
    """Test connection to Dispatcharr.

    If no parameters provided, tests with saved settings.
    """
    from teamarr.dispatcharr import get_factory

    try:
        factory = get_factory(get_db)
    except RuntimeError:
        # Factory not initialized, create one
        from teamarr.dispatcharr.factory import DispatcharrFactory

        factory = DispatcharrFactory(get_db)

    if request:
        result = factory.test_connection(
            url=request.url,
            username=request.username,
            password=request.password,
        )
    else:
        result = factory.test_connection()

    return ConnectionTestResponse(
        success=result.success,
        url=result.url,
        username=result.username,
        version=result.version,
        account_count=result.account_count,
        group_count=result.group_count,
        channel_count=result.channel_count,
        error=result.error,
    )


@router.get("/dispatcharr/status")
def get_dispatcharr_status() -> dict:
    """Get current Dispatcharr connection status.

    Actually tests the connection to verify it works, not just that
    settings are configured.

    Returns:
        configured: Settings are filled in (enabled, url, username)
        connected: Connection test succeeded
        error: Error message if connection failed (only present on failure)
    """
    from teamarr.dispatcharr import get_factory

    try:
        factory = get_factory(get_db)

        if not factory.is_configured:
            return {
                "configured": False,
                "connected": False,
            }

        # Actually test the connection to verify it works
        result = factory.test_connection()

        response = {
            "configured": True,
            "connected": result.success,
        }

        if not result.success and result.error:
            response["error"] = result.error

        return response

    except RuntimeError:
        return {
            "configured": False,
            "connected": False,
        }


@router.get("/dispatcharr/epg-sources")
def get_dispatcharr_epg_sources(include_dummy: bool = False) -> dict:
    """Get available EPG sources from Dispatcharr.

    Returns list of EPG sources that can be selected for EPG ID.
    Requires Dispatcharr to be configured and connected.
    """
    from teamarr.dispatcharr import get_dispatcharr_connection

    try:
        conn = get_dispatcharr_connection(get_db)
        if not conn:
            return {
                "success": False,
                "error": "Dispatcharr not configured or not connected",
                "sources": [],
            }

        sources = conn.epg.list_sources(include_dummy=include_dummy)
        return {
            "success": True,
            "sources": [
                {
                    "id": s.id,
                    "name": s.name,
                    "source_type": s.source_type,
                    "status": s.status,
                }
                for s in sources
            ],
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "sources": [],
        }
