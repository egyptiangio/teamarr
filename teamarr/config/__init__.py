"""Application configuration.

Single source of truth for all configuration values.
Loads from environment variables with .env file support.
"""

import os
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# =============================================================================
# VERSION - Read from pyproject.toml (single source of truth)
# =============================================================================


def _get_base_version() -> str:
    """Read version - prefer pyproject.toml (source of truth), fall back to installed metadata."""
    # Try pyproject.toml first (single source of truth, works in dev and Docker)
    try:
        import tomllib

        pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"
        if pyproject_path.exists():
            with open(pyproject_path, "rb") as f:
                data = tomllib.load(f)
                return data.get("project", {}).get("version", "0.0.0")
    except (OSError, KeyError, ValueError):
        pass

    # Fall back to installed package metadata (pip install without source)
    try:
        from importlib.metadata import version

        return version("teamarr")
    except ImportError:
        pass

    return "0.0.0"


BASE_VERSION = _get_base_version()


def _get_version() -> str:
    """
    Get version string with automatic dev/branch detection.

    Returns:
        - "X.Y.Z" on main/master branch (stable release)
        - "X.Y.Z-dev+SHA" on dev branch with commit SHA
        - "X.Y.Z-branch+SHA" on other branches
    """
    version = BASE_VERSION
    branch = None
    sha = None

    base_dir = Path(__file__).parent.parent.parent

    # First, try to read from Docker build-time files
    try:
        branch_file = base_dir / ".git-branch"
        sha_file = base_dir / ".git-sha"

        if branch_file.exists():
            branch = branch_file.read_text().strip()

        if sha_file.exists():
            sha = sha_file.read_text().strip()
    except OSError:
        pass

    # Fallback to environment variables
    if not branch:
        branch = os.environ.get("GIT_BRANCH")
    if not sha:
        sha = os.environ.get("GIT_SHA")

    # Last fallback: try git commands (for development)
    if not branch or branch == "unknown":
        try:
            git_dir = base_dir / ".git"
            if git_dir.exists():
                branch = subprocess.check_output(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    stderr=subprocess.DEVNULL,
                    text=True,
                    cwd=base_dir,
                ).strip()

                if not sha or sha == "unknown":
                    try:
                        sha = subprocess.check_output(
                            ["git", "rev-parse", "--short=6", "HEAD"],
                            stderr=subprocess.DEVNULL,
                            text=True,
                            cwd=base_dir,
                        ).strip()
                    except (subprocess.SubprocessError, OSError):
                        pass
        except (subprocess.SubprocessError, OSError):
            pass

    # Build version string
    if branch and branch != "unknown":
        if branch in ["main", "master"]:
            # Clean version for production
            pass
        elif sha and sha != "unknown":
            # Dev and feature branches get SHA suffix
            version = f"{BASE_VERSION}-{branch}+{sha}"
        else:
            # Fallback without SHA
            version = f"{BASE_VERSION}-{branch}"

    return version


VERSION = _get_version()

# Load .env file from project root
_PROJECT_ROOT = Path(__file__).parent.parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"
load_dotenv(_ENV_FILE)


class Config:
    """Application configuration singleton.

    All configuration values should be accessed through this class.
    Values are loaded from environment variables with sensible defaults.

    Settings from database are loaded at app startup via load_settings_from_db().
    Config does NOT import from database layer (layer separation).
    """

    # EPG Timezone - From TZ env var or loaded from DB at startup
    _timezone_from_env: str | None = os.getenv("TZ") or os.getenv("USER_TIMEZONE")
    _timezone_cache: str | None = None

    # UI Timezone - From TZ env var (immutable at runtime)
    # Falls back to EPG timezone if not set
    _ui_timezone_from_env: str | None = os.getenv("TZ")

    # Display settings - Loaded from DB at startup
    _display_settings_cache: dict | None = None

    # Default display settings (used before DB is loaded)
    _DEFAULT_DISPLAY_SETTINGS: dict = {
        "time_format": "12h",
        "show_timezone": True,
        "channel_id_format": "{team_name_pascal}.{league_id}",
        "xmltv_generator_name": "Teamarr",
        "xmltv_generator_url": "https://github.com/Pharaoh-Labs/teamarr",
    }

    # Default timezone (used before DB is loaded)
    _DEFAULT_TIMEZONE: str = "America/New_York"

    # Database
    DATABASE_PATH: str = os.getenv(
        "DATABASE_PATH",
        str(_PROJECT_ROOT / "data" / "teamarr.db"),
    )

    # API
    API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
    API_PORT: int = int(os.getenv("API_PORT", "8000"))

    # ESPN API (no auth required, but good to have configurable)
    ESPN_API_BASE: str = os.getenv(
        "ESPN_API_BASE",
        "https://site.api.espn.com/apis/site/v2/sports",
    )

    @classmethod
    def get_timezone_str(cls) -> str:
        """Get the EPG timezone as a string.

        This returns the user-configured epg_timezone from the database.
        The TZ env var does NOT override this - it only affects ui_timezone.

        Priority:
        1. Cached value from database (set at startup from epg_timezone setting)
        2. Default timezone (before DB is loaded)
        """
        # Use cached value from DB (user's epg_timezone setting)
        if cls._timezone_cache:
            return cls._timezone_cache

        # Fallback to default (before DB is loaded)
        return cls._DEFAULT_TIMEZONE

    @classmethod
    def get_timezone(cls) -> ZoneInfo:
        """Get the user timezone as a ZoneInfo object.

        This is THE method for getting timezone. Use it everywhere.
        """
        return ZoneInfo(cls.get_timezone_str())

    @classmethod
    def set_timezone(cls, timezone: str) -> None:
        """Set the cached timezone (called by app startup or settings update)."""
        cls._timezone_cache = timezone

    @classmethod
    def reload(cls) -> None:
        """Reload configuration from environment.

        Useful for testing or runtime config changes.
        Note: Does not reload database settings - call set methods for that.
        """
        load_dotenv(_ENV_FILE, override=True)
        cls._timezone_from_env = os.getenv("TZ") or os.getenv("USER_TIMEZONE")
        cls._ui_timezone_from_env = os.getenv("TZ")

    @classmethod
    def get_display_settings(cls) -> dict:
        """Get display settings.

        Returns cached settings from database, or defaults if not yet loaded.
        """
        if cls._display_settings_cache is not None:
            return cls._display_settings_cache
        return cls._DEFAULT_DISPLAY_SETTINGS.copy()

    @classmethod
    def set_display_settings(
        cls,
        time_format: str,
        show_timezone: bool,
        channel_id_format: str,
        xmltv_generator_name: str,
        xmltv_generator_url: str,
    ) -> None:
        """Set the cached display settings (called by app startup or settings update)."""
        cls._display_settings_cache = {
            "time_format": time_format,
            "show_timezone": show_timezone,
            "channel_id_format": channel_id_format,
            "xmltv_generator_name": xmltv_generator_name,
            "xmltv_generator_url": xmltv_generator_url,
        }

    @classmethod
    def clear_display_cache(cls) -> None:
        """Clear cached display settings (forces reload on next access)."""
        cls._display_settings_cache = None

    @classmethod
    def clear_timezone_cache(cls) -> None:
        """Clear cached timezone (forces reload on next access)."""
        cls._timezone_cache = None

    # =========================================================================
    # UI Timezone (for frontend display, from env var)
    # =========================================================================

    @classmethod
    def get_ui_timezone_str(cls) -> str:
        """Get the UI display timezone as a string.

        Priority:
        1. TZ env var (if set and valid)
        2. Fall back to EPG timezone (user-configurable)
        """
        if cls._ui_timezone_from_env:
            # Validate timezone string
            try:
                ZoneInfo(cls._ui_timezone_from_env)
                return cls._ui_timezone_from_env
            except (KeyError, ValueError):
                # Invalid timezone, fall back to EPG timezone
                pass
        return cls.get_timezone_str()

    @classmethod
    def get_ui_timezone(cls) -> ZoneInfo:
        """Get the UI display timezone as a ZoneInfo object."""
        return ZoneInfo(cls.get_ui_timezone_str())

    @classmethod
    def is_ui_timezone_from_env(cls) -> bool:
        """Check if UI timezone is set via environment variable.

        Returns True if TZ env var is set AND valid.
        """
        if not cls._ui_timezone_from_env:
            return False
        # Validate it's a valid timezone
        try:
            ZoneInfo(cls._ui_timezone_from_env)
            return True
        except (KeyError, ValueError):
            return False


def get_user_timezone() -> ZoneInfo:
    """Get the configured user timezone.

    This is the single source of truth for timezone.
    Import this function wherever you need timezone.
    """
    return Config.get_timezone()


def get_user_timezone_str() -> str:
    """Get the configured user timezone as a string."""
    return Config.get_timezone_str()


def set_timezone(timezone: str) -> None:
    """Set the cached timezone (called by settings update)."""
    Config.set_timezone(timezone)


def clear_timezone_cache() -> None:
    """Clear cached timezone (call after epg_timezone setting update)."""
    Config.clear_timezone_cache()


def get_display_settings() -> dict:
    """Get display settings (time format, show timezone, etc.).

    Returns:
        Dict with time_format ('12h' or '24h'), show_timezone (bool), etc.
    """
    return Config.get_display_settings()


def set_display_settings(
    time_format: str,
    show_timezone: bool,
    channel_id_format: str,
    xmltv_generator_name: str,
    xmltv_generator_url: str,
) -> None:
    """Set the cached display settings (called by settings update)."""
    Config.set_display_settings(
        time_format=time_format,
        show_timezone=show_timezone,
        channel_id_format=channel_id_format,
        xmltv_generator_name=xmltv_generator_name,
        xmltv_generator_url=xmltv_generator_url,
    )


def get_time_format() -> str:
    """Get time format setting ('12h' or '24h')."""
    return Config.get_display_settings().get("time_format", "12h")


def get_show_timezone() -> bool:
    """Get show timezone setting."""
    return Config.get_display_settings().get("show_timezone", True)


def clear_display_cache() -> None:
    """Clear cached display settings (call after settings update)."""
    Config.clear_display_cache()


# =============================================================================
# UI Timezone exports (for frontend display)
# =============================================================================


def get_ui_timezone() -> ZoneInfo:
    """Get the UI display timezone.

    Used for frontend date/time display. Falls back to EPG timezone if
    TZ env var is not set.
    """
    return Config.get_ui_timezone()


def get_ui_timezone_str() -> str:
    """Get the UI display timezone as a string."""
    return Config.get_ui_timezone_str()


def is_ui_timezone_from_env() -> bool:
    """Check if UI timezone is from environment variable (immutable)."""
    return Config.is_ui_timezone_from_env()
