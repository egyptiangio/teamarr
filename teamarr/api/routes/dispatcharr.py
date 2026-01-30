"""Dispatcharr integration API endpoints.

Provides endpoints for:
- Testing Dispatcharr connection
- Listing M3U accounts and groups
- Fetching streams for preview
"""

import logging

from fastapi import APIRouter, HTTPException

from teamarr.database import get_db
from teamarr.dispatcharr.factory import (
    get_dispatcharr_connection,
    test_dispatcharr_connection,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dispatcharr")


@router.get("/test")
def test_connection() -> dict:
    """Test connection to Dispatcharr.

    Returns:
        Connection test result with status and details
    """
    result = test_dispatcharr_connection(db_factory=get_db)
    return result.to_dict()


@router.get("/m3u-accounts")
def list_m3u_accounts() -> list[dict]:
    """List all M3U accounts from Dispatcharr.

    Returns:
        List of M3U accounts with id, name, and metadata
    """
    conn = get_dispatcharr_connection(db_factory=get_db)
    if not conn:
        raise HTTPException(status_code=503, detail="Dispatcharr not configured or unavailable")

    accounts = conn.m3u.list_accounts(include_custom=False)
    return [
        {
            "id": a.id,
            "name": a.name,
            "url": a.url,
            "status": a.status,
            "updated_at": a.updated_at,
        }
        for a in accounts
    ]


@router.get("/m3u-accounts/{account_id}/groups")
def list_m3u_groups(account_id: int) -> list[dict]:
    """List M3U groups (channel groups) for a specific account.

    Args:
        account_id: M3U account ID

    Returns:
        List of channel groups with stream counts
    """
    conn = get_dispatcharr_connection(db_factory=get_db)
    if not conn:
        raise HTTPException(status_code=503, detail="Dispatcharr not configured or unavailable")

    # Get all groups first
    all_groups = conn.m3u.list_groups()

    # Get streams filtered by account to find groups with streams from this account
    # Note: This is an approximation - Dispatcharr may not directly support
    # filtering groups by account, so we list streams per group
    result = []
    for group in all_groups:
        # Count streams for this group from this account
        streams = conn.m3u.list_streams(group_name=group.name, account_id=account_id, limit=1000)
        if streams:  # Only include groups that have streams from this account
            result.append(
                {
                    "id": group.id,
                    "name": group.name,
                    "stream_count": len(streams),
                }
            )

    return result


def _natural_sort_key(name: str) -> list:
    """Generate sort key for natural/human sorting.

    Handles embedded numbers correctly:
    - "ESPN+ 2" comes before "ESPN+ 10"
    - "Sportsnet+ 01" comes before "Sportsnet+ 02"
    """
    import re

    parts = []
    for part in re.split(r"(\d+)", name.lower()):
        if part.isdigit():
            parts.append(int(part))  # Compare numbers as integers
        else:
            parts.append(part)  # Compare text as strings
    return parts


@router.get("/m3u-accounts/{account_id}/groups/{group_id}/streams")
def list_group_streams(account_id: int, group_id: int) -> list[dict]:
    """List streams in a specific M3U group.

    Args:
        account_id: M3U account ID
        group_id: Channel group ID

    Returns:
        List of streams with id and name, sorted naturally
    """
    conn = get_dispatcharr_connection(db_factory=get_db)
    if not conn:
        raise HTTPException(status_code=503, detail="Dispatcharr not configured or unavailable")

    streams = conn.m3u.list_streams(group_id=group_id, account_id=account_id, limit=500)

    # Sort using natural ordering (ESPN+ 2 before ESPN+ 10)
    return sorted(
        [
            {
                "id": s.id,
                "name": s.name,
            }
            for s in streams
        ],
        key=lambda x: _natural_sort_key(x["name"]),
    )


@router.get("/channel-groups")
def list_channel_groups(exclude_m3u: bool = True) -> list[dict]:
    """List Dispatcharr channel groups (for channel assignment).

    Args:
        exclude_m3u: If True, exclude groups originating from M3U accounts.
                     Defaults to True since M3U groups shouldn't be used for channel assignment.

    Returns:
        List of channel groups
    """
    conn = get_dispatcharr_connection(db_factory=get_db)
    if not conn:
        raise HTTPException(status_code=503, detail="Dispatcharr not configured or unavailable")

    groups = conn.m3u.list_groups(exclude_m3u=exclude_m3u)
    return [
        {
            "id": g.id,
            "name": g.name,
        }
        for g in groups
    ]


@router.post("/channel-groups")
def create_channel_group(name: str) -> dict:
    """Create a new channel group in Dispatcharr.

    Args:
        name: Group name

    Returns:
        Created group data
    """
    conn = get_dispatcharr_connection(db_factory=get_db)
    if not conn:
        raise HTTPException(status_code=503, detail="Dispatcharr not configured or unavailable")

    result = conn.m3u.create_channel_group(name)
    if not result.success:
        logger.warning("[FAILED] Create channel group name=%s error=%s", name, result.error)
        raise HTTPException(status_code=400, detail=result.error)

    logger.info("[CREATED] Channel group in Dispatcharr name=%s", name)
    return result.data


@router.get("/channel-profiles")
def list_channel_profiles() -> list[dict]:
    """List all channel profiles from Dispatcharr.

    Returns:
        List of channel profiles
    """
    conn = get_dispatcharr_connection(db_factory=get_db)
    if not conn:
        raise HTTPException(status_code=503, detail="Dispatcharr not configured or unavailable")

    profiles = conn.channels.list_profiles()
    return [
        {
            "id": p.id,
            "name": p.name,
        }
        for p in profiles
    ]


@router.post("/channel-profiles")
def create_channel_profile(name: str) -> dict:
    """Create a new channel profile in Dispatcharr.

    Args:
        name: Profile name

    Returns:
        Created profile data
    """
    conn = get_dispatcharr_connection(db_factory=get_db)
    if not conn:
        raise HTTPException(status_code=503, detail="Dispatcharr not configured or unavailable")

    result = conn.channels.create_profile(name)
    if not result.success:
        logger.warning("[FAILED] Create channel profile name=%s error=%s", name, result.error)
        raise HTTPException(status_code=400, detail=result.error)

    logger.info("[CREATED] Channel profile in Dispatcharr name=%s", name)
    return result.data


@router.get("/stream-profiles")
def list_stream_profiles() -> list[dict]:
    """List all stream profiles from Dispatcharr.

    Stream profiles define how streams are processed (ffmpeg, VLC, proxy, etc).

    Returns:
        List of active stream profiles
    """
    conn = get_dispatcharr_connection(db_factory=get_db)
    if not conn:
        raise HTTPException(status_code=503, detail="Dispatcharr not configured or unavailable")

    profiles = conn.channels.list_stream_profiles()
    return [
        {
            "id": p.id,
            "name": p.name,
            "command": p.command,
        }
        for p in profiles
    ]


@router.get("/epg-sources")
def list_epg_sources() -> list[dict]:
    """List EPG sources from Dispatcharr.

    Returns:
        List of EPG sources
    """
    conn = get_dispatcharr_connection(db_factory=get_db)
    if not conn:
        raise HTTPException(status_code=503, detail="Dispatcharr not configured or unavailable")

    sources = conn.epg.list_sources()
    return [
        {
            "id": s.id,
            "name": s.name,
            "url": s.url,
            "status": s.status,
            "source_type": s.source_type,
        }
        for s in sources
    ]


@router.post("/m3u-accounts/{account_id}/refresh")
def refresh_m3u_account(account_id: int) -> dict:
    """Trigger M3U refresh for an account.

    Args:
        account_id: M3U account ID to refresh

    Returns:
        Refresh result
    """
    conn = get_dispatcharr_connection(db_factory=get_db)
    if not conn:
        raise HTTPException(status_code=503, detail="Dispatcharr not configured or unavailable")

    result = conn.m3u.refresh_account(account_id)
    if result.success:
        logger.info("[REFRESHED] M3U account_id=%d", account_id)
    else:
        logger.warning("[FAILED] Refresh M3U account_id=%d error=%s", account_id, result.message)
    return {
        "success": result.success,
        "message": result.message,
    }
