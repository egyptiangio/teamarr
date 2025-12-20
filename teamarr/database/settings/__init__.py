"""Database operations for application settings.

Provides CRUD operations for the settings table (singleton row).
Settings are organized into logical groups for easier management.
"""

from .read import (
    get_all_settings,
    get_dispatcharr_settings,
    get_display_settings,
    get_epg_settings,
    get_lifecycle_settings,
    get_scheduler_settings,
)
from .types import (
    AllSettings,
    APISettings,
    DispatcharrSettings,
    DisplaySettings,
    DurationSettings,
    EPGSettings,
    LifecycleSettings,
    ReconciliationSettings,
    SchedulerSettings,
)
from .update import (
    increment_epg_generation_counter,
    update_dispatcharr_settings,
    update_display_settings,
    update_duration_settings,
    update_epg_settings,
    update_lifecycle_settings,
    update_reconciliation_settings,
    update_scheduler_settings,
)

__all__ = [
    # Types
    "DispatcharrSettings",
    "LifecycleSettings",
    "ReconciliationSettings",
    "SchedulerSettings",
    "EPGSettings",
    "DurationSettings",
    "DisplaySettings",
    "APISettings",
    "AllSettings",
    # Read operations
    "get_all_settings",
    "get_dispatcharr_settings",
    "get_scheduler_settings",
    "get_lifecycle_settings",
    "get_epg_settings",
    "get_display_settings",
    # Update operations
    "update_dispatcharr_settings",
    "update_scheduler_settings",
    "update_lifecycle_settings",
    "update_epg_settings",
    "update_reconciliation_settings",
    "update_duration_settings",
    "update_display_settings",
    "increment_epg_generation_counter",
]
