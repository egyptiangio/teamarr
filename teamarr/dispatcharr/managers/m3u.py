"""M3U account and stream management for Dispatcharr.

Handles M3U account listing, stream discovery, and refresh operations.
"""

import logging
import threading
import time
import urllib.parse
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import monotonic

from teamarr.dispatcharr.client import DispatcharrClient
from teamarr.dispatcharr.types import (
    BatchRefreshResult,
    DispatcharrChannelGroup,
    DispatcharrM3UAccount,
    DispatcharrStream,
    OperationResult,
    RefreshResult,
)

logger = logging.getLogger(__name__)

# How long a memoized M3U account list stands (#736). Accounts are edited by
# hand, so this only has to be short enough that a new one shows up promptly —
# mutations invalidate it outright.
_ACCOUNTS_TTL_SECONDS = 60.0

# Same idea for the channel-group catalog, which the Sources form loads on open
# and which runs to several thousand entries on a real install.
_GROUPS_TTL_SECONDS = 60.0

# Concurrency for paginated stream fetches. Bounded well below the page count on
# purpose: Dispatcharr is a single Django app, frequently on the same host, so
# the goal is to remove the serial round-trip cost, not to saturate it.
_MAX_PAGE_WORKERS = 8
# ids per ``?ids=`` request — ~8 chars each keeps the URL well under 2 KB.
_IDS_CHUNK_SIZE = 200


def _fix_double_encoded_utf8(text: str) -> str:
    """Fix double-encoded UTF-8 strings.

    Some M3U sources have UTF-8 text that was decoded as Latin-1 then re-encoded,
    resulting in characters like 'Ã±' instead of 'ñ'. Delegates the actual
    guarded latin-1/utf-8 round trip to
    ``teamarr.consumers.matching.normalizer.try_fix_double_encoded``.

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

    # Deferred import: teamarr.consumers.matching.normalizer sits behind an
    # import chain that eventually reaches back to teamarr.dispatcharr.factory
    # (which imports this module at load time), so this can't be hoisted to
    # module scope without creating a circular import.
    from teamarr.consumers.matching.normalizer import try_fix_double_encoded

    return try_fix_double_encoded(text)


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
        # When _groups_cache was last filled from Dispatcharr (#736). The list
        # itself is kept indefinitely for id->name lookups; this stamp only
        # decides whether list_groups may SERVE it instead of re-fetching.
        self._groups_fetched_at: float | None = None
        self._groups_lock = threading.Lock()
        # Short-lived memo for list_accounts (#736); see the method.
        self._accounts_cache: tuple[float, list[DispatcharrM3UAccount]] | None = None
        self._accounts_lock = threading.Lock()

    def list_accounts(self, include_custom: bool = False) -> list[DispatcharrM3UAccount]:
        """List all M3U accounts.

        Memoized for ``_ACCOUNTS_TTL_SECONDS`` (#736). The Sources list calls
        this on every page load purely to freshen account NAMES it already
        stores a fallback for, and profiling put 93% of that endpoint's time
        inside this one live request. Accounts change when a user adds or edits
        one, which is rare and invalidates the memo explicitly below.

        The memo holds the unfiltered list, so ``include_custom`` still decides
        per call rather than poisoning the cache for the other caller.

        Args:
            include_custom: If False (default), excludes the "custom" account

        Returns:
            List of DispatcharrM3UAccount objects
        """
        with self._accounts_lock:
            cached = self._accounts_cache
            if cached is not None and (monotonic() - cached[0]) < _ACCOUNTS_TTL_SECONDS:
                accounts = cached[1]
            else:
                response = self._client.get("/api/m3u/accounts/")
                if response is None or response.status_code != 200:
                    status = response.status_code if response else "No response"
                    logger.error("[M3U] Failed to list accounts: %s", status)
                    # Not cached: a transient failure must not blank the account
                    # names for the whole TTL.
                    return []
                accounts = [DispatcharrM3UAccount.from_api(a) for a in response.json()]
                self._accounts_cache = (monotonic(), accounts)

        if not include_custom:
            return [a for a in accounts if a.name.lower() != "custom"]
        return list(accounts)

    def invalidate_accounts_cache(self) -> None:
        """Drop the memoized account list (#736) after a mutation."""
        with self._accounts_lock:
            self._accounts_cache = None

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

    def list_groups(
        self,
        search: str | None = None,
        exclude_m3u: bool = False,
    ) -> list[DispatcharrChannelGroup]:
        """List channel groups, optionally filtered by name.

        Args:
            search: Filter by group name (case-insensitive substring match)
            exclude_m3u: If True, exclude groups that originate from M3U accounts

        Returns:
            List of DispatcharrChannelGroup objects
        """
        with self._groups_lock:
            fresh = (
                self._groups_cache is not None
                and self._groups_fetched_at is not None
                and (monotonic() - self._groups_fetched_at) < _GROUPS_TTL_SECONDS
            )
            if fresh:
                groups = self._groups_cache or []
            else:
                response = self._client.get("/api/channels/groups/")
                if response is None or response.status_code != 200:
                    status = response.status_code if response else "No response"
                    logger.error("[M3U] Failed to list channel groups: %s", status)
                    return []

                groups = [DispatcharrChannelGroup.from_api(g) for g in response.json()]
                self._groups_cache = groups  # Cache for name lookups
                self._groups_fetched_at = monotonic()

        # Filter out M3U-originated groups if requested
        if exclude_m3u:
            groups = [g for g in groups if not g.m3u_accounts]

        if search:
            search_lower = search.lower()
            groups = [g for g in groups if search_lower in g.name.lower()]

        return groups

    def create_channel_group(self, name: str) -> OperationResult:
        """Create a new channel group in Dispatcharr.

        Args:
            name: Group name

        Returns:
            OperationResult with success status and created group data
        """
        if not name or not name.strip():
            return OperationResult(success=False, error="Group name is required")

        payload = {"name": name.strip()}
        response = self._client.post("/api/channels/groups/", payload)

        if response is None:
            return OperationResult(success=False, error="Request failed - no response")

        if response.status_code == 201:
            data = response.json()
            # Invalidate cache so new group appears
            self._groups_cache = None
            self._groups_fetched_at = None
            return OperationResult(success=True, data=data)

        if response.status_code == 400:
            return OperationResult(
                success=False,
                error=response.json().get("detail", "Bad request"),
            )

        return OperationResult(
            success=False,
            error=f"Failed to create group: {response.status_code}",
        )

    def get_account_group_counts(self, account_id: int) -> dict[int, int]:
        """Get per-group stream counts for a single M3U account.

        Uses the account detail endpoint, whose ``channel_groups`` array holds
        one relationship entry per group with Dispatcharr's tracked
        ``stream_count`` — one request instead of listing streams per group.

        Args:
            account_id: M3U account ID

        Returns:
            Mapping of channel group ID -> stream count for this account
        """
        response = self._client.get(f"/api/m3u/accounts/{account_id}/")
        if response is None or response.status_code != 200:
            status = response.status_code if response else "No response"
            logger.error("[M3U] Failed to fetch account %d detail: %s", account_id, status)
            return {}

        counts: dict[int, int] = {}
        for rel in response.json().get("channel_groups") or []:
            group_id = rel.get("channel_group")
            if group_id is not None:
                counts[group_id] = rel.get("stream_count") or 0
        return counts

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
            if group_name is None:
                # Group ID was provided but group no longer exists (deleted/renamed)
                # Return empty list instead of silently fetching ALL streams
                logger.warning(
                    "[M3U] Group ID %d no longer exists in Dispatcharr - "
                    "group may have been deleted or renamed. Returning empty stream list.",
                    group_id,
                )
                return []

        # Build query params — don't request more per page than the caller wants
        page_size = min(limit, 1000) if limit else 1000
        filters = [f"page_size={page_size}"]
        if group_name:
            filters.append(f"channel_group_name={urllib.parse.quote(group_name)}")
        if account_id is not None:
            filters.append(f"m3u_account={account_id}")
        query = "&".join(filters)

        def page_url(page: int) -> str:
            return f"/api/channels/streams/?page={page}&{query}"

        def fetch(url: str) -> dict | list | None:
            """One page. ``None`` means the request failed — never an empty page."""
            response = self._client.get(url)
            if response is None or response.status_code != 200:
                status = response.status_code if response else "No response"
                logger.error("[M3U] Failed to list streams: %s", status)
                return None
            return response.json()

        def next_path(data: dict) -> str | None:
            """Path of the next page, if any (Dispatcharr may return a full URL)."""
            next_url = data.get("next")
            if not next_url:
                return None
            if next_url.startswith("http"):
                from urllib.parse import urlparse

                parsed = urlparse(next_url)
                return f"{parsed.path}?{parsed.query}" if parsed.query else parsed.path
            return next_url

        def follow(url: str | None, raw: list[dict]) -> bool:
            """Walk `next` links from `url`, appending. False if a page failed."""
            while url and (limit is None or len(raw) < limit):
                data = fetch(url)
                if data is None:
                    return False
                if not isinstance(data, dict):
                    raw.extend(data)  # non-paginated response (legacy?)
                    return True
                raw.extend(data.get("results", []))
                url = next_path(data)
            return True

        raw_streams: list[dict] = []

        first = fetch(page_url(1))
        if first is None:
            return []

        if not isinstance(first, dict):
            # Non-paginated response (legacy?)
            raw_streams.extend(first)
        else:
            raw_streams.extend(first.get("results", []))
            total = first.get("count")
            pages = -(-total // page_size) if isinstance(total, int) and page_size > 0 else None

            if limit is not None or pages is None:
                # A caller with a limit wants to stop early, and a response with
                # no usable count cannot be addressed by page number — both walk
                # `next` one hop at a time.
                if not follow(next_path(first), raw_streams):
                    return []
            elif pages > 1:
                # Pages 2..N are independently addressable, so fetch them at once
                # rather than paying a round trip each: 34 pages x 147ms was the
                # largest single item left in the groups phase (#610). Bounded —
                # Dispatcharr is one Django app, often on the same host.
                by_page: dict[int, list[dict]] = {}
                last = first
                workers = min(_MAX_PAGE_WORKERS, pages - 1)
                with ThreadPoolExecutor(
                    max_workers=workers, thread_name_prefix="m3u-page"
                ) as executor:
                    futures = {executor.submit(fetch, page_url(n)): n for n in range(2, pages + 1)}
                    for future in as_completed(futures):
                        page = futures[future]
                        try:
                            data = future.result()
                        except Exception as e:  # noqa: BLE001 - treated as a failed page
                            logger.error("[M3U] Page %d failed: %s", page, e)
                            data = None
                        if data is None:
                            # A partial list is worse than none: the caller cannot
                            # tell it apart from a genuinely smaller group, would
                            # silently lose matches, and could delete channels for
                            # the streams that went missing. Callers already handle
                            # the empty case (see the group processor's guard).
                            logger.error(
                                "[M3U] Aborting stream list: page %d of %d failed",
                                page,
                                pages,
                            )
                            executor.shutdown(wait=False, cancel_futures=True)
                            return []
                        by_page[page] = data.get("results", []) if isinstance(data, dict) else data
                        if isinstance(data, dict) and page == pages:
                            last = data

                for page in range(2, pages + 1):
                    raw_streams.extend(by_page.get(page, []))

                # The set can grow between page 1 and the last page; whatever
                # `next` still points at is picked up here.
                if not follow(next_path(last), raw_streams):
                    return []
            elif not follow(next_path(first), raw_streams):
                return []

        # Fix double-encoded UTF-8 in stream names
        streams = []
        for raw in raw_streams:
            if "name" in raw:
                raw["name"] = _fix_double_encoded_utf8(raw["name"])
            streams.append(DispatcharrStream.from_api(raw))

        if limit:
            streams = streams[:limit]

        # Log stale stream count for debugging
        stale_count = sum(1 for s in streams if s.is_stale)
        if stale_count > 0:
            logger.info(
                "[M3U] Fetched %d streams (%d marked stale) from Dispatcharr",
                len(streams),
                stale_count,
            )
        elif streams:
            # Check if API even returns is_stale field by looking at raw data
            logger.debug(
                "[M3U] Fetched %d streams (0 stale - verify Dispatcharr version >= 0.6.0)",
                len(streams),
            )

        return streams

    def get_streams_by_ids(
        self,
        stream_ids: Iterable[int],
        chunk_size: int = _IDS_CHUNK_SIZE,
    ) -> list[DispatcharrStream]:
        """Fetch specific streams by id via ``/api/channels/streams/?ids=``.

        Dispatcharr answers an ``ids`` filter with an unpaginated list, so a
        few hundred streams cost a handful of requests instead of a walk over
        the whole catalog (#647: 119 pages / 118k streams on a real install to
        look up ~500). Ids are sent in chunks to keep URLs short. A failed
        chunk is logged and skipped — callers treat a missing detail as "use
        what the channel already tells us", so partial is strictly better
        than nothing here.
        """
        ids = sorted({int(i) for i in stream_ids})
        if not ids:
            return []
        streams: list[DispatcharrStream] = []
        for start in range(0, len(ids), chunk_size):
            chunk = ids[start : start + chunk_size]
            url = "/api/channels/streams/?ids=" + ",".join(map(str, chunk))
            response = self._client.get(url)
            if response is None or response.status_code != 200:
                logger.warning(
                    "[M3U] Failed to fetch %d stream(s) by id: %s",
                    len(chunk),
                    response.status_code if response else "No response",
                )
                continue
            data = response.json()
            raw_list = data.get("results", []) if isinstance(data, dict) else data
            for raw in raw_list:
                if "name" in raw:
                    raw["name"] = _fix_double_encoded_utf8(raw["name"])
                streams.append(DispatcharrStream.from_api(raw))
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
            # A refresh moves the account's status and updated_at, both of which
            # the Sources UI shows — don't serve them stale off the memo (#736).
            self.invalidate_accounts_cache()
            return RefreshResult(success=True, message="M3U refresh initiated")
        elif response.status_code == 404:
            return RefreshResult(success=False, message="M3U account not found")
        else:
            return RefreshResult(success=False, message=f"HTTP {response.status_code}")

    def wait_for_refresh(
        self,
        account_id: int,
        timeout: int = 300,
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
        timeout: int = 300,
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
