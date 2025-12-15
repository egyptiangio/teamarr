"""Token management for Dispatcharr API authentication.

Provides just-in-time authentication with automatic token refresh
and session management for Dispatcharr API integration.
"""

import logging
import threading
from datetime import datetime, timedelta

import httpx

logger = logging.getLogger(__name__)


class TokenManager:
    """Manages JWT tokens for Dispatcharr API authentication.

    Features:
    - Automatic token caching per URL/username combination
    - Proactive token refresh before expiry (1 minute buffer)
    - Automatic re-authentication on token failure
    - Thread-safe session management

    Usage:
        manager = TokenManager("http://localhost:9191", "admin", "password")
        token = manager.get_token()
        if token:
            headers = {"Authorization": f"Bearer {token}"}
    """

    # Class-level session storage for multi-instance support
    # Key: "{url}_{username}" -> session dict
    _sessions: dict[str, dict] = {}

    # Token refresh buffer (refresh this many minutes before expiry)
    TOKEN_REFRESH_BUFFER_MINUTES = 1

    # Token validity duration (Dispatcharr default is ~5 minutes)
    TOKEN_VALIDITY_MINUTES = 5

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        timeout: float = 30.0,
    ):
        """Initialize token manager.

        Args:
            base_url: Base URL of Dispatcharr instance (e.g., "http://localhost:9191")
            username: Dispatcharr username
            password: Dispatcharr password
            timeout: Request timeout in seconds (default: 30.0)
        """
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._timeout = timeout
        self._session_key = f"{self._base_url}_{self._username}"
        self._lock = threading.Lock()

        # Initialize session if not exists
        if self._session_key not in self._sessions:
            self._sessions[self._session_key] = {
                "access_token": None,
                "refresh_token": None,
                "token_expiry": None,
            }

    @property
    def _session(self) -> dict:
        """Get current session data."""
        return self._sessions[self._session_key]

    def get_token(self) -> str | None:
        """Get a valid access token, authenticating if necessary.

        Thread-safe: Uses lock to prevent concurrent authentication attempts.

        Returns:
            Valid access token or None if authentication fails
        """
        with self._lock:
            # Check if current token is valid
            if self._is_valid():
                return self._session["access_token"]

            # Try to refresh token
            if self._session["refresh_token"] and self._refresh():
                return self._session["access_token"]

            # Full authentication
            if self._authenticate():
                return self._session["access_token"]

            return None

    def clear(self) -> None:
        """Clear cached tokens for this session."""
        with self._lock:
            self._session["access_token"] = None
            self._session["refresh_token"] = None
            self._session["token_expiry"] = None

    def _is_valid(self) -> bool:
        """Check if current access token is still valid."""
        if not self._session["access_token"]:
            return False
        if not self._session["token_expiry"]:
            return False
        return datetime.now() < self._session["token_expiry"]

    def _refresh(self) -> bool:
        """Attempt to refresh the access token using refresh token.

        Returns:
            True if refresh successful, False otherwise
        """
        if not self._session["refresh_token"]:
            return False

        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(
                    f"{self._base_url}/api/accounts/token/refresh/",
                    json={"refresh": self._session["refresh_token"]},
                )

            if response.status_code == 200:
                data = response.json()
                self._session["access_token"] = data.get("access")
                self._session["token_expiry"] = datetime.now() + timedelta(
                    minutes=self.TOKEN_VALIDITY_MINUTES - self.TOKEN_REFRESH_BUFFER_MINUTES
                )
                logger.debug("Dispatcharr token refreshed successfully")
                return True

            logger.warning(f"Token refresh failed: {response.status_code}")
            return False

        except httpx.RequestError as e:
            logger.warning(f"Token refresh request failed: {e}")
            return False

    def _authenticate(self) -> bool:
        """Perform full authentication with username/password.

        Returns:
            True if authentication successful, False otherwise
        """
        try:
            logger.debug(f"Authenticating to {self._base_url} as {self._username}")

            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(
                    f"{self._base_url}/api/accounts/token/",
                    json={
                        "username": self._username,
                        "password": self._password,
                    },
                )

            if response.status_code == 200:
                data = response.json()
                self._session["access_token"] = data.get("access")
                self._session["refresh_token"] = data.get("refresh")
                self._session["token_expiry"] = datetime.now() + timedelta(
                    minutes=self.TOKEN_VALIDITY_MINUTES - self.TOKEN_REFRESH_BUFFER_MINUTES
                )
                logger.info("Dispatcharr authentication successful")
                return True

            if response.status_code == 401:
                logger.error("Authentication failed: Invalid credentials")
                return False

            if response.status_code == 403:
                logger.error(f"Authentication failed: Forbidden - {response.text}")
                return False

            logger.error(f"Authentication failed: {response.status_code} - {response.text}")
            return False

        except httpx.RequestError as e:
            logger.error(f"Authentication request failed: {e}")
            return False

    @classmethod
    def clear_all_sessions(cls) -> None:
        """Clear all cached sessions (useful for testing)."""
        cls._sessions.clear()
