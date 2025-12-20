"""Channel database types and dataclasses.

Data types for managed channels, streams, and exception keywords.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ManagedChannel:
    """A managed channel from the database."""

    id: int
    event_epg_group_id: int
    event_id: str
    event_provider: str
    tvg_id: str
    channel_name: str
    channel_number: str | None = None
    logo_url: str | None = None

    # Dispatcharr integration
    dispatcharr_channel_id: int | None = None
    dispatcharr_uuid: str | None = None
    dispatcharr_logo_id: int | None = None

    # Channel settings
    channel_group_id: int | None = None
    stream_profile_id: int | None = None
    channel_profile_ids: list[int] = field(default_factory=list)
    primary_stream_id: int | None = None
    exception_keyword: str | None = None

    # Event context
    home_team: str | None = None
    away_team: str | None = None
    event_date: datetime | None = None
    event_name: str | None = None
    league: str | None = None
    sport: str | None = None

    # Lifecycle
    scheduled_delete_at: datetime | None = None
    deleted_at: datetime | None = None
    delete_reason: str | None = None

    # Sync status
    sync_status: str = "pending"
    sync_message: str | None = None
    last_verified_at: datetime | None = None

    # Timestamps
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_row(cls, row: dict) -> "ManagedChannel":
        """Create from database row dict."""
        profile_ids = row.get("channel_profile_ids")
        if profile_ids and isinstance(profile_ids, str):
            try:
                profile_ids = json.loads(profile_ids)
            except json.JSONDecodeError:
                profile_ids = []

        return cls(
            id=row["id"],
            event_epg_group_id=row["event_epg_group_id"],
            event_id=row["event_id"],
            event_provider=row["event_provider"],
            tvg_id=row["tvg_id"],
            channel_name=row["channel_name"],
            channel_number=row.get("channel_number"),
            logo_url=row.get("logo_url"),
            dispatcharr_channel_id=row.get("dispatcharr_channel_id"),
            dispatcharr_uuid=row.get("dispatcharr_uuid"),
            dispatcharr_logo_id=row.get("dispatcharr_logo_id"),
            channel_group_id=row.get("channel_group_id"),
            stream_profile_id=row.get("stream_profile_id"),
            channel_profile_ids=profile_ids or [],
            primary_stream_id=row.get("primary_stream_id"),
            exception_keyword=row.get("exception_keyword"),
            home_team=row.get("home_team"),
            away_team=row.get("away_team"),
            event_date=row.get("event_date"),
            event_name=row.get("event_name"),
            league=row.get("league"),
            sport=row.get("sport"),
            scheduled_delete_at=row.get("scheduled_delete_at"),
            deleted_at=row.get("deleted_at"),
            delete_reason=row.get("delete_reason"),
            sync_status=row.get("sync_status", "pending"),
            sync_message=row.get("sync_message"),
            last_verified_at=row.get("last_verified_at"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )


@dataclass
class ManagedChannelStream:
    """A stream attached to a managed channel."""

    id: int
    managed_channel_id: int
    dispatcharr_stream_id: int
    stream_name: str | None = None
    source_group_id: int | None = None
    source_group_type: str = "parent"
    priority: int = 0
    m3u_account_id: int | None = None
    m3u_account_name: str | None = None
    exception_keyword: str | None = None
    added_at: datetime | None = None
    removed_at: datetime | None = None

    @classmethod
    def from_row(cls, row: dict) -> "ManagedChannelStream":
        """Create from database row dict."""
        return cls(
            id=row["id"],
            managed_channel_id=row["managed_channel_id"],
            dispatcharr_stream_id=row["dispatcharr_stream_id"],
            stream_name=row.get("stream_name"),
            source_group_id=row.get("source_group_id"),
            source_group_type=row.get("source_group_type", "parent"),
            priority=row.get("priority", 0),
            m3u_account_id=row.get("m3u_account_id"),
            m3u_account_name=row.get("m3u_account_name"),
            exception_keyword=row.get("exception_keyword"),
            added_at=row.get("added_at"),
            removed_at=row.get("removed_at"),
        )
