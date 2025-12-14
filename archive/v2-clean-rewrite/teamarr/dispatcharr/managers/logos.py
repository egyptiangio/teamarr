"""Logo management for Dispatcharr.

Handles logo upload, lookup, and deletion operations.
"""

import logging

from teamarr.dispatcharr.client import DispatcharrClient
from teamarr.dispatcharr.types import DispatcharrLogo, OperationResult

logger = logging.getLogger(__name__)


class LogoManager:
    """Logo management for Dispatcharr.

    Handles uploading, finding, and deleting logos.
    Includes caching for efficient URL-based lookups.

    Usage:
        manager = LogoManager(client)
        result = manager.upload(name="Team Logo", url="https://example.com/logo.png")
        if result.success:
            logo_id = result.logo["id"]
    """

    # Class-level cache shared across instances (keyed by base URL)
    _caches: dict[str, dict[str, DispatcharrLogo]] = {}

    def __init__(self, client: DispatcharrClient):
        """Initialize logo manager.

        Args:
            client: Authenticated DispatcharrClient instance
        """
        self._client = client
        self._url = client._base_url

        # Initialize cache for this URL if not exists
        if self._url not in self._caches:
            self._caches[self._url] = {}

    @property
    def _cache(self) -> dict[str, DispatcharrLogo]:
        """Get URL-based logo cache for this client."""
        return self._caches[self._url]

    def clear_cache(self) -> None:
        """Clear logo cache."""
        self._cache.clear()
        logger.debug("LogoManager cache cleared")

    def _ensure_cache(self) -> None:
        """Ensure cache is populated."""
        if not self._cache:
            logos = self._client.paginated_get(
                "/api/channels/logos/?page_size=500",
                error_context="logos",
            )
            for logo_data in logos:
                logo = DispatcharrLogo.from_api(logo_data)
                if logo.url:
                    self._cache[logo.url] = logo
            logger.debug(f"Cached {len(self._cache)} logos")

    def list_logos(self) -> list[DispatcharrLogo]:
        """List all logos in Dispatcharr.

        Returns:
            List of DispatcharrLogo objects
        """
        logos = self._client.paginated_get(
            "/api/channels/logos/?page_size=500",
            error_context="logos",
        )
        return [DispatcharrLogo.from_api(logo) for logo in logos]

    def get(self, logo_id: int) -> DispatcharrLogo | None:
        """Get logo by ID.

        Args:
            logo_id: Logo ID

        Returns:
            DispatcharrLogo or None if not found
        """
        response = self._client.get(f"/api/channels/logos/{logo_id}/")
        if response and response.status_code == 200:
            return DispatcharrLogo.from_api(response.json())
        return None

    def find_by_url(self, url: str) -> DispatcharrLogo | None:
        """Find logo by URL.

        Uses cache for O(1) lookup.

        Args:
            url: Logo URL to search for

        Returns:
            DispatcharrLogo or None if not found
        """
        self._ensure_cache()
        return self._cache.get(url)

    def upload(self, name: str, url: str) -> OperationResult:
        """Upload a logo or find existing by URL.

        If a logo with the same URL already exists, returns that logo
        instead of creating a duplicate.

        Args:
            name: Display name for the logo
            url: URL of the logo image

        Returns:
            OperationResult with success status and logo data
        """
        # Check if logo already exists
        existing = self.find_by_url(url)
        if existing:
            return OperationResult(
                success=True,
                logo={"id": existing.id, "name": existing.name, "url": existing.url},
                message="Logo already exists",
            )

        # Upload new logo
        response = self._client.post(
            "/api/channels/logos/",
            {"name": name, "url": url},
        )

        if response is None:
            return OperationResult(
                success=False,
                error=self._client.parse_api_error(response),
            )

        if response.status_code in (200, 201):
            logo_data = response.json()
            logo = DispatcharrLogo.from_api(logo_data)
            # Update cache
            self._cache[url] = logo
            return OperationResult(
                success=True,
                logo=logo_data,
                data=logo_data,
            )

        return OperationResult(
            success=False,
            error=self._client.parse_api_error(response),
        )

    def delete(self, logo_id: int) -> OperationResult:
        """Delete a logo from Dispatcharr.

        Note: Deleting a logo that's in use by channels may fail.

        Args:
            logo_id: Logo ID to delete

        Returns:
            OperationResult with success status
        """
        # Get logo first for cache invalidation
        logo = self.get(logo_id)

        response = self._client.delete(f"/api/channels/logos/{logo_id}/")

        if response is None:
            return OperationResult(
                success=False,
                error=self._client.parse_api_error(response),
            )

        if response.status_code in (200, 204):
            # Remove from cache
            if logo and logo.url and logo.url in self._cache:
                del self._cache[logo.url]
            return OperationResult(success=True)

        if response.status_code == 404:
            return OperationResult(success=False, error="Logo not found")

        return OperationResult(
            success=False,
            error=self._client.parse_api_error(response),
        )

    def upload_or_find(self, name: str, url: str) -> int | None:
        """Upload logo or find existing, returning just the ID.

        Convenience method for common use case.

        Args:
            name: Display name for the logo
            url: URL of the logo image

        Returns:
            Logo ID or None if upload failed
        """
        result = self.upload(name, url)
        if result.success and result.logo:
            return result.logo.get("id")
        return None
