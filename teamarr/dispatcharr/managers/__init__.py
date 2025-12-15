"""Dispatcharr API managers.

High-level managers for Dispatcharr operations:
- ChannelManager: Channel CRUD with caching
- EPGManager: EPG source operations
- M3UManager: M3U accounts and streams
- LogoManager: Logo upload/delete
"""

from teamarr.dispatcharr.managers.channels import ChannelCache, ChannelManager
from teamarr.dispatcharr.managers.epg import EPGManager
from teamarr.dispatcharr.managers.logos import LogoManager
from teamarr.dispatcharr.managers.m3u import M3UManager

__all__ = [
    "ChannelCache",
    "ChannelManager",
    "EPGManager",
    "LogoManager",
    "M3UManager",
]
