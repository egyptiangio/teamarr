"""M3U account and stream management for Dispatcharr.

Handles M3U account listing, stream discovery, and refresh operations.
"""

import logging
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

from teamarr.dispatcharr.client import DispatcharrClient
from teamarr.dispatcharr.types import (
    BatchRefreshResult,
    DispatcharrChannelGroup,
    DispatcharrM3UAccount,
    DispatcharrStream,
    RefreshResult,
)

logger = logging.getLogger(__name__)


def _fix_double_encoded_utf8(text: str) -> str:
    """Fix double-encoded UTF-8 strings.

    Some M3U sources have UTF-8 text that was decoded as Latin-1 then re-encoded,
    resulting in characters like 'Ã±' instead of 'ñ'.

    Args:
        text: Potentially double-encoded string

    Returns:
        Properly decoded UTF-8 string, or original if not double-encoded
    """
    if not text or not isinstance(text, str):
        return text

    # Quick check: if no high bytes that look like double-encoding, skip
    if "Ã" not in text:
        return text

    try:
        # Try to fix: encode as Latin-1 (to get original bytes), decode as UTF-8
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return text


class M3UManager:
    """M3U account and stream management for Dispatcharr.

    Handles listing M3U accounts, discovering streams, and refreshing.

    Usage:
        manager = M3UManager(client)
        accounts = manager.list_accounts()
        groups = manager.list_groups(search="NFL")
        streams = manager.list_streams(group_name="NFL Game Pass")
    """

    def __init__(self, client: DispatcharrClient):
        """Initialize M3U manager.

        Args:
            client: Authenticated DispatcharrClient instance
        """
        self._client = client
        self._groups_cache: list[DispatcharrChannelGroup] | None = None

    def list_accounts(self, include_custom: bool = False) -> list[DispatcharrM3UAccount]:
        """List all M3U accounts.

        Args:
            include_custom: If False (default), excludes the "custom" account

        Returns:
            List of DispatcharrM3UAccount objects
        """
        response = self._client.get("/api/m3u/accounts/")
        if response is None or response.status_code != 200:
            status = response.status_code if response else "No response"
            logger.error(f"Failed to list M3U accounts: {status}")
            return []
        accounts = [DispatcharrM3UAccount.from_api(a) for a in response.json()]
        if not include_custom:
            accounts = [a for a in accounts if a.name.lower() != "custom"]
        return accounts

    def get_account(self, account_id: int) -> DispatcharrM3UAccount | None:
        """Get a specific M3U account by ID.

        Args:
            account_id: M3U account ID

        Returns:
            DispatcharrM3UAccount or None if not found
        """
        response = self._client.get(f"/api/m3u/accounts/{account_id}/")
        if response and response.status_code == 200:
            return DispatcharrM3UAccount.from_api(response.json())
        return None

    def list_groups(self, search: str | None = None) -> list[DispatcharrChannelGroup]:
        """List channel groups, optionally filtered by name.

        Args:
            search: Filter by group name (case-insensitive substring match)

        Returns:
            List of DispatcharrChannelGroup objects
        """
        response = self._client.get("/api/channels/groups/")
        if response is None or response.status_code != 200:
            status = response.status_code if response else "No response"
            logger.error(f"Failed to list channel groups: {status}")
            return []

        groups = [DispatcharrChannelGroup.from_api(g) for g in response.json()]
        self._groups_cache = groups  # Cache for name lookups

        if search:
            search_lower = search.lower()
            groups = [g for g in groups if search_lower in g.name.lower()]

        return groups

    def get_group_name(self, group_id: int) -> str | None:
        """Get exact group name by ID (needed for stream filtering).

        Args:
            group_id: Channel group ID

        Returns:
            Group name or None if not found
        """
        if self._groups_cache is None:
            self.list_groups()

        group = next((g for g in (self._groups_cache or []) if g.id == group_id), None)
        return group.name if group else None

    def list_streams(
        self,
        group_name: str | None = None,
        group_id: int | None = None,
        account_id: int | None = None,
        limit: int | None = None,
    ) -> list[DispatcharrStream]:
        """List streams from Dispatcharr.

        Filter by group using exact group_name (preferred) or group_id (requires lookup).
        The API's channel_group_name filter requires exact match including emoji.

        Args:
            group_name: Exact group name (e.g., "NFL Game Pass")
            group_id: Group ID (will lookup name if group_name not provided)
            account_id: Filter by M3U account ID
            limit: Maximum streams to return

        Returns:
            List of DispatcharrStream objects
        """
        # Resolve group_name from group_id if needed
        if group_name is None and group_id is not None:
            group_name = self.get_group_name(group_id)

        # Build query params
        params = ["page_size=1000"]
        if group_name:
            params.append(f"channel_group_name={urllib.parse.quote(group_name)}")
        if account_id is not None:
            params.append(f"m3u_account={account_id}")

        response = self._client.get(f"/api/channels/streams/?{'&'.join(params)}")
        if response is None or response.status_code != 200:
            status = response.status_code if response else "No response"
            logger.error(f"Failed to list streams: {status}")
            return []

        data = response.json()
        raw_streams = data.get("results", []) if isinstance(data, dict) else data

        # Fix double-encoded UTF-8 in stream names
        streams = []
        for raw in raw_streams:
            if "name" in raw:
                raw["name"] = _fix_double_encoded_utf8(raw["name"])
            streams.append(DispatcharrStream.from_api(raw))

        if limit:
            streams = streams[:limit]

        return streams

    def get_group_with_streams(
        self,
        group_id: int,
        stream_limit: int | None = None,
    ) -> dict | None:
        """Get group info with its streams for UI preview.

        Args:
            group_id: Dispatcharr group ID
            stream_limit: Max streams to return (None = no limit)

        Returns:
            Dict with group, streams, and total_streams count
        """
        if self._groups_cache is None:
            self.list_groups()

        group = next((g for g in (self._groups_cache or []) if g.id == group_id), None)
        if not group:
            return None

        streams = self.list_streams(group_name=group.name)

        return {
            "group": {"id": group.id, "name": group.name},
            "streams": streams[:stream_limit] if stream_limit else streams,
            "total_streams": len(streams),
        }

    def refresh_account(self, account_id: int) -> RefreshResult:
        """Trigger M3U refresh for an account (async, returns immediately).

        Args:
            account_id: M3U account ID

        Returns:
            RefreshResult with success status
        """
        response = self._client.post(f"/api/m3u/refresh/{account_id}/")

        if response is None:
            return RefreshResult(success=False, message="Request failed - no response")

        if response.status_code in (200, 202):
            return RefreshResult(success=True, message="M3U refresh initiated")
        elif response.status_code == 404:
            return RefreshResult(success=False, message="M3U account not found")
        else:
            return RefreshResult(success=False, message=f"HTTP {response.status_code}")

    def wait_for_refresh(
        self,
        account_id: int,
        timeout: int = 120,
        poll_interval: int = 2,
        skip_if_recent_minutes: int = 60,
    ) -> RefreshResult:
        """Trigger M3U refresh and wait for completion.

        Args:
            account_id: M3U account ID
            timeout: Maximum seconds to wait (default: 120)
            poll_interval: Seconds between status checks (default: 2)
            skip_if_recent_minutes: Skip refresh if updated within this many minutes

        Returns:
            RefreshResult with success status and duration
        """
        from datetime import datetime, timedelta

        # Check if recently refreshed
        account = self.get_account(account_id)
        if not account:
            return RefreshResult(success=False, message=f"M3U account {account_id} not found")

        if account.updated_at and skip_if_recent_minutes > 0:
            try:
                # Parse ISO timestamp
                updated = datetime.fromisoformat(account.updated_at.replace("Z", "+00:00"))
                threshold = datetime.now(updated.tzinfo) - timedelta(minutes=skip_if_recent_minutes)
                if updated > threshold:
                    now = datetime.now(updated.tzinfo)
                    mins_ago = (now - updated).seconds // 60
                    return RefreshResult(
                        success=True,
                        message=f"Skipped - refreshed {mins_ago} minutes ago",
                        skipped=True,
                    )
            except Exception:
                pass  # If parsing fails, proceed with refresh

        before_updated = account.updated_at

        # Trigger refresh
        trigger_result = self.refresh_account(account_id)
        if not trigger_result.success:
            return trigger_result

        # Poll until status changes
        start_time = time.time()

        while time.time() - start_time < timeout:
            time.sleep(poll_interval)

            current = self.get_account(account_id)
            if not current:
                continue

            # Check if refresh completed (updated_at changed)
            if current.updated_at != before_updated:
                duration = time.time() - start_time
                return RefreshResult(
                    success=True,
                    message="M3U refresh completed",
                    duration=duration,
                )

            # Check for error status
            if current.status == "error":
                duration = time.time() - start_time
                return RefreshResult(
                    success=False,
                    message="M3U refresh failed",
                    duration=duration,
                )

        return RefreshResult(
            success=False,
            message=f"M3U refresh timed out after {timeout} seconds",
            duration=float(timeout),
        )

    def refresh_multiple(
        self,
        account_ids: list[int],
        timeout: int = 120,
        skip_if_recent_minutes: int = 60,
        max_workers: int = 5,
    ) -> BatchRefreshResult:
        """Refresh multiple M3U accounts in parallel.

        Args:
            account_ids: List of M3U account IDs to refresh
            timeout: Maximum seconds to wait per account (default: 120)
            skip_if_recent_minutes: Skip if refreshed within this many minutes
            max_workers: Maximum parallel refreshes (default: 5)

        Returns:
            BatchRefreshResult with results per account
        """
        start_time = time.time()
        results: dict[int, RefreshResult] = {}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    self.wait_for_refresh,
                    account_id,
                    timeout,
                    2,  # poll_interval
                    skip_if_recent_minutes,
                ): account_id
                for account_id in account_ids
            }

            for future in as_completed(futures):
                account_id = futures[future]
                try:
                    results[account_id] = future.result()
                except Exception as e:
                    results[account_id] = RefreshResult(
                        success=False,
                        message=f"Error: {e!s}",
                    )

        total_duration = time.time() - start_time
        skipped = sum(1 for r in results.values() if r.skipped)
        succeeded = sum(1 for r in results.values() if r.success)
        failed = len(results) - succeeded

        return BatchRefreshResult(
            success=failed == 0,
            results=results,
            duration=total_duration,
            failed_count=failed,
            succeeded_count=succeeded,
            skipped_count=skipped,
        )

    def test_connection(self) -> dict:
        """Test connection to Dispatcharr M3U API.

        Returns:
            Dict with success, message, and accounts list
        """
        try:
            accounts = self.list_accounts()
            return {
                "success": True,
                "message": f"Connected. Found {len(accounts)} M3U account(s).",
                "accounts": [{"id": a.id, "name": a.name} for a in accounts],
            }
        except Exception as e:
            return {"success": False, "message": str(e)}
