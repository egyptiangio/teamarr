"""Application configuration.

Single source of truth for all configuration values.
Loads from environment variables with .env file support.
"""

import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# Load .env file from project root
_PROJECT_ROOT = Path(__file__).parent.parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"
load_dotenv(_ENV_FILE)


class Config:
    """Application configuration singleton.

    All configuration values should be accessed through this class.
    Values are loaded from environment variables with sensible defaults.
    """

    # Timezone - THE single source of truth for all time display
    USER_TIMEZONE: str = os.getenv("USER_TIMEZONE", "America/New_York")

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
    def get_timezone(cls) -> ZoneInfo:
        """Get the user timezone as a ZoneInfo object.

        This is THE method for getting timezone. Use it everywhere.
        """
        return ZoneInfo(cls.USER_TIMEZONE)

    @classmethod
    def reload(cls) -> None:
        """Reload configuration from environment.

        Useful for testing or runtime config changes.
        """
        load_dotenv(_ENV_FILE, override=True)
        cls.USER_TIMEZONE = os.getenv("USER_TIMEZONE", "America/New_York")


def get_user_timezone() -> ZoneInfo:
    """Get the configured user timezone.

    This is the single source of truth for timezone.
    Import this function wherever you need timezone.
    """
    return Config.get_timezone()


def get_user_timezone_str() -> str:
    """Get the configured user timezone as a string."""
    return Config.USER_TIMEZONE
