"""Update checker service for version notifications.

Checks for updates from GitHub Releases (stable) or GHCR (dev builds).
Supports optional notifications, caching, and rate limiting.
"""

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import httpx

logger = logging.getLogger(__name__)


@dataclass
class UpdateInfo:
    """Information about available updates."""

    current_version: str
    latest_version: str | None
    update_available: bool
    checked_at: datetime
    build_type: Literal["stable", "dev", "unknown"]
    download_url: str | None = None
    release_notes_url: str | None = None
    latest_stable: str | None = None  # Always fetch stable version
    latest_dev: str | None = None  # Always fetch dev version (if available)


class UpdateChecker:
    """Base class for update checking."""

    def __init__(
        self,
        current_version: str,
        cache_duration_hours: int = 6,
        timeout_seconds: int = 10,
    ):
        """Initialize update checker.

        Args:
            current_version: Current application version
            cache_duration_hours: How long to cache results (default: 6 hours)
            timeout_seconds: HTTP request timeout (default: 10 seconds)
        """
        self.current_version = current_version
        self.cache_duration_hours = cache_duration_hours
        self.timeout_seconds = timeout_seconds
        self._cached_result: UpdateInfo | None = None
        self._last_check_time: float = 0

    def _is_cache_valid(self) -> bool:
        """Check if cached result is still valid."""
        if not self._cached_result:
            return False
        cache_age = time.time() - self._last_check_time
        cache_max_age = self.cache_duration_hours * 3600
        return cache_age < cache_max_age

    def check_for_updates(self, force: bool = False) -> UpdateInfo | None:
        """Check for updates with caching.

        Args:
            force: Skip cache and force a fresh check

        Returns:
            UpdateInfo if check succeeded, None if check failed
        """
        if not force and self._is_cache_valid():
            logger.debug("[UPDATE_CHECKER] Using cached result")
            return self._cached_result

        try:
            result = self._fetch_update_info()
            self._cached_result = result
            self._last_check_time = time.time()
            return result
        except Exception as e:
            logger.warning("[UPDATE_CHECKER] Failed to check for updates: %s", e)
            # Return cached result if available, even if expired
            return self._cached_result

    def _fetch_update_info(self) -> UpdateInfo:
        """Fetch update information (implemented by subclasses)."""
        raise NotImplementedError


class StableUpdateChecker(UpdateChecker):
    """Check for stable release updates via GitHub Releases API."""

    def __init__(
        self,
        current_version: str,
        owner: str = "Pharaoh-Labs",
        repo: str = "teamarr",
        **kwargs,
    ):
        """Initialize stable update checker.

        Args:
            current_version: Current application version
            owner: GitHub repository owner (default: "Pharaoh-Labs")
            repo: GitHub repository name (default: "teamarr")
            **kwargs: Additional arguments passed to UpdateChecker
        """
        super().__init__(current_version, **kwargs)
        self.owner = owner
        self.repo = repo

    def _fetch_update_info(self) -> UpdateInfo:
        """Fetch latest stable release from GitHub."""
        url = f"https://api.github.com/repos/{self.owner}/{self.repo}/releases/latest"

        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(url)
            response.raise_for_status()
            data = response.json()

        latest_version = data["tag_name"].lstrip("v")
        current_clean = self.current_version.split("-")[0].lstrip("v")

        # Simple version comparison (assumes semver)
        update_available = self._compare_versions(current_clean, latest_version)

        return UpdateInfo(
            current_version=self.current_version,
            latest_version=latest_version,
            update_available=update_available,
            checked_at=datetime.now(UTC),
            build_type="stable",
            download_url=data.get("html_url"),
            release_notes_url=data.get("html_url"),
            latest_stable=latest_version,
            latest_dev=None,  # Stable checker doesn't fetch dev info
        )

    @staticmethod
    def _compare_versions(current: str, latest: str) -> bool:
        """Compare semantic versions.

        Args:
            current: Current version (e.g., "2.0.11")
            latest: Latest version (e.g., "2.0.12")

        Returns:
            True if latest > current
        """
        try:
            current_parts = [int(x) for x in current.split(".")]
            latest_parts = [int(x) for x in latest.split(".")]

            # Pad to same length
            max_len = max(len(current_parts), len(latest_parts))
            current_parts.extend([0] * (max_len - len(current_parts)))
            latest_parts.extend([0] * (max_len - len(latest_parts)))

            return latest_parts > current_parts
        except (ValueError, AttributeError):
            logger.warning("[UPDATE_CHECKER] Invalid version format: %s or %s", current, latest)
            return False


class DevUpdateChecker(UpdateChecker):
    """Check for dev build updates via GitHub commit comparison."""

    def __init__(
        self,
        current_version: str,
        owner: str = "Pharaoh-Labs",
        repo: str = "teamarr",
        dev_branch: str = "dev",
        **kwargs,
    ):
        """Initialize dev update checker.

        Args:
            current_version: Current application version with commit SHA
            owner: GitHub repository owner
            repo: GitHub repository name
            dev_branch: Branch to check for latest commit (default: "dev")
            **kwargs: Additional arguments passed to UpdateChecker
        """
        super().__init__(current_version, **kwargs)
        self.owner = owner
        self.repo = repo
        self.dev_branch = dev_branch

    def _extract_sha_from_version(self, version: str) -> str | None:
        """Extract commit SHA from version string.

        Args:
            version: Version string (e.g., "2.0.11-dev+abc123")

        Returns:
            Commit SHA if found, None otherwise
        """
        if "+" in version:
            return version.split("+")[-1]
        return None

    def _fetch_latest_commit_sha(self) -> str | None:
        """Fetch the latest commit SHA from GitHub for the dev branch.

        Returns:
            Latest commit SHA (short form, 6-7 chars) or None if failed
        """
        try:
            url = f"https://api.github.com/repos/{self.owner}/{self.repo}/commits/{self.dev_branch}"
            
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.get(url)
                response.raise_for_status()
                data = response.json()
                
                # Get short SHA (first 7 characters to match git convention)
                full_sha = data.get("sha", "")
                return full_sha[:7] if full_sha else None
        except Exception as e:
            logger.debug("[UPDATE_CHECKER] Failed to fetch latest commit from GitHub: %s", e)
            return None

    def _fetch_update_info(self) -> UpdateInfo:
        """Fetch latest dev build info using GitHub commit comparison.

        Method: Compare commit SHAs
        1. Extract SHA from current version (e.g., "2.0.11-dev+abc123" → "abc123")
        2. Fetch latest commit SHA from GitHub dev branch
        3. Compare: if different → update available

        Returns:
            UpdateInfo with update availability status
        """
        current_sha = self._extract_sha_from_version(self.current_version)
        latest_sha = self._fetch_latest_commit_sha()
        update_available = False

        if current_sha and latest_sha:
            # Compare SHAs (case-insensitive, handle different lengths)
            # current_sha might be 6 chars, latest_sha is 7 chars
            min_len = min(len(current_sha), len(latest_sha))
            update_available = latest_sha[:min_len].lower() != current_sha[:min_len].lower()
            logger.debug(
                "[UPDATE_CHECKER] Commit comparison: current=%s, latest=%s, update=%s",
                current_sha,
                latest_sha,
                update_available,
            )
        elif not current_sha:
            logger.warning("[UPDATE_CHECKER] No commit SHA in current version: %s", self.current_version)
        elif not latest_sha:
            logger.warning("[UPDATE_CHECKER] Failed to fetch latest commit from GitHub")

        # Use "unknown" if we couldn't fetch the latest SHA
        display_sha = latest_sha if latest_sha else "unknown"

        return UpdateInfo(
            current_version=self.current_version,
            latest_version=f"{self.dev_branch} ({display_sha})",
            update_available=update_available,
            checked_at=datetime.now(UTC),
            build_type="dev",
            download_url=f"https://github.com/{self.owner}/{self.repo}/tree/{self.dev_branch}",
            latest_stable=None,
            latest_dev=display_sha,
        )


class ComprehensiveUpdateChecker(UpdateChecker):
    """Check for both stable and dev updates, providing complete information."""

    def __init__(
        self,
        current_version: str,
        owner: str = "Pharaoh-Labs",
        repo: str = "teamarr",
        dev_branch: str = "dev",
        **kwargs,
    ):
        """Initialize comprehensive update checker.

        Args:
            current_version: Current application version
            owner: GitHub repository owner (default: "Pharaoh-Labs")
            repo: GitHub repository name (default: "teamarr")
            dev_branch: Git branch to check for dev builds (default: "dev")
            **kwargs: Additional arguments passed to UpdateChecker
        """
        super().__init__(current_version, **kwargs)
        self.owner = owner
        self.repo = repo
        self.dev_branch = dev_branch
        
        # Detect current build type
        self.is_dev = "-" in current_version and "+" in current_version

    def _fetch_latest_stable(self) -> str | None:
        """Fetch latest stable release version from GitHub.

        Returns:
            Latest stable version (e.g., "2.0.11") or None if failed
        """
        try:
            url = f"https://api.github.com/repos/{self.owner}/{self.repo}/releases/latest"
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.get(url)
                response.raise_for_status()
                data = response.json()
                return data["tag_name"].lstrip("v")
        except Exception as e:
            logger.debug("[UPDATE_CHECKER] Failed to fetch latest stable release: %s", e)
            return None

    def _fetch_latest_dev_sha(self) -> str | None:
        """Fetch latest commit SHA from dev branch.

        Returns:
            Latest commit SHA (short, 7 chars) or None if failed
        """
        try:
            url = f"https://api.github.com/repos/{self.owner}/{self.repo}/commits/{self.dev_branch}"
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.get(url)
                response.raise_for_status()
                data = response.json()
                full_sha = data.get("sha", "")
                return full_sha[:7] if full_sha else None
        except Exception as e:
            logger.debug("[UPDATE_CHECKER] Failed to fetch latest dev commit: %s", e)
            return None

    def _extract_sha_from_version(self, version: str) -> str | None:
        """Extract commit SHA from version string.

        Args:
            version: Version string (e.g., "2.0.11-dev+abc123")

        Returns:
            Commit SHA if found, None otherwise
        """
        if "+" in version:
            return version.split("+")[-1]
        return None

    def _compare_versions(self, current: str, latest: str) -> bool:
        """Compare semantic versions.

        Args:
            current: Current version (e.g., "2.0.11")
            latest: Latest version (e.g., "2.0.12")

        Returns:
            True if latest > current
        """
        try:
            current_parts = [int(x) for x in current.split(".")]
            latest_parts = [int(x) for x in latest.split(".")]

            # Pad to same length
            max_len = max(len(current_parts), len(latest_parts))
            current_parts += [0] * (max_len - len(current_parts))
            latest_parts += [0] * (max_len - len(latest_parts))

            return latest_parts > current_parts
        except (ValueError, AttributeError):
            return False

    def _fetch_update_info(self) -> UpdateInfo:
        """Fetch both stable and dev update information.

        Returns:
            UpdateInfo with complete stable and dev version details
        """
        # Fetch both stable and dev info
        latest_stable = self._fetch_latest_stable()
        latest_dev_sha = self._fetch_latest_dev_sha()

        # Determine update availability based on build type
        update_available = False
        build_type = "dev" if self.is_dev else "stable"
        
        if self.is_dev:
            # Dev build - check against latest dev commit
            current_sha = self._extract_sha_from_version(self.current_version)
            if current_sha and latest_dev_sha:
                min_len = min(len(current_sha), len(latest_dev_sha))
                update_available = latest_dev_sha[:min_len].lower() != current_sha[:min_len].lower()
                logger.debug(
                    "[UPDATE_CHECKER] Dev commit comparison: current=%s, latest=%s, update=%s",
                    current_sha,
                    latest_dev_sha,
                    update_available,
                )
            
            latest_version = f"{self.dev_branch} ({latest_dev_sha if latest_dev_sha else 'unknown'})"
            download_url = f"https://github.com/{self.owner}/{self.repo}/tree/{self.dev_branch}"
            release_notes_url = None
        else:
            # Stable build - check against latest stable release
            if latest_stable:
                current_clean = self.current_version.split("-")[0].lstrip("v")
                update_available = self._compare_versions(current_clean, latest_stable)
                logger.debug(
                    "[UPDATE_CHECKER] Stable version comparison: current=%s, latest=%s, update=%s",
                    current_clean,
                    latest_stable,
                    update_available,
                )
            
            latest_version = latest_stable if latest_stable else "unknown"
            download_url = f"https://github.com/{self.owner}/{self.repo}/releases/latest"
            release_notes_url = download_url

        return UpdateInfo(
            current_version=self.current_version,
            latest_version=latest_version,
            update_available=update_available,
            checked_at=datetime.now(UTC),
            build_type=build_type,
            download_url=download_url,
            release_notes_url=release_notes_url,
            latest_stable=latest_stable,
            latest_dev=latest_dev_sha,
        )



def create_update_checker(
    version: str,
    owner: str = "Pharaoh-Labs",
    repo: str = "teamarr",
    dev_branch: str = "dev",
    cache_duration_hours: int = 6,
) -> UpdateChecker:
    """Factory function to create appropriate update checker.

    Always fetches both stable and dev information for comprehensive visibility.

    Args:
        version: Current application version
        owner: GitHub repository owner (default: "Pharaoh-Labs")
        repo: GitHub repository name (default: "teamarr")
        dev_branch: Git branch to check for dev builds (default: "dev")
        cache_duration_hours: How long to cache results

    Returns:
        ComprehensiveUpdateChecker that fetches both stable and dev info
    """
    return ComprehensiveUpdateChecker(
        current_version=version,
        owner=owner,
        repo=repo,
        dev_branch=dev_branch,
        cache_duration_hours=cache_duration_hours,
    )
