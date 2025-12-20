"""Channel lifecycle management for event-based EPG.

DEPRECATED: This module is maintained for backward compatibility.
Import from teamarr.consumers.lifecycle instead.
"""

# Re-export everything from the new package for backward compatibility
from teamarr.consumers.lifecycle import (
    ChannelCreationResult,
    ChannelLifecycleManager,
    ChannelLifecycleService,
    CreateTiming,
    DeleteTiming,
    DuplicateMode,
    LifecycleDecision,
    StreamProcessResult,
    create_lifecycle_service,
    generate_event_tvg_id,
    get_lifecycle_settings,
)

__all__ = [
    "CreateTiming",
    "DeleteTiming",
    "DuplicateMode",
    "LifecycleDecision",
    "ChannelCreationResult",
    "StreamProcessResult",
    "ChannelLifecycleManager",
    "ChannelLifecycleService",
    "generate_event_tvg_id",
    "get_lifecycle_settings",
    "create_lifecycle_service",
]
