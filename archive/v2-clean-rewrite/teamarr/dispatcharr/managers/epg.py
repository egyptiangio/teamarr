"""EPG source management for Dispatcharr.

Handles EPG source operations including refresh and status polling.
"""

import logging
import time

from teamarr.dispatcharr.client import DispatcharrClient
from teamarr.dispatcharr.types import DispatcharrEPGSource, RefreshResult

logger = logging.getLogger(__name__)


class EPGManager:
    """EPG source management for Dispatcharr.

    Handles listing, refreshing, and status checking of EPG sources.

    Usage:
        manager = EPGManager(client)
        sources = manager.list_sources()
        result = manager.wait_for_refresh(epg_id=21, timeout=60)
    """

    def __init__(self, client: DispatcharrClient):
        """Initialize EPG manager.

        Args:
            client: Authenticated DispatcharrClient instance
        """
        self._client = client

    def list_sources(self, include_dummy: bool = True) -> list[DispatcharrEPGSource]:
        """List all EPG sources.

        Args:
            include_dummy: Whether to include dummy sources (default: True)

        Returns:
            List of DispatcharrEPGSource objects
        """
        response = self._client.get("/api/epg/sources/")
        if response is None or response.status_code != 200:
            return []

        sources = [DispatcharrEPGSource.from_api(s) for s in response.json()]

        if not include_dummy:
            sources = [s for s in sources if s.source_type != "dummy"]

        return sources

    def get_source(self, epg_id: int) -> DispatcharrEPGSource | None:
        """Get a specific EPG source by ID.

        Args:
            epg_id: EPG source ID

        Returns:
            DispatcharrEPGSource or None if not found
        """
        response = self._client.get(f"/api/epg/sources/{epg_id}/")
        if response and response.status_code == 200:
            return DispatcharrEPGSource.from_api(response.json())
        return None

    def find_by_name(
        self,
        name: str,
        exact: bool = False,
    ) -> DispatcharrEPGSource | None:
        """Find EPG source by name.

        Args:
            name: Name to search for
            exact: If True, require exact match; otherwise partial match

        Returns:
            First matching EPG source or None
        """
        sources = self.list_sources()

        for source in sources:
            if exact:
                if source.name == name:
                    return source
            else:
                if name.lower() in source.name.lower():
                    return source

        return None

    def refresh(self, epg_id: int) -> RefreshResult:
        """Trigger refresh for a single EPG source.

        Args:
            epg_id: EPG source ID to refresh

        Returns:
            RefreshResult with success status and message
        """
        response = self._client.post("/api/epg/import/", {"id": epg_id})

        if response is None:
            return RefreshResult(
                success=False,
                message="Request failed - could not connect",
            )

        if response.status_code == 202:
            return RefreshResult(success=True, message="EPG refresh initiated")
        elif response.status_code == 400:
            return RefreshResult(success=False, message="Cannot refresh dummy EPG source")
        elif response.status_code == 401:
            return RefreshResult(success=False, message="Authentication failed")
        elif response.status_code == 404:
            return RefreshResult(success=False, message="EPG source not found")
        else:
            try:
                msg = response.json().get("message", f"HTTP {response.status_code}")
            except Exception:
                msg = f"HTTP {response.status_code}"
            return RefreshResult(success=False, message=msg)

    def wait_for_refresh(
        self,
        epg_id: int,
        timeout: int = 60,
        poll_interval: int = 2,
    ) -> RefreshResult:
        """Trigger EPG refresh and wait for completion.

        Dispatcharr's EPG import is async (returns 202). This method triggers
        the refresh and polls until completion by monitoring status and updated_at.

        EPG status values: idle, fetching, parsing, error, success, disabled

        Args:
            epg_id: EPG source ID to refresh
            timeout: Maximum seconds to wait (default: 60)
            poll_interval: Seconds between status checks (default: 2)

        Returns:
            RefreshResult with success status, duration, and final source state
        """
        # Get current state before refresh
        before = self.get_source(epg_id)
        if not before:
            return RefreshResult(success=False, message=f"EPG source {epg_id} not found")

        before_updated = before.updated_at

        # Trigger refresh
        trigger_result = self.refresh(epg_id)
        if not trigger_result.success:
            return trigger_result

        # Poll until status changes to success/error or updated_at changes
        start_time = time.time()
        last_logged_status: str | None = None
        last_status: str | None = None
        last_message: str | None = None

        while time.time() - start_time < timeout:
            time.sleep(poll_interval)

            current = self.get_source(epg_id)
            if not current:
                logger.debug(f"EPG refresh poll: could not get source {epg_id}")
                continue

            current_status = current.status
            current_updated = current.updated_at
            current_message = current.last_message
            last_status = current_status
            last_message = current_message

            # Log status changes
            if current_status != last_logged_status:
                elapsed = time.time() - start_time
                logger.debug(
                    f"EPG refresh poll: status={current_status}, "
                    f"message='{current_message}', elapsed={elapsed:.1f}s"
                )
                last_logged_status = current_status

            # Check if refresh completed (status is success and updated_at changed)
            if current_status == "success" and current_updated != before_updated:
                duration = time.time() - start_time
                return RefreshResult(
                    success=True,
                    message=current.last_message or "EPG refresh completed",
                    duration=duration,
                    source={"id": current.id, "status": current.status},
                )
            elif current_status == "error":
                duration = time.time() - start_time
                return RefreshResult(
                    success=False,
                    message=current.last_message or "EPG refresh failed",
                    duration=duration,
                    source={"id": current.id, "status": current.status},
                )

        # Timeout - but check if status is actually success
        # When no channels are mapped, Dispatcharr completes instantly
        if last_status == "success":
            return RefreshResult(
                success=True,
                message=last_message or "EPG refresh completed (no channels mapped yet)",
                duration=float(timeout),
                last_status=last_status,
                last_message=last_message,
            )

        return RefreshResult(
            success=False,
            message=f"EPG refresh timed out after {timeout} seconds",
            duration=float(timeout),
            last_status=last_status,
            last_message=last_message,
        )

    def refresh_by_name(self, name: str) -> RefreshResult:
        """Refresh EPG source by name (partial match).

        Args:
            name: Name to search for

        Returns:
            RefreshResult with success status
        """
        source = self.find_by_name(name)
        if not source:
            return RefreshResult(
                success=False,
                message=f"No EPG source found matching '{name}'",
            )
        return self.refresh(source.id)

    def test_connection(self) -> dict:
        """Test connection to Dispatcharr EPG API.

        Returns:
            Dict with success, message, and optionally sources list
        """
        try:
            sources = self.list_sources()
            return {
                "success": True,
                "message": f"Connected successfully. Found {len(sources)} EPG source(s).",
                "sources": [{"id": s.id, "name": s.name} for s in sources],
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Error: {e!s}",
            }
