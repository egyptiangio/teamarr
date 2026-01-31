"""Channel management for Dispatcharr.

Handles channel CRUD operations with caching for efficient lookups
during EPG generation cycles.
"""

import logging
import threading

from teamarr.dispatcharr.client import DispatcharrClient
from teamarr.dispatcharr.types import (
    DispatcharrChannel,
    DispatcharrChannelProfile,
    DispatcharrStreamProfile,
    OperationResult,
)

logger = logging.getLogger(__name__)


class ChannelCache:
    """In-memory cache for channels with indexed lookups.

    Provides O(1) lookups by ID, tvg_id, and channel_number.
    Thread-safe via external lock in ChannelManager.
    """

    def __init__(self):
        self._channels: list[DispatcharrChannel] | None = None
        self._by_id: dict[int, DispatcharrChannel] = {}
        self._by_tvg_id: dict[str, DispatcharrChannel] = {}
        self._by_number: dict[str, DispatcharrChannel] = {}

    def clear(self) -> None:
        """Clear all caches."""
        self._channels = None
        self._by_id.clear()
        self._by_tvg_id.clear()
        self._by_number.clear()

    def is_populated(self) -> bool:
        """Check if cache has been populated."""
        return self._channels is not None

    def populate(self, channels: list[DispatcharrChannel]) -> None:
        """Populate cache from channel list."""
        self._channels = channels
        self._by_id.clear()
        self._by_tvg_id.clear()
        self._by_number.clear()

        for ch in channels:
            self._by_id[ch.id] = ch
            if ch.tvg_id:
                self._by_tvg_id[ch.tvg_id] = ch
            if ch.channel_number:
                self._by_number[ch.channel_number] = ch

    def get_all(self) -> list[DispatcharrChannel]:
        """Get all cached channels."""
        return self._channels or []

    def get_by_id(self, channel_id: int) -> DispatcharrChannel | None:
        """O(1) lookup by ID."""
        return self._by_id.get(channel_id)

    def get_by_tvg_id(self, tvg_id: str) -> DispatcharrChannel | None:
        """O(1) lookup by tvg_id."""
        return self._by_tvg_id.get(tvg_id)

    def get_by_number(self, channel_number: str | int) -> DispatcharrChannel | None:
        """O(1) lookup by channel number."""
        return self._by_number.get(str(channel_number))

    def invalidate(self, channel_id: int) -> None:
        """Remove channel from cache after deletion."""
        channel = self._by_id.pop(channel_id, None)
        if channel:
            if channel.tvg_id and channel.tvg_id in self._by_tvg_id:
                del self._by_tvg_id[channel.tvg_id]
            if channel.channel_number and channel.channel_number in self._by_number:
                del self._by_number[channel.channel_number]
            if self._channels:
                self._channels = [c for c in self._channels if c.id != channel_id]

    def update(self, channel: DispatcharrChannel) -> None:
        """Update channel in cache (removes old version first)."""
        # Remove old version
        self.invalidate(channel.id)

        # Add new version
        self._by_id[channel.id] = channel
        if channel.tvg_id:
            self._by_tvg_id[channel.tvg_id] = channel
        if channel.channel_number:
            self._by_number[channel.channel_number] = channel
        if self._channels is not None:
            self._channels.append(channel)


class ChannelManager:
    """High-level channel operations for Dispatcharr.

    Handles channel CRUD with caching for efficient lookups.
    Thread-safe for concurrent access during EPG generation.

    Usage:
        manager = ChannelManager(client)
        manager.clear_cache()  # At start of EPG generation

        # Create channel
        result = manager.create_channel(
            name="Giants @ Cowboys",
            channel_number=5001,
            stream_ids=[456],
            tvg_id="teamarr-event-12345"
        )

        # Find by tvg_id (O(1) from cache)
        channel = manager.find_by_tvg_id("teamarr-event-12345")
    """

    # Class-level caches shared across instances (keyed by base URL)
    _caches: dict[str, ChannelCache] = {}
    _locks: dict[str, threading.Lock] = {}

    def __init__(self, client: DispatcharrClient):
        """Initialize channel manager.

        Args:
            client: Authenticated DispatcharrClient instance
        """
        self._client = client
        self._url = client._base_url
        self._lock = self._locks.setdefault(self._url, threading.Lock())

        # Initialize cache for this URL if not exists
        if self._url not in self._caches:
            self._caches[self._url] = ChannelCache()

    @property
    def _cache(self) -> ChannelCache:
        """Get cache for this client's URL."""
        return self._caches[self._url]

    def clear_cache(self) -> None:
        """Clear channel cache. Call at start of each EPG generation cycle."""
        with self._lock:
            self._cache.clear()
            logger.debug("[CHANNEL_CACHE] Cleared")

    def _ensure_cache(self) -> list[DispatcharrChannel]:
        """Ensure cache is populated. Returns cached channels list."""
        if not self._cache.is_populated():
            raw_channels = self._client.paginated_get(
                "/api/channels/channels/?page_size=1000",
                error_context="channels",
            )
            channels = [DispatcharrChannel.from_api(c) for c in raw_channels]
            self._cache.populate(channels)
            logger.debug("[CHANNEL_CACHE] Populated %d channels", len(channels))
        return self._cache.get_all()

    def get_channels(self, use_cache: bool = True) -> list[DispatcharrChannel]:
        """Get all channels from Dispatcharr.

        Args:
            use_cache: Whether to use/populate the cache (default: True)

        Returns:
            List of DispatcharrChannel objects
        """
        with self._lock:
            if use_cache:
                return self._ensure_cache()

            raw_channels = self._client.paginated_get(
                "/api/channels/channels/?page_size=1000",
                error_context="channels",
            )
            return [DispatcharrChannel.from_api(c) for c in raw_channels]

    def get_channel(
        self,
        channel_id: int,
        use_cache: bool = True,
    ) -> DispatcharrChannel | None:
        """Get a single channel by ID.

        Args:
            channel_id: Dispatcharr channel ID
            use_cache: Whether to use cache (default: True)

        Returns:
            DispatcharrChannel or None if not found
        """
        with self._lock:
            if use_cache:
                self._ensure_cache()
                cached = self._cache.get_by_id(channel_id)
                if cached:
                    return cached

            # Cache miss or cache disabled - fetch from API
            response = self._client.get(f"/api/channels/channels/{channel_id}/")
            if response and response.status_code == 200:
                channel = DispatcharrChannel.from_api(response.json())
                if use_cache:
                    self._cache.update(channel)
                return channel
            return None

    def create_channel(
        self,
        name: str,
        channel_number: int,
        stream_ids: list[int] | None = None,
        tvg_id: str | None = None,
        channel_group_id: int | None = None,
        logo_id: int | None = None,
        channel_profile_ids: list[int] | None = None,
        stream_profile_id: int | None = None,
    ) -> OperationResult:
        """Create a new channel in Dispatcharr.

        Dispatcharr profile semantics (as of commit 6b873be):
          [] = NO profiles
          [0] = ALL profiles (sentinel value)
          [1, 2, ...] = specific profile IDs

        Args:
            name: Channel name (e.g., "Giants @ Cowboys")
            channel_number: Channel number
            stream_ids: List of stream IDs to attach (order = priority)
            tvg_id: TVG ID for XMLTV EPG matching
            channel_group_id: Optional group to assign channel to
            logo_id: Optional logo ID
            channel_profile_ids: List of profile IDs. Use [0] for all profiles,
                [] for no profiles, or specific IDs like [1, 2].
            stream_profile_id: Optional stream profile ID for transcoding/proxy

        Returns:
            OperationResult with success status and created channel data
        """
        payload: dict = {
            "name": name,
            "channel_number": str(channel_number),
            "streams": stream_ids or [],
        }

        if tvg_id:
            payload["tvg_id"] = tvg_id
        if channel_group_id:
            payload["channel_group_id"] = channel_group_id
        if logo_id:
            payload["logo_id"] = logo_id
        if channel_profile_ids is not None:
            payload["channel_profile_ids"] = channel_profile_ids
        if stream_profile_id is not None:
            payload["stream_profile_id"] = stream_profile_id

        logger.debug("[CHANNEL] Creating: %s", payload)
        response = self._client.post("/api/channels/channels/", payload)

        if response is None:
            return OperationResult(
                success=False,
                error=self._client.parse_api_error(response),
            )

        if response.status_code in (200, 201):
            channel_data = response.json()
            channel = DispatcharrChannel.from_api(channel_data)
            with self._lock:
                self._cache.update(channel)
            return OperationResult(
                success=True,
                channel=channel_data,
                data=channel_data,
            )

        return OperationResult(
            success=False,
            error=self._client.parse_api_error(response),
        )

    def update_channel(
        self,
        channel_id: int,
        data: dict,
    ) -> OperationResult:
        """Update an existing channel.

        Args:
            channel_id: Dispatcharr channel ID
            data: Fields to update (name, channel_number, tvg_id, streams, etc.)

        Returns:
            OperationResult with success status and updated channel data
        """
        # Convert channel_number to string if present
        if "channel_number" in data:
            data["channel_number"] = str(data["channel_number"])

        response = self._client.patch(f"/api/channels/channels/{channel_id}/", data)

        if response is None:
            return OperationResult(
                success=False,
                error=self._client.parse_api_error(response),
            )

        if response.status_code == 200:
            channel_data = response.json()
            channel = DispatcharrChannel.from_api(channel_data)
            with self._lock:
                self._cache.update(channel)
            return OperationResult(
                success=True,
                channel=channel_data,
                data=channel_data,
            )

        return OperationResult(
            success=False,
            error=self._client.parse_api_error(response),
        )

    def delete_channel(self, channel_id: int) -> OperationResult:
        """Delete a channel from Dispatcharr.

        Args:
            channel_id: Dispatcharr channel ID

        Returns:
            OperationResult with success status
        """
        logger.debug("[CHANNEL] Deleting %d", channel_id)
        response = self._client.delete(f"/api/channels/channels/{channel_id}/")

        if response is None:
            logger.warning("[CHANNEL] Delete %d: No response", channel_id)
            return OperationResult(
                success=False,
                error=self._client.parse_api_error(response),
            )

        if response.status_code in (200, 204):
            logger.debug("[CHANNEL] Delete %d: Success", channel_id)
            with self._lock:
                self._cache.invalidate(channel_id)
            return OperationResult(success=True)

        if response.status_code == 404:
            logger.debug("[CHANNEL] Delete %d: Not found", channel_id)
            with self._lock:
                self._cache.invalidate(channel_id)
            return OperationResult(success=False, error="Channel not found")

        logger.warning("[CHANNEL] Delete %d: Failed (status %d)", channel_id, response.status_code)
        return OperationResult(
            success=False,
            error=self._client.parse_api_error(response),
        )

    def assign_streams(
        self,
        channel_id: int,
        stream_ids: list[int],
    ) -> OperationResult:
        """Assign streams to a channel (replaces existing streams).

        Args:
            channel_id: Dispatcharr channel ID
            stream_ids: List of stream IDs (order = priority)

        Returns:
            OperationResult with success status
        """
        return self.update_channel(channel_id, {"streams": stream_ids})

    def find_by_tvg_id(self, tvg_id: str) -> DispatcharrChannel | None:
        """Find channel by TVG ID.

        Uses O(1) lookup via cache index.

        Args:
            tvg_id: TVG ID to search for

        Returns:
            DispatcharrChannel or None if not found
        """
        with self._lock:
            self._ensure_cache()
            return self._cache.get_by_tvg_id(tvg_id)

    def find_by_number(self, channel_number: int | str) -> DispatcharrChannel | None:
        """Find channel by channel number.

        Uses O(1) lookup via cache index.

        Args:
            channel_number: Channel number to search for

        Returns:
            DispatcharrChannel or None if not found
        """
        with self._lock:
            self._ensure_cache()
            return self._cache.get_by_number(str(channel_number))

    def set_channel_epg(
        self,
        channel_id: int,
        epg_data_id: int,
    ) -> OperationResult:
        """Set EPG data source for a channel.

        Links the channel to a specific EPG data entry in Dispatcharr.

        Args:
            channel_id: Dispatcharr channel ID
            epg_data_id: EPG data ID to link

        Returns:
            OperationResult with success status
        """
        response = self._client.post(
            f"/api/channels/channels/{channel_id}/set-epg/",
            {"epg_data_id": epg_data_id},
        )

        if response is None:
            return OperationResult(
                success=False,
                error=self._client.parse_api_error(response),
            )

        if response.status_code == 200:
            return OperationResult(success=True)

        return OperationResult(
            success=False,
            error=self._client.parse_api_error(response),
        )

    # ========================================================================
    # Channel Profiles
    # ========================================================================

    def list_profiles(self) -> list[DispatcharrChannelProfile]:
        """List all channel profiles from Dispatcharr.

        Returns:
            List of DispatcharrChannelProfile objects
        """
        response = self._client.get("/api/channels/profiles/")

        if response is None or response.status_code != 200:
            status = response.status_code if response else "No response"
            logger.error("[CHANNEL] Failed to list profiles: %s", status)
            return []

        return [DispatcharrChannelProfile.from_api(p) for p in response.json()]

    def create_profile(self, name: str) -> OperationResult:
        """Create a new channel profile in Dispatcharr.

        Args:
            name: Profile name

        Returns:
            OperationResult with success status and created profile data
        """
        if not name or not name.strip():
            return OperationResult(success=False, error="Profile name is required")

        payload = {"name": name.strip()}
        response = self._client.post("/api/channels/profiles/", payload)

        if response is None:
            return OperationResult(success=False, error="Request failed - no response")

        if response.status_code == 201:
            return OperationResult(success=True, data=response.json())

        if response.status_code == 400:
            return OperationResult(
                success=False,
                error=response.json().get("detail", "Bad request"),
            )

        return OperationResult(
            success=False,
            error=f"Failed to create profile: {response.status_code}",
        )

    def add_to_profile(
        self,
        profile_id: int,
        channel_id: int,
    ) -> OperationResult:
        """Add channel to a channel profile.

        Uses the per-channel endpoint to enable the channel in the profile.

        Args:
            profile_id: Channel profile ID
            channel_id: Channel ID to add

        Returns:
            OperationResult with success status
        """
        response = self._client.patch(
            f"/api/channels/profiles/{profile_id}/channels/{channel_id}/",
            {"enabled": True},
        )

        if response is None:
            return OperationResult(
                success=False,
                error=self._client.parse_api_error(response),
            )

        if response.status_code == 200:
            return OperationResult(success=True)

        return OperationResult(
            success=False,
            error=self._client.parse_api_error(response),
        )

    def remove_from_profile(
        self,
        profile_id: int,
        channel_id: int,
    ) -> OperationResult:
        """Remove channel from a channel profile.

        Uses the per-channel endpoint to disable the channel in the profile.

        Args:
            profile_id: Channel profile ID
            channel_id: Channel ID to remove

        Returns:
            OperationResult with success status
        """
        response = self._client.patch(
            f"/api/channels/profiles/{profile_id}/channels/{channel_id}/",
            {"enabled": False},
        )

        if response is None:
            return OperationResult(
                success=False,
                error=self._client.parse_api_error(response),
            )

        if response.status_code == 200:
            return OperationResult(success=True)

        return OperationResult(
            success=False,
            error=self._client.parse_api_error(response),
        )

    def bulk_update_profile_channels(
        self,
        profile_id: int,
        add_channel_ids: list[int] | None = None,
        remove_channel_ids: list[int] | None = None,
    ) -> OperationResult:
        """Bulk update channel membership in a profile.

        More efficient than individual add_to_profile/remove_from_profile calls
        when updating multiple channels at once.

        Args:
            profile_id: Channel profile ID
            add_channel_ids: Channel IDs to enable in the profile
            remove_channel_ids: Channel IDs to disable in the profile

        Returns:
            OperationResult with success status
        """
        channels = []
        if add_channel_ids:
            channels.extend({"channel_id": cid, "enabled": True} for cid in add_channel_ids)
        if remove_channel_ids:
            channels.extend({"channel_id": cid, "enabled": False} for cid in remove_channel_ids)

        if not channels:
            return OperationResult(success=True)  # Nothing to do

        response = self._client.patch(
            f"/api/channels/profiles/{profile_id}/channels/bulk-update/",
            {"channels": channels},
        )

        if response is None:
            return OperationResult(
                success=False,
                error=self._client.parse_api_error(response),
            )

        if response.status_code == 200:
            return OperationResult(success=True)

        return OperationResult(
            success=False,
            error=self._client.parse_api_error(response),
        )

    # ========================================================================
    # Stream Profiles
    # ========================================================================

    def list_stream_profiles(self) -> list[DispatcharrStreamProfile]:
        """List all stream profiles from Dispatcharr.

        Stream profiles define how streams are processed (ffmpeg, VLC, proxy, etc).
        Only returns active profiles.

        Returns:
            List of DispatcharrStreamProfile objects
        """
        response = self._client.get("/api/core/streamprofiles/")

        if response is None or response.status_code != 200:
            status = response.status_code if response else "No response"
            logger.error("[CHANNEL] Failed to list stream profiles: %s", status)
            return []

        profiles = [DispatcharrStreamProfile.from_api(p) for p in response.json()]
        # Only return active profiles
        return [p for p in profiles if p.is_active]

    def get_epg_data_list(self, epg_source_id: int | None = None) -> list[dict]:
        """Get all EPGData entries from Dispatcharr.

        EPGData represents individual channel entries within an EPG source.
        Each entry has a tvg_id that can be used to match channels.

        Args:
            epg_source_id: Optional filter by EPG source ID

        Returns:
            List of EPGData dicts with id, tvg_id, name, icon_url, epg_source
        """
        all_epg_data = self._client.paginated_get(
            "/api/epg/epgdata/?page_size=500",
            error_context="EPG data",
        )

        if epg_source_id is not None:
            all_epg_data = [e for e in all_epg_data if e.get("epg_source") == epg_source_id]

        return all_epg_data

    def build_epg_lookup(self, epg_source_id: int | None = None) -> dict[str, dict]:
        """Build tvg_id -> EPGData lookup for batch operations.

        Args:
            epg_source_id: Optional filter by EPG source ID

        Returns:
            Dict mapping tvg_id to EPGData dict
        """
        epg_data_list = self.get_epg_data_list(epg_source_id)
        return {e.get("tvg_id"): e for e in epg_data_list if e.get("tvg_id")}

    def find_epg_data_by_tvg_id(
        self,
        tvg_id: str,
        epg_source_id: int | None = None,
        epg_lookup: dict[str, dict] | None = None,
    ) -> dict | None:
        """Find EPGData by tvg_id.

        Args:
            tvg_id: The tvg_id to search for (e.g., "teamarr-event-401547679")
            epg_source_id: Optional EPG source ID to filter by
            epg_lookup: Optional pre-built lookup dict for batch operations

        Returns:
            EPGData dict if found, None otherwise
        """
        # Use pre-built lookup if provided (batch optimization)
        if epg_lookup is not None:
            return epg_lookup.get(tvg_id)

        # Otherwise fetch and search
        epg_data_list = self.get_epg_data_list(epg_source_id)
        for epg_data in epg_data_list:
            if epg_data.get("tvg_id") == tvg_id:
                return epg_data

        return None
