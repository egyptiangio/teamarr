"""Update checker service for version notifications.

Checks for updates from GitHub Releases (stable) and GitHub Commits (dev builds).
Supports caching and rate limiting.
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
    latest_stable: str | None = None
    latest_dev: str | None = None
    commits_behind: int | None = None  # For dev builds: how many commits behind


class ComprehensiveUpdateChecker:
    """Check for both stable and dev updates, providing complete information.
    
    Always fetches both stable (from GitHub Releases) and dev (from GitHub Commits)
    information in a single check, regardless of the current build type.
    """

    def __init__(
        self,
        current_version: str,
        owner: str = "Pharaoh-Labs",
        repo: str = "teamarr",
        dev_branch: str = "dev",
        cache_duration_hours: int = 6,
        timeout_seconds: int = 10,
    ):
        """Initialize comprehensive update checker.

        Args:
            current_version: Current application version
            owner: GitHub repository owner (default: "Pharaoh-Labs")
            repo: GitHub repository name (default: "teamarr")
            dev_branch: Git branch to check for dev builds (default: "dev")
            cache_duration_hours: How long to cache results (default: 6 hours)
            timeout_seconds: HTTP request timeout (default: 10 seconds)
        """
        self.current_version = current_version
        self.owner = owner
        self.repo = repo
        self.dev_branch = dev_branch
        self.cache_duration_hours = cache_duration_hours
        self.timeout_seconds = timeout_seconds
        self._cached_result: UpdateInfo | None = None
        self._last_check_time: float = 0
        
        # Detect current build type
        self.is_dev = "-" in current_version and "+" in current_version
    
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

    def _fetch_latest_dev_sha_from_branch(self, branch: str) -> str | None:
        """Fetch latest commit SHA from specified branch.

        Args:
            branch: Branch name to fetch from
            
        Returns:
            Latest commit SHA (short, 7 chars) or None if failed
        """
        try:
            url = f"https://api.github.com/repos/{self.owner}/{self.repo}/commits/{branch}"
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.get(url)
                response.raise_for_status()
                data = response.json()
                full_sha = data.get("sha", "")
                return full_sha[:7] if full_sha else None
        except Exception as e:
            logger.debug("[UPDATE_CHECKER] Failed to fetch latest commit from branch %s: %s", branch, e)
            return None

    def _fetch_commits_behind(self, current_sha: str, latest_sha: str) -> int | None:
        """Calculate how many commits behind the current SHA is from latest.

        Uses GitHub's compare API to determine the number of commits between two SHAs.

        Args:
            current_sha: Current commit SHA (can be short or full)
            latest_sha: Latest commit SHA (can be short or full)

        Returns:
            Number of commits behind, or None if cannot be determined
        """
        try:
            # Use GitHub compare API: base...head
            url = f"https://api.github.com/repos/{self.owner}/{self.repo}/compare/{current_sha}...{latest_sha}"
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.get(url)
                response.raise_for_status()
                data = response.json()
                
                # Get number of commits ahead
                commits_ahead = data.get("ahead_by", 0)
                
                # If status is "behind" or "diverged", we're behind
                if data.get("status") in ["behind", "diverged"]:
                    return commits_ahead
                
                return 0 if commits_ahead == 0 else None
        except Exception as e:
            logger.debug("[UPDATE_CHECKER] Failed to calculate commits behind: %s", e)
            return None

    def _fetch_update_info(self) -> UpdateInfo:
        """Fetch both stable and dev update information.

        Returns:
            UpdateInfo with complete stable and dev version details
        """
        # Fetch both stable and dev info
        latest_stable = self._fetch_latest_stable()
        
        # Fetch latest dev SHA from the configured/detected branch
        latest_dev_sha = self._fetch_latest_dev_sha_from_branch(self.dev_branch)

        # Determine update availability based on build type
        update_available = False
        build_type = "dev" if self.is_dev else "stable"
        commits_behind = None
        
        if self.is_dev:
            # Dev build - check against latest commit on the current branch (may be feature branch)
            current_sha = self._extract_sha(self.current_version)
            latest_current_branch_sha = self._fetch_latest_dev_sha_from_branch(self.dev_branch)
            
            if current_sha and latest_current_branch_sha:
                min_len = min(len(current_sha), len(latest_current_branch_sha))
                update_available = latest_current_branch_sha[:min_len].lower() != current_sha[:min_len].lower()
                
                # Calculate commits behind if update available
                if update_available:
                    commits_behind = self._fetch_commits_behind(current_sha, latest_current_branch_sha)
                
                logger.debug(
                    "[UPDATE_CHECKER] Dev commit comparison: current=%s, latest=%s, update=%s, behind=%s",
                    current_sha,
                    latest_current_branch_sha,
                    update_available,
                    commits_behind,
                )
            
            latest_version = latest_current_branch_sha if latest_current_branch_sha else "unknown"
            download_url = f"https://github.com/{self.owner}/{self.repo}/tree/{self.dev_branch}"
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

        return UpdateInfo(
            current_version=self.current_version,
            latest_version=latest_version,
            update_available=update_available,
            checked_at=datetime.now(UTC),
            build_type=build_type,
            download_url=download_url,
            latest_stable=latest_stable,
            latest_dev=latest_dev_sha,  # Always from display branch
            commits_behind=commits_behind,
        )

    @staticmethod
    def _extract_sha(version: str) -> str | None:
        """Extract commit SHA from version string.

        Args:
            version: Version string (e.g., "2.0.11-dev+abc123")

        Returns:
            Commit SHA if found, None otherwise
        """
        if "+" in version:
            return version.split("+")[-1]
        return None

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
            current_parts += [0] * (max_len - len(current_parts))
            latest_parts += [0] * (max_len - len(latest_parts))

            return latest_parts > current_parts
        except (ValueError, AttributeError):
            return False

def create_update_checker(
    version: str,
    owner: str = "Pharaoh-Labs",
    repo: str = "teamarr",
    dev_branch: str = "dev",
    display_dev_branch: str | None = None,
    cache_duration_hours: int = 6,
) -> ComprehensiveUpdateChecker:
    """Factory function to create update checker.

    Always creates a ComprehensiveUpdateChecker that fetches both stable 
    and dev information for complete visibility.

    Args:
        version: Current application version
        owner: GitHub repository owner (default: "Pharaoh-Labs")
        repo: GitHub repository name (default: "teamarr")
        dev_branch: Git branch to check for dev builds (default: "dev")
        display_dev_branch: Branch to use for fetching latest_dev display (default: same as dev_branch)
        cache_duration_hours: How long to cache results (default: 6 hours)

    Returns:
        ComprehensiveUpdateChecker instance
    """
    return ComprehensiveUpdateChecker(
        current_version=version,
        owner=owner,
        repo=repo,
        dev_branch=dev_branch,
        display_dev_branch=display_dev_branch,
        cache_duration_hours=cache_duration_hours,
    )

