"""Dataclasses for Dispatcharr API responses.

These types represent the data structures returned by the Dispatcharr API.
All types are frozen dataclasses for immutability and hashability.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DispatcharrChannel:
    """A channel in Dispatcharr."""

    id: int
    uuid: str
    name: str
    channel_number: str
    tvg_id: str | None = None
    channel_group_id: int | None = None
    channel_group_name: str | None = None
    logo_id: int | None = None
    logo_url: str | None = None
    stream_profile_id: int | None = None
    streams: tuple[int, ...] = field(default_factory=tuple)

    @classmethod
    def from_api(cls, data: dict) -> "DispatcharrChannel":
        """Create from API response dict."""
        streams = data.get("streams", [])
        if isinstance(streams, list):
            streams = tuple(streams)
        return cls(
            id=data["id"],
            uuid=data.get("uuid", ""),
            name=data.get("name", ""),
            channel_number=str(data.get("channel_number", "")),
            tvg_id=data.get("tvg_id"),
            channel_group_id=data.get("channel_group_id"),
            channel_group_name=data.get("channel_group_name"),
            logo_id=data.get("logo_id"),
            logo_url=data.get("logo_url"),
            stream_profile_id=data.get("stream_profile_id"),
            streams=streams,
        )


@dataclass(frozen=True)
class DispatcharrStream:
    """A stream from an M3U source in Dispatcharr."""

    id: int
    name: str
    url: str | None = None
    channel_group: str | None = None
    channel_group_id: int | None = None
    tvg_id: str | None = None
    tvg_name: str | None = None
    tvg_logo: str | None = None
    m3u_account_id: int | None = None
    m3u_account_name: str | None = None

    @classmethod
    def from_api(cls, data: dict) -> "DispatcharrStream":
        """Create from API response dict."""
        return cls(
            id=data["id"],
            name=data.get("name", ""),
            url=data.get("url"),
            channel_group=data.get("channel_group"),
            channel_group_id=data.get("channel_group_id"),
            tvg_id=data.get("tvg_id"),
            tvg_name=data.get("tvg_name"),
            tvg_logo=data.get("tvg_logo"),
            m3u_account_id=data.get("m3u_account"),
            m3u_account_name=data.get("m3u_account_name"),
        )


@dataclass(frozen=True)
class DispatcharrEPGSource:
    """An EPG source in Dispatcharr."""

    id: int
    name: str
    source_type: str
    url: str | None = None
    status: str = "idle"
    last_message: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_api(cls, data: dict) -> "DispatcharrEPGSource":
        """Create from API response dict."""
        return cls(
            id=data["id"],
            name=data.get("name", ""),
            source_type=data.get("source_type", ""),
            url=data.get("url"),
            status=data.get("status", "idle"),
            last_message=data.get("last_message"),
            updated_at=data.get("updated_at"),
        )


@dataclass(frozen=True)
class DispatcharrEPGData:
    """EPG data entry (channel within an EPG source)."""

    id: int
    tvg_id: str
    name: str | None = None
    icon_url: str | None = None
    epg_source_id: int | None = None

    @classmethod
    def from_api(cls, data: dict) -> "DispatcharrEPGData":
        """Create from API response dict."""
        return cls(
            id=data["id"],
            tvg_id=data.get("tvg_id", ""),
            name=data.get("name"),
            icon_url=data.get("icon_url"),
            epg_source_id=data.get("epg_source"),
        )


@dataclass(frozen=True)
class DispatcharrLogo:
    """An uploaded logo in Dispatcharr."""

    id: int
    name: str
    url: str

    @classmethod
    def from_api(cls, data: dict) -> "DispatcharrLogo":
        """Create from API response dict."""
        return cls(
            id=data["id"],
            name=data.get("name", ""),
            url=data.get("url", ""),
        )


@dataclass(frozen=True)
class DispatcharrM3UAccount:
    """An M3U account in Dispatcharr."""

    id: int
    name: str
    status: str = "idle"
    url: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_api(cls, data: dict) -> "DispatcharrM3UAccount":
        """Create from API response dict."""
        return cls(
            id=data["id"],
            name=data.get("name", ""),
            status=data.get("status", "idle"),
            url=data.get("url"),
            updated_at=data.get("updated_at"),
        )


@dataclass(frozen=True)
class DispatcharrChannelGroup:
    """A channel group in Dispatcharr (from M3U)."""

    id: int
    name: str
    m3u_accounts: tuple[int, ...] = field(default_factory=tuple)

    @classmethod
    def from_api(cls, data: dict) -> "DispatcharrChannelGroup":
        """Create from API response dict."""
        accounts = data.get("m3u_accounts", [])
        if isinstance(accounts, list):
            accounts = tuple(accounts)
        return cls(
            id=data["id"],
            name=data.get("name", ""),
            m3u_accounts=accounts,
        )


@dataclass
class OperationResult:
    """Result of a Dispatcharr API operation.

    This is a mutable dataclass since results are built incrementally.
    """

    success: bool
    message: str | None = None
    error: str | None = None
    data: dict | None = None
    channel: dict | None = None  # For channel operations
    logo: dict | None = None  # For logo operations
    duration: float | None = None  # For timed operations


@dataclass
class RefreshResult:
    """Result of an M3U or EPG refresh operation."""

    success: bool
    message: str | None = None
    duration: float | None = None
    source: dict | None = None  # Final state after refresh
    skipped: bool = False  # True if refresh was skipped (recently refreshed)
    last_status: str | None = None  # Last status before timeout
    last_message: str | None = None  # Last message before timeout


@dataclass
class BatchRefreshResult:
    """Result of refreshing multiple M3U accounts."""

    success: bool  # True if all succeeded
    results: dict[int, RefreshResult] = field(default_factory=dict)  # account_id -> result
    duration: float = 0.0
    failed_count: int = 0
    succeeded_count: int = 0
    skipped_count: int = 0
