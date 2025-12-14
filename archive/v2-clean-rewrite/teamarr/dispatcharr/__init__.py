"""Dispatcharr API client package.

Provides authenticated access to Dispatcharr API with:
- Automatic token management and refresh
- Exponential backoff retry for transient errors
- High-level managers for channels, EPG, M3U, and logos
- Caching for efficient batch operations

Usage:
    from teamarr.dispatcharr import DispatcharrClient, ChannelManager

    with DispatcharrClient("http://localhost:9191", "admin", "pass") as client:
        channels = ChannelManager(client)
        channels.clear_cache()

        result = channels.create_channel(
            name="Giants @ Cowboys",
            channel_number=5001,
            stream_ids=[456],
            tvg_id="teamarr-event-12345"
        )

        if result.success:
            print(f"Created channel: {result.channel}")
"""

from teamarr.dispatcharr.auth import TokenManager
from teamarr.dispatcharr.client import DispatcharrClient
from teamarr.dispatcharr.managers import (
    ChannelCache,
    ChannelManager,
    EPGManager,
    LogoManager,
    M3UManager,
)
from teamarr.dispatcharr.types import (
    BatchRefreshResult,
    DispatcharrChannel,
    DispatcharrChannelGroup,
    DispatcharrEPGData,
    DispatcharrEPGSource,
    DispatcharrLogo,
    DispatcharrM3UAccount,
    DispatcharrStream,
    OperationResult,
    RefreshResult,
)

__all__ = [
    # Client
    "DispatcharrClient",
    "TokenManager",
    # Managers
    "ChannelCache",
    "ChannelManager",
    "EPGManager",
    "LogoManager",
    "M3UManager",
    # Types
    "BatchRefreshResult",
    "DispatcharrChannel",
    "DispatcharrChannelGroup",
    "DispatcharrEPGData",
    "DispatcharrEPGSource",
    "DispatcharrLogo",
    "DispatcharrM3UAccount",
    "DispatcharrStream",
    "OperationResult",
    "RefreshResult",
]
