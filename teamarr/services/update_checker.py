"""Update checker service for version notifications.

Checks for updates from GitHub Releases (stable) and GitHub Commits (dev builds).
Supports caching and configurable repositories for forks.
"""

import logging
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

# Module-level HTTP client for connection reuse
_http_client: httpx.Client | None = None


def _get_http_client() -> httpx.Client:
    """Get or create the module-level HTTP client."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.Client(
            timeout=10,
            headers={"Accept": "application/vnd.github.v3+json"},
        )
    return _http_client


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
    latest_date: datetime | None = None  # When the latest version was released/committed


class UpdateChecker:
    """Check for both stable and dev updates from GitHub.

    Fetches latest stable release (from GitHub Releases) and latest dev commit
    (from specified branch) to provide complete update information.

    Results are cached for 1 hour to avoid hammering GitHub's API.
    """

    CACHE_TTL_SECONDS = 3600  # 1 hour

    def __init__(
        self,
        current_version: str,
        owner: str = "Pharaoh-Labs",
        repo: str = "teamarr",
        dev_branch: str = "dev",
    ):
        """Initialize update checker.

        Args:
            current_version: Current application version (e.g., "2.0.11" or "2.0.11-dev+abc123")
            owner: GitHub repository owner
            repo: GitHub repository name
            dev_branch: Branch to check for dev builds
        """
        self.current_version = current_version
        self.owner = owner
        self.repo = repo
        self.dev_branch = dev_branch

        # Parse current version
        self.is_dev = self._is_dev_version(current_version)
        self.current_sha = self._extract_sha(current_version) if self.is_dev else None

        # Cache
        self._cached_result: UpdateInfo | None = None
        self._cache_time: float = 0

    @staticmethod
    def _is_dev_version(version: str) -> bool:
        """Check if version string indicates a dev build."""
        # Dev versions look like: 2.0.11-dev+abc123 or 2.0.11-feature/branch+abc123
        return "-" in version and "+" in version

    @staticmethod
    def _extract_sha(version: str) -> str | None:
        """Extract commit SHA from version string."""
        if "+" in version:
            return version.split("+")[-1]
        return None

    @staticmethod
    def _extract_branch(version: str) -> str | None:
        """Extract branch name from version string.

        Example: "2.0.11-feature/foo+abc123" -> "feature/foo"
        Example: "2.0.11-dev+abc123" -> "dev"
        """
        if "-" not in version or "+" not in version:
            return None
        # Split on first hyphen, then remove the SHA suffix
        after_hyphen = version.split("-", 1)[1]
        return after_hyphen.split("+")[0]

    def _is_cache_valid(self) -> bool:
        """Check if cached result is still valid."""
        if self._cached_result is None:
            return False
        return (time.time() - self._cache_time) < self.CACHE_TTL_SECONDS

    def check_for_updates(self, force: bool = False) -> UpdateInfo | None:
        """Check for updates with caching.

        Args:
            force: Skip cache and force a fresh check

        Returns:
            UpdateInfo if check succeeded, None if check failed and no cache
        """
        if not force and self._is_cache_valid():
            return self._cached_result

        try:
            result = self._fetch_update_info()
            self._cached_result = result
            self._cache_time = time.time()
            return result
        except Exception as e:
            logger.warning("[UPDATE_CHECKER] Failed to check for updates: %s", e)
            # Return cached result if available, None otherwise
            return self._cached_result

    def _fetch_update_info(self) -> UpdateInfo:
        """Fetch both stable and dev update information from GitHub."""
        latest_stable, stable_date = self._fetch_latest_stable()
        latest_dev_sha, dev_date = self._fetch_latest_commit_sha(self.dev_branch)

        # Determine if update is available based on build type
        update_available = False
        latest_date: datetime | None = None

        if self.is_dev:
            # For dev builds, compare commit dates (more reliable than SHA comparison)
            latest_date = dev_date
            if self.current_sha and latest_dev_sha:
                # Use 6-char comparison to match git --short=6 used in version string
                current_short = self.current_sha[:6].lower()
                latest_short = latest_dev_sha[:6].lower()
                if current_short != latest_short:
                    # SHAs differ - check if latest is actually newer by date
                    if dev_date:
                        current_date = self._fetch_commit_date(self.current_sha)
                        if current_date and dev_date > current_date:
                            update_available = True
                        # If we can't get current commit date, don't assume update available
                        # This avoids false positives for local commits not yet pushed
                    # If we can't get latest date, don't assume update available

            latest_version = latest_dev_sha[:6] if latest_dev_sha else "unknown"
            download_url = f"https://github.com/{self.owner}/{self.repo}/tree/{self.dev_branch}"
            build_type: Literal["stable", "dev", "unknown"] = "dev"
        else:
            # For stable builds, compare semantic versions
            latest_date = stable_date
            if latest_stable:
                current_clean = self.current_version.split("-")[0].lstrip("v")
                update_available = self._is_newer_version(latest_stable, current_clean)

            latest_version = latest_stable if latest_stable else "unknown"
            download_url = f"https://github.com/{self.owner}/{self.repo}/releases/latest"
            build_type = "stable"

        return UpdateInfo(
            current_version=self.current_version,
            latest_version=latest_version,
            update_available=update_available,
            checked_at=datetime.now(UTC),
            build_type=build_type,
            download_url=download_url,
            latest_stable=latest_stable,
            latest_dev=latest_dev_sha[:6] if latest_dev_sha else None,
            latest_date=latest_date,
        )

    def _fetch_latest_stable(self) -> tuple[str | None, datetime | None]:
        """Fetch latest stable release version and date from GitHub Releases."""
        url = f"https://api.github.com/repos/{self.owner}/{self.repo}/releases/latest"
        try:
            response = _get_http_client().get(url)
            if response.status_code == 404:
                logger.debug("[UPDATE_CHECKER] No releases found for %s/%s", self.owner, self.repo)
                return None, None
            response.raise_for_status()
            data = response.json()
            version = data["tag_name"].lstrip("v")
            published_at = data.get("published_at")
            release_date = None
            if published_at:
                release_date = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            return version, release_date
        except httpx.HTTPStatusError as e:
            logger.debug("[UPDATE_CHECKER] GitHub API error fetching releases: %s", e)
            return None, None
        except Exception as e:
            logger.debug("[UPDATE_CHECKER] Failed to fetch latest release: %s", e)
            return None, None

    def _fetch_latest_commit_sha(self, branch: str) -> tuple[str | None, datetime | None]:
        """Fetch latest commit SHA and date from a branch."""
        encoded_branch = quote(branch, safe="")
        url = f"https://api.github.com/repos/{self.owner}/{self.repo}/commits/{encoded_branch}"
        try:
            response = _get_http_client().get(url)
            if response.status_code == 404:
                logger.debug("[UPDATE_CHECKER] Branch %s not found", branch)
                return None, None
            response.raise_for_status()
            data = response.json()
            sha = data.get("sha")
            commit_date = None
            if data.get("commit", {}).get("committer", {}).get("date"):
                date_str = data["commit"]["committer"]["date"]
                commit_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return sha, commit_date
        except Exception as e:
            logger.debug("[UPDATE_CHECKER] Failed to fetch commit for %s: %s", branch, e)
            return None, None

    def _fetch_commit_date(self, sha: str) -> datetime | None:
        """Fetch the date of a specific commit."""
        url = f"https://api.github.com/repos/{self.owner}/{self.repo}/commits/{sha}"
        try:
            response = _get_http_client().get(url)
            if response.status_code == 404:
                logger.debug("[UPDATE_CHECKER] Commit %s not found", sha)
                return None
            response.raise_for_status()
            data = response.json()
            if data.get("commit", {}).get("committer", {}).get("date"):
                date_str = data["commit"]["committer"]["date"]
                return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return None
        except Exception as e:
            logger.debug("[UPDATE_CHECKER] Failed to fetch commit date for %s: %s", sha, e)
            return None

    @staticmethod
    def _is_newer_version(latest: str, current: str) -> bool:
        """Compare semantic versions. Returns True if latest > current."""
        try:
            # Parse versions like "2.0.11" or "2.1.0"
            def parse_version(v: str) -> tuple[int, ...]:
                # Remove leading 'v' and any suffix
                clean = v.lstrip("v").split("-")[0]
                parts = clean.split(".")
                return tuple(int(p) for p in parts)

            latest_tuple = parse_version(latest)
            current_tuple = parse_version(current)
            return latest_tuple > current_tuple
        except (ValueError, IndexError):
            # If parsing fails, assume no update
            return False


def create_update_checker(
    version: str,
    owner: str = "Pharaoh-Labs",
    repo: str = "teamarr",
    dev_branch: str = "dev",
    auto_detect_branch: bool = True,
) -> UpdateChecker:
    """Factory function to create an UpdateChecker with optional branch auto-detection.

    Args:
        version: Current application version
        owner: GitHub repository owner
        repo: GitHub repository name
        dev_branch: Default branch for dev builds
        auto_detect_branch: If True, extract branch from version string for dev builds

    Returns:
        Configured UpdateChecker instance
    """
    # Auto-detect branch from version string if enabled and running dev build
    effective_branch = dev_branch
    if auto_detect_branch and UpdateChecker._is_dev_version(version):
        detected = UpdateChecker._extract_branch(version)
        if detected:
            effective_branch = detected
            logger.debug("[UPDATE_CHECKER] Auto-detected branch: %s", effective_branch)

    return UpdateChecker(
        current_version=version,
        owner=owner,
        repo=repo,
        dev_branch=effective_branch,
    )
