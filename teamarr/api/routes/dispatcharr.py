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


@router.get("/m3u-accounts/{account_id}/groups/{group_id}/streams")
def list_group_streams(account_id: int, group_id: int) -> list[dict]:
    """List streams in a specific M3U group.

    Args:
        account_id: M3U account ID
        group_id: Channel group ID

    Returns:
        List of streams with id and name
    """
    conn = get_dispatcharr_connection(db_factory=get_db)
    if not conn:
        raise HTTPException(status_code=503, detail="Dispatcharr not configured or unavailable")

    streams = conn.m3u.list_streams(group_id=group_id, account_id=account_id, limit=500)

    return [
        {
            "id": s.id,
            "name": s.name,
        }
        for s in streams
    ]


@router.get("/channel-groups")
def list_channel_groups() -> list[dict]:
    """List all Dispatcharr channel groups (for channel assignment).

    Returns:
        List of channel groups
    """
    conn = get_dispatcharr_connection(db_factory=get_db)
    if not conn:
        raise HTTPException(status_code=503, detail="Dispatcharr not configured or unavailable")

    groups = conn.m3u.list_groups()
    return [
        {
            "id": g.id,
            "name": g.name,
        }
        for g in groups
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
    return {
        "success": result.success,
        "message": result.message,
    }
