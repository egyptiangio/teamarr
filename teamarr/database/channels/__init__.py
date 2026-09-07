"""Database operations for managed channels.

Provides CRUD operations for the managed_channels table and related tables
(managed_channel_streams, managed_channel_history, consolidation_exception_keywords).
"""

# Types
# CRUD operations
from .crud import (
    create_managed_channel,
    find_any_channel_for_event,
    find_existing_channel,
    get_all_managed_channels,
    get_channels_pending_deletion,
    get_managed_channel,
    get_managed_channel_by_dispatcharr_id,
    get_managed_channel_by_event,
    get_managed_channel_by_tvg_id,
    get_managed_channels_for_group,
    mark_channel_deleted,
    update_managed_channel,
)

# History operations
from .history import (
    cleanup_old_history,
    get_channel_history,
    log_channel_history,
)

# Keywords operations
from .keywords import (
    check_exception_keyword,
    get_exception_keywords,
)

# Settings helpers
from .settings_helpers import (
    get_dispatcharr_settings,
    get_reconciliation_settings,
    get_scheduler_settings,
)

# Stream operations
from .streams import (
    add_stream_to_channel,
    compute_stream_priority_from_rules,
    get_all_channel_streams,
    get_all_ordered_stream_ids,
    get_channel_streams,
    get_next_stream_priority,
    get_ordered_stream_ids,
    remove_stream_from_channel,
    reorder_channel_streams,
    stream_exists_on_channel,
    update_stream_account_name,
    update_stream_channel_source_group,
    update_stream_feed_side,
    update_stream_feed_team,
    update_stream_name,
    update_stream_priority,
    update_stream_window,
)
from .types import ManagedChannel, ManagedChannelStream

__all__ = [
    # Types
    "ManagedChannel",
    "ManagedChannelStream",
    # CRUD
    "create_managed_channel",
    "get_managed_channel",
    "get_managed_channel_by_tvg_id",
    "get_managed_channel_by_event",
    "get_managed_channel_by_dispatcharr_id",
    "get_managed_channels_for_group",
    "get_channels_pending_deletion",
    "get_all_managed_channels",
    "update_managed_channel",
    "mark_channel_deleted",
    "find_existing_channel",
    "find_any_channel_for_event",
    # Streams
    "add_stream_to_channel",
    "compute_stream_priority_from_rules",
    "get_all_channel_streams",
    "get_all_ordered_stream_ids",
    "get_channel_streams",
    "get_next_stream_priority",
    "get_ordered_stream_ids",
    "remove_stream_from_channel",
    "reorder_channel_streams",
    "stream_exists_on_channel",
    "update_stream_account_name",
    "update_stream_channel_source_group",
    "update_stream_feed_side",
    "update_stream_feed_team",
    "update_stream_name",
    "update_stream_priority",
    "update_stream_window",
    # History
    "log_channel_history",
    "get_channel_history",
    "cleanup_old_history",
    # Keywords
    "get_exception_keywords",
    "check_exception_keyword",
    # Settings helpers
    "get_dispatcharr_settings",
    "get_reconciliation_settings",
    "get_scheduler_settings",
]
