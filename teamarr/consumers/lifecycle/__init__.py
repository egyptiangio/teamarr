"""Channel lifecycle management for event-based EPG.

Handles creation and deletion timing for event channels.
Channels are created before events and deleted after.

EPG Association Flow:
1. Generate consistent tvg_id: teamarr-event-{event_id}
2. Create channel in Dispatcharr with this tvg_id
3. Generate XMLTV with matching channel id
4. After EPG refresh, look up EPGData by tvg_id
5. Call set_channel_epg(channel_id, epg_data_id) to associate
"""

import logging
from sqlite3 import Connection
from typing import Any

from .service import ChannelLifecycleService
from .timing import ChannelLifecycleManager
from .types import (
    ChannelCreationResult,
    CreateTiming,
    DeleteTiming,
    DuplicateMode,
    LifecycleDecision,
    StreamProcessResult,
    generate_event_tvg_id,
)

logger = logging.getLogger(__name__)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def get_lifecycle_settings(conn: Connection) -> dict:
    """Get global channel lifecycle settings from the settings table.

    Returns:
        Dict with create_timing, delete_timing, duplicate_handling settings
    """
    cursor = conn.execute(
        """SELECT channel_create_timing, channel_delete_timing,
                  default_duplicate_event_handling
           FROM settings WHERE id = 1"""
    )
    row = cursor.fetchone()

    if row:
        return {
            "create_timing": row["channel_create_timing"] or "same_day",
            "delete_timing": row["channel_delete_timing"] or "day_after",
            "duplicate_handling": row["default_duplicate_event_handling"] or "consolidate",
        }

    return {
        "create_timing": "same_day",
        "delete_timing": "day_after",
        "duplicate_handling": "consolidate",
    }


def create_lifecycle_service(
    db_factory: Any,
    sports_service: Any,
    dispatcharr_client: Any = None,
) -> ChannelLifecycleService:
    """Create a ChannelLifecycleService with optional Dispatcharr integration.

    Args:
        db_factory: Factory function returning database connection
        sports_service: SportsDataService for template resolution (required)
        dispatcharr_client: Optional DispatcharrClient instance

    Returns:
        Configured ChannelLifecycleService

    Raises:
        ValueError: If sports_service is not provided
    """
    from teamarr.database.channels import get_dispatcharr_settings

    with db_factory() as conn:
        settings = get_dispatcharr_settings(conn)
        lifecycle = get_lifecycle_settings(conn)

    channel_manager = None
    logo_manager = None
    epg_manager = None

    if dispatcharr_client and settings.get("enabled"):
        from teamarr.dispatcharr import ChannelManager, EPGManager, LogoManager

        channel_manager = ChannelManager(dispatcharr_client)
        logo_manager = LogoManager(dispatcharr_client)
        epg_manager = EPGManager(dispatcharr_client)

    return ChannelLifecycleService(
        db_factory=db_factory,
        sports_service=sports_service,
        channel_manager=channel_manager,
        logo_manager=logo_manager,
        epg_manager=epg_manager,
        create_timing=lifecycle["create_timing"],
        delete_timing=lifecycle["delete_timing"],
    )


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    # Types
    "CreateTiming",
    "DeleteTiming",
    "DuplicateMode",
    "LifecycleDecision",
    "ChannelCreationResult",
    "StreamProcessResult",
    # Classes
    "ChannelLifecycleManager",
    "ChannelLifecycleService",
    # Functions
    "generate_event_tvg_id",
    "get_lifecycle_settings",
    "create_lifecycle_service",
]
