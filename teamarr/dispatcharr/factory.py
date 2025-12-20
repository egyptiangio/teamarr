"""Dispatcharr client factory.

Creates and manages DispatcharrClient instances based on database settings.
Provides a singleton pattern for the application-wide client.
"""

import logging
import threading
from dataclasses import dataclass
from typing import Any

from teamarr.dispatcharr.client import DispatcharrClient
from teamarr.dispatcharr.managers.channels import ChannelManager
from teamarr.dispatcharr.managers.epg import EPGManager
from teamarr.dispatcharr.managers.logos import LogoManager
from teamarr.dispatcharr.managers.m3u import M3UManager

logger = logging.getLogger(__name__)


@dataclass
class DispatcharrConnection:
    """Container for Dispatcharr client and managers.

    Provides convenient access to all Dispatcharr functionality.
    """

    client: DispatcharrClient
    channels: ChannelManager
    epg: EPGManager
    m3u: M3UManager
    logos: LogoManager

    def close(self) -> None:
        """Close the underlying client connection."""
        self.client.close()


class DispatcharrFactory:
    """Factory for creating and managing Dispatcharr connections.

    Thread-safe singleton pattern ensures only one active connection exists.
    Connection is recreated if settings change.

    Usage:
        factory = DispatcharrFactory(db_factory)

        # Get connection (creates if needed)
        conn = factory.get_connection()
        if conn:
            channels = conn.channels.get_channels()

        # Test connection
        result = factory.test_connection()
        if result.success:
            print(f"Connected to {result.version}")

        # Force reconnect (after settings change)
        factory.reconnect()
    """

    def __init__(self, db_factory: Any):
        """Initialize the factory.

        Args:
            db_factory: Factory function returning database connection
        """
        self._db_factory = db_factory
        self._connection: DispatcharrConnection | None = None
        self._lock = threading.Lock()
        self._settings_hash: str | None = None

    @property
    def is_configured(self) -> bool:
        """Check if Dispatcharr settings are configured."""
        from teamarr.database.settings import get_dispatcharr_settings

        with self._db_factory() as conn:
            settings = get_dispatcharr_settings(conn)

        return bool(settings.enabled and settings.url and settings.username)

    @property
    def is_connected(self) -> bool:
        """Check if there's an active connection."""
        return self._connection is not None

    def get_connection(self) -> DispatcharrConnection | None:
        """Get the Dispatcharr connection, creating if needed.

        Returns:
            DispatcharrConnection or None if not configured
        """
        if not self.is_configured:
            return None

        with self._lock:
            # Check if settings changed
            current_hash = self._get_settings_hash()
            if self._connection and self._settings_hash != current_hash:
                logger.info("Dispatcharr settings changed, reconnecting")
                self._close_connection()

            # Create connection if needed
            if not self._connection:
                self._connection = self._create_connection()
                self._settings_hash = current_hash

            return self._connection

    def get_client(self) -> DispatcharrClient | None:
        """Get just the DispatcharrClient (for passing to services).

        Returns:
            DispatcharrClient or None if not configured
        """
        conn = self.get_connection()
        return conn.client if conn else None

    def reconnect(self) -> DispatcharrConnection | None:
        """Force reconnection with current settings.

        Returns:
            New DispatcharrConnection or None if not configured
        """
        with self._lock:
            self._close_connection()
            self._settings_hash = None

        return self.get_connection()

    def close(self) -> None:
        """Close the current connection."""
        with self._lock:
            self._close_connection()

    def test_connection(
        self,
        url: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> "ConnectionTestResult":
        """Test connection to Dispatcharr.

        If no parameters provided, uses settings from database.

        Args:
            url: Optional URL override
            username: Optional username override
            password: Optional password override

        Returns:
            ConnectionTestResult with success status and details
        """
        from teamarr.database.settings import get_dispatcharr_settings

        # Get settings
        if url and username and password:
            test_url = url
            test_username = username
            test_password = password
        else:
            with self._db_factory() as conn:
                settings = get_dispatcharr_settings(conn)

            if not settings.url or not settings.username:
                return ConnectionTestResult(
                    success=False,
                    error="Dispatcharr not configured",
                )

            test_url = url or settings.url
            test_username = username or settings.username
            test_password = password or settings.password or ""

        # Test connection
        try:
            client = DispatcharrClient(
                base_url=test_url,
                username=test_username,
                password=test_password,
            )

            # Try to get channels (simple test)
            response = client.get("/api/channels/channels/?page_size=1")
            if response is None:
                client.close()
                return ConnectionTestResult(
                    success=False,
                    url=test_url,
                    username=test_username,
                    error="Authentication failed or server unavailable",
                )

            if response.status_code != 200:
                error_msg = client.parse_api_error(response)
                client.close()
                return ConnectionTestResult(
                    success=False,
                    url=test_url,
                    username=test_username,
                    error=f"API error: {error_msg}",
                )

            data = response.json()
            # Handle both paginated (dict with count) and list responses
            if isinstance(data, dict):
                channel_count = data.get("count")
            elif isinstance(data, list):
                channel_count = len(data)
            else:
                channel_count = None

            # Get M3U accounts count (exclude "custom")
            account_count = None
            try:
                acc_response = client.get("/api/m3u/accounts/")
                if acc_response and acc_response.status_code == 200:
                    accounts = acc_response.json()
                    # Filter out "custom" account
                    accounts = [a for a in accounts if a.get("name", "").lower() != "custom"]
                    account_count = len(accounts)
            except Exception:
                pass

            # Get channel groups count
            group_count = None
            try:
                grp_response = client.get("/api/channels/groups/")
                if grp_response and grp_response.status_code == 200:
                    group_count = len(grp_response.json())
            except Exception:
                pass

            client.close()

            return ConnectionTestResult(
                success=True,
                url=test_url,
                username=test_username,
                channel_count=channel_count,
                account_count=account_count,
                group_count=group_count,
            )

        except Exception as e:
            error_msg = str(e)

            # Parse common errors
            if "401" in error_msg or "Unauthorized" in error_msg:
                error_msg = "Authentication failed - check username/password"
            elif "Connection refused" in error_msg:
                error_msg = "Connection refused - check URL"
            elif "Name or service not known" in error_msg:
                error_msg = "Invalid hostname - check URL"
            elif "timed out" in error_msg.lower():
                error_msg = "Connection timed out - check URL and network"

            return ConnectionTestResult(
                success=False,
                url=test_url,
                username=test_username,
                error=error_msg,
            )

    def _create_connection(self) -> DispatcharrConnection | None:
        """Create a new Dispatcharr connection.

        Returns:
            DispatcharrConnection or None if not configured
        """
        from teamarr.database.settings import get_dispatcharr_settings

        with self._db_factory() as conn:
            settings = get_dispatcharr_settings(conn)

        if not settings.enabled or not settings.url or not settings.username:
            return None

        try:
            client = DispatcharrClient(
                base_url=settings.url,
                username=settings.username,
                password=settings.password or "",
            )

            connection = DispatcharrConnection(
                client=client,
                channels=ChannelManager(client),
                epg=EPGManager(client),
                m3u=M3UManager(client),
                logos=LogoManager(client),
            )

            logger.info(f"Connected to Dispatcharr at {settings.url}")
            return connection

        except Exception as e:
            logger.error(f"Failed to connect to Dispatcharr: {e}")
            return None

    def _close_connection(self) -> None:
        """Close the current connection (must hold lock)."""
        if self._connection:
            try:
                self._connection.close()
            except Exception as e:
                logger.warning(f"Error closing Dispatcharr connection: {e}")
            self._connection = None

    def _get_settings_hash(self) -> str:
        """Get a hash of current settings for change detection."""
        from teamarr.database.settings import get_dispatcharr_settings

        with self._db_factory() as conn:
            settings = get_dispatcharr_settings(conn)

        return f"{settings.url}:{settings.username}:{settings.password}:{settings.enabled}"


@dataclass
class ConnectionTestResult:
    """Result of a connection test."""

    success: bool
    url: str | None = None
    username: str | None = None
    version: str | None = None
    account_count: int | None = None
    group_count: int | None = None
    channel_count: int | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = {"success": self.success}
        if self.url:
            result["url"] = self.url
        if self.username:
            result["username"] = self.username
        if self.version:
            result["version"] = self.version
        if self.account_count is not None:
            result["account_count"] = self.account_count
        if self.group_count is not None:
            result["group_count"] = self.group_count
        if self.channel_count is not None:
            result["channel_count"] = self.channel_count
        if self.error:
            result["error"] = self.error
        return result


# =============================================================================
# GLOBAL FACTORY INSTANCE
# =============================================================================

_factory: DispatcharrFactory | None = None
_factory_lock = threading.Lock()


def get_factory(db_factory: Any = None) -> DispatcharrFactory:
    """Get the global DispatcharrFactory instance.

    Args:
        db_factory: Factory function for database connections.
                   Required on first call, optional thereafter.

    Returns:
        Global DispatcharrFactory instance
    """
    global _factory

    with _factory_lock:
        if _factory is None:
            if db_factory is None:
                raise RuntimeError("db_factory required on first call to get_factory()")
            _factory = DispatcharrFactory(db_factory)
        return _factory


def get_dispatcharr_client(db_factory: Any = None) -> DispatcharrClient | None:
    """Convenience function to get the DispatcharrClient.

    Args:
        db_factory: Factory function for database connections.

    Returns:
        DispatcharrClient or None if not configured
    """
    factory = get_factory(db_factory)
    return factory.get_client()


def get_dispatcharr_connection(db_factory: Any = None) -> DispatcharrConnection | None:
    """Convenience function to get the DispatcharrConnection.

    Args:
        db_factory: Factory function for database connections.

    Returns:
        DispatcharrConnection or None if not configured
    """
    factory = get_factory(db_factory)
    return factory.get_connection()


def test_dispatcharr_connection(
    db_factory: Any = None,
    url: str | None = None,
    username: str | None = None,
    password: str | None = None,
) -> ConnectionTestResult:
    """Convenience function to test Dispatcharr connection.

    Args:
        db_factory: Factory function for database connections.
        url: Optional URL override
        username: Optional username override
        password: Optional password override

    Returns:
        ConnectionTestResult with success status and details
    """
    factory = get_factory(db_factory)
    return factory.test_connection(url, username, password)


def close_dispatcharr() -> None:
    """Close the global Dispatcharr connection."""
    global _factory

    with _factory_lock:
        if _factory:
            _factory.close()
