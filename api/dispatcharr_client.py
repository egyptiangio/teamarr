"""
Dispatcharr API Client with JIT Authentication

Provides just-in-time authentication with automatic token refresh
and session management for Dispatcharr API integration.

Retry Strategy:
- Exponential backoff: 1s, 2s, 4s, 8s, 16s, 32s (capped)
- Jitter: ±50% randomization to prevent thundering herd
- Max retries: 5 (configurable)
- Retryable: ConnectionError, Timeout, 502, 503, 504
"""

import logging
import random
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any
import requests

logger = logging.getLogger(__name__)


# =============================================================================
# Retry Configuration
# =============================================================================

# Retryable exceptions (connection-level failures)
RETRYABLE_EXCEPTIONS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)

# Retryable HTTP status codes (server-side transient errors)
RETRYABLE_STATUS_CODES = {502, 503, 504}


def _calculate_backoff(attempt: int, base_delay: float = 1.0, max_delay: float = 32.0) -> float:
    """
    Calculate delay with exponential backoff and jitter.

    Formula: min(max_delay, base_delay * 2^attempt) * random(0.5, 1.5)

    Args:
        attempt: Current attempt number (0-indexed)
        base_delay: Initial delay in seconds (default: 1.0)
        max_delay: Maximum delay cap in seconds (default: 32.0)

    Returns:
        Delay in seconds with jitter applied

    Example delays:
        Attempt 0: 0.5-1.5s   (base * 1)
        Attempt 1: 1-3s       (base * 2)
        Attempt 2: 2-6s       (base * 4)
        Attempt 3: 4-12s      (base * 8)
        Attempt 4: 8-24s      (base * 16)
        Attempt 5: 16-32s     (base * 32, capped)
    """
    delay = min(max_delay, base_delay * (2 ** attempt))
    # Add jitter: ±50%
    jitter = random.uniform(0.5, 1.5)
    return delay * jitter


class DispatcharrAuth:
    """
    Just-In-Time authentication handler for Dispatcharr API.

    Features:
    - Automatic token caching per URL/username combination
    - Proactive token refresh before expiry
    - Automatic re-authentication on token failure
    - Thread-safe session management
    - HTTP connection pooling via requests.Session for performance

    Usage:
        auth = DispatcharrAuth("http://localhost:9191", "admin", "password")
        token = auth.get_token()
        response = auth.request("GET", "/api/epg/sources/")
    """

    # Class-level session storage for multi-instance support
    _sessions: Dict[str, Dict] = {}

    # Class-level HTTP session storage (connection pooling)
    _http_sessions: Dict[str, requests.Session] = {}

    # Token refresh buffer (refresh this many minutes before expiry)
    TOKEN_REFRESH_BUFFER_MINUTES = 1

    # Token validity duration (Dispatcharr default is ~5 minutes)
    TOKEN_VALIDITY_MINUTES = 5

    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        timeout: int = 30
    ):
        """
        Initialize Dispatcharr authentication handler.

        Args:
            url: Base URL of Dispatcharr instance (e.g., "http://localhost:9191")
            username: Dispatcharr username
            password: Dispatcharr password
            timeout: Request timeout in seconds (default: 30)
        """
        self.url = url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self._session_key = f"{self.url}_{self.username}"

        # Initialize token session if not exists
        if self._session_key not in self._sessions:
            self._sessions[self._session_key] = {
                "access_token": None,
                "refresh_token": None,
                "token_expiry": None
            }

        # Initialize HTTP session for connection pooling (one per base URL)
        if self.url not in self._http_sessions:
            session = requests.Session()
            # Configure connection pooling - keep connections alive for reuse
            adapter = requests.adapters.HTTPAdapter(
                pool_connections=10,   # Number of connection pools (per host)
                pool_maxsize=100,      # Max connections per pool (matches ThreadPoolExecutor workers)
                max_retries=0          # We handle retries ourselves
            )
            session.mount('http://', adapter)
            session.mount('https://', adapter)
            self._http_sessions[self.url] = session

    @property
    def _http_session(self) -> requests.Session:
        """Get the HTTP session for connection pooling"""
        return self._http_sessions[self.url]

    @property
    def _session(self) -> Dict:
        """Get current session data"""
        return self._sessions[self._session_key]

    def _is_token_valid(self) -> bool:
        """Check if current access token is still valid"""
        if not self._session["access_token"]:
            return False
        if not self._session["token_expiry"]:
            return False
        return datetime.now() < self._session["token_expiry"]

    def _refresh_token(self) -> bool:
        """
        Attempt to refresh the access token using refresh token.

        Returns:
            True if refresh successful, False otherwise
        """
        if not self._session["refresh_token"]:
            return False

        try:
            response = self._http_session.post(
                f"{self.url}/api/accounts/token/refresh/",
                json={"refresh": self._session["refresh_token"]},
                timeout=self.timeout
            )

            if response.status_code == 200:
                data = response.json()
                self._session["access_token"] = data.get("access")
                self._session["token_expiry"] = (
                    datetime.now() +
                    timedelta(minutes=self.TOKEN_VALIDITY_MINUTES - self.TOKEN_REFRESH_BUFFER_MINUTES)
                )
                logger.debug("Dispatcharr token refreshed successfully")
                return True
            else:
                logger.warning(f"Token refresh failed: {response.status_code}")
                return False

        except requests.RequestException as e:
            logger.warning(f"Token refresh request failed: {e}")
            return False

    def _authenticate(self) -> bool:
        """
        Perform full authentication with username/password.

        Returns:
            True if authentication successful, False otherwise
        """
        try:
            logger.debug(f"Authenticating to {self.url} as {self.username}")

            response = self._http_session.post(
                f"{self.url}/api/accounts/token/",
                json={
                    "username": self.username,
                    "password": self.password
                },
                timeout=self.timeout
            )

            if response.status_code == 200:
                data = response.json()
                self._session["access_token"] = data.get("access")
                self._session["refresh_token"] = data.get("refresh")
                self._session["token_expiry"] = (
                    datetime.now() +
                    timedelta(minutes=self.TOKEN_VALIDITY_MINUTES - self.TOKEN_REFRESH_BUFFER_MINUTES)
                )
                logger.info("Dispatcharr authentication successful")
                return True

            elif response.status_code == 401:
                logger.error("Authentication failed: Invalid credentials")
                return False

            elif response.status_code == 403:
                logger.error(f"Authentication failed: Forbidden - {response.text}")
                return False

            else:
                logger.error(f"Authentication failed: {response.status_code} - {response.text}")
                return False

        except requests.RequestException as e:
            logger.error(f"Authentication request failed: {e}")
            return False

    def get_token(self) -> Optional[str]:
        """
        Get a valid access token, authenticating if necessary.

        Returns:
            Valid access token or None if authentication fails
        """
        # Check if current token is valid
        if self._is_token_valid():
            return self._session["access_token"]

        # Try to refresh token
        if self._session["refresh_token"] and self._refresh_token():
            return self._session["access_token"]

        # Full authentication
        if self._authenticate():
            return self._session["access_token"]

        return None

    def clear_session(self):
        """Clear cached tokens for this session"""
        self._session["access_token"] = None
        self._session["refresh_token"] = None
        self._session["token_expiry"] = None

    def request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        retry_on_401: bool = True,
        max_retries: int = 5
    ) -> Optional[requests.Response]:
        """
        Make an authenticated request to Dispatcharr API with retry support.

        Uses exponential backoff with jitter for transient errors:
        - Connection errors, timeouts, chunked encoding errors
        - HTTP 502, 503, 504 responses

        Args:
            method: HTTP method (GET, POST, PATCH, DELETE)
            endpoint: API endpoint (e.g., "/api/epg/sources/")
            data: JSON data for POST/PATCH requests
            retry_on_401: Whether to retry with fresh token on 401
            max_retries: Maximum retry attempts for transient errors (default: 5)

        Returns:
            Response object or None if request fails after all retries
        """
        token = self.get_token()
        if not token:
            logger.error("Failed to obtain authentication token")
            return None

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        full_url = f"{self.url}{endpoint}"
        last_exception = None

        for attempt in range(max_retries + 1):
            try:
                if method.upper() == "GET":
                    response = self._http_session.get(full_url, headers=headers, timeout=self.timeout)
                elif method.upper() == "POST":
                    response = self._http_session.post(full_url, headers=headers, json=data, timeout=self.timeout)
                elif method.upper() == "PATCH":
                    response = self._http_session.patch(full_url, headers=headers, json=data, timeout=self.timeout)
                elif method.upper() == "DELETE":
                    response = self._http_session.delete(full_url, headers=headers, timeout=self.timeout)
                else:
                    logger.error(f"Unsupported HTTP method: {method}")
                    return None

                # Handle 401 with re-authentication (not counted as retry)
                if response.status_code == 401 and retry_on_401:
                    logger.info("Received 401, clearing session and retrying...")
                    self.clear_session()
                    return self.request(method, endpoint, data, retry_on_401=False, max_retries=max_retries)

                # Check for retryable HTTP status codes
                if response.status_code in RETRYABLE_STATUS_CODES:
                    if attempt < max_retries:
                        delay = _calculate_backoff(attempt)
                        logger.warning(
                            f"Retryable HTTP {response.status_code} for {method} {endpoint}, "
                            f"retry {attempt + 1}/{max_retries} after {delay:.1f}s"
                        )
                        time.sleep(delay)
                        continue
                    else:
                        logger.error(f"Max retries exceeded for {method} {endpoint} (HTTP {response.status_code})")

                return response

            except RETRYABLE_EXCEPTIONS as e:
                last_exception = e

                if attempt < max_retries:
                    delay = _calculate_backoff(attempt)
                    logger.warning(
                        f"Retryable error for {method} {endpoint}: {type(e).__name__}, "
                        f"retry {attempt + 1}/{max_retries} after {delay:.1f}s"
                    )
                    time.sleep(delay)
                else:
                    logger.error(f"Max retries exceeded for {method} {endpoint}: {e}")

            except requests.RequestException as e:
                # Non-retryable request exception
                logger.error(f"Request failed (non-retryable): {e}")
                return None

        # All retries exhausted
        if last_exception:
            logger.error(f"Request failed after {max_retries} retries: {last_exception}")
        return None

    # Convenience methods
    def get(self, endpoint: str) -> Optional[requests.Response]:
        """Make authenticated GET request"""
        return self.request("GET", endpoint)

    def post(self, endpoint: str, data: Dict = None) -> Optional[requests.Response]:
        """Make authenticated POST request"""
        return self.request("POST", endpoint, data)


class EPGManager:
    """
    High-level EPG management interface for Dispatcharr.

    Usage:
        manager = EPGManager("http://localhost:9191", "admin", "password")
        sources = manager.list_sources()
        manager.refresh(21)
    """

    def __init__(self, url: str, username: str, password: str):
        self.auth = DispatcharrAuth(url, username, password)

    def list_sources(self, include_dummy: bool = True) -> List[Dict]:
        """
        List all EPG sources.

        Args:
            include_dummy: Whether to include dummy sources (default: True)

        Returns:
            List of EPG source dictionaries
        """
        response = self.auth.get("/api/epg/sources/")
        if response is None or response.status_code != 200:
            return []

        sources = response.json()

        if not include_dummy:
            sources = [s for s in sources if s.get("source_type") != "dummy"]

        return sources

    def get_source(self, epg_id: int) -> Optional[Dict]:
        """
        Get a specific EPG source by ID.

        Args:
            epg_id: EPG source ID

        Returns:
            EPG source dict or None if not found
        """
        response = self.auth.get(f"/api/epg/sources/{epg_id}/")
        if response and response.status_code == 200:
            return response.json()
        return None

    def find_by_name(self, name: str, exact: bool = False) -> Optional[Dict]:
        """
        Find EPG source by name.

        Args:
            name: Name to search for
            exact: If True, require exact match; otherwise partial match

        Returns:
            First matching EPG source or None
        """
        sources = self.list_sources()

        for source in sources:
            source_name = source.get("name", "")
            if exact:
                if source_name == name:
                    return source
            else:
                if name.lower() in source_name.lower():
                    return source

        return None

    def refresh(self, epg_id: int) -> Dict[str, Any]:
        """
        Trigger refresh for a single EPG source.

        Args:
            epg_id: EPG source ID to refresh

        Returns:
            Result dict with "success" and "message" keys
        """
        response = self.auth.post("/api/epg/import/", {"id": epg_id})

        if response is None:
            return {"success": False, "message": "Request failed - could not connect"}

        if response.status_code == 202:
            return {"success": True, "message": "EPG refresh initiated"}
        elif response.status_code == 400:
            return {"success": False, "message": "Cannot refresh dummy EPG source"}
        elif response.status_code == 401:
            return {"success": False, "message": "Authentication failed"}
        elif response.status_code == 404:
            return {"success": False, "message": "EPG source not found"}
        else:
            try:
                msg = response.json().get("message", f"HTTP {response.status_code}")
            except:
                msg = f"HTTP {response.status_code}"
            return {"success": False, "message": msg}

    def wait_for_refresh(
        self,
        epg_id: int,
        timeout: int = 60,
        poll_interval: int = 2
    ) -> Dict[str, Any]:
        """
        Trigger EPG refresh and wait for completion.

        Dispatcharr's EPG import is async (returns 202). This method triggers
        the refresh and polls until completion by monitoring status and updated_at.

        EPG status values: idle, fetching, parsing, error, success, disabled

        Args:
            epg_id: EPG source ID to refresh
            timeout: Maximum seconds to wait (default: 60)
            poll_interval: Seconds between status checks (default: 2)

        Returns:
            Result dict with:
            - success: bool
            - message: str
            - duration: float (seconds taken)
            - source: dict (final EPG source state if successful)
        """
        import time

        # Get current state before refresh
        before = self.get_source(epg_id)
        if not before:
            return {"success": False, "message": f"EPG source {epg_id} not found"}

        before_updated = before.get('updated_at')

        # Trigger refresh
        trigger_result = self.refresh(epg_id)
        if not trigger_result.get('success'):
            return trigger_result

        # Poll until status changes to success/error or updated_at changes
        start_time = time.time()
        last_logged_status = None
        last_status = None
        last_message = None

        while time.time() - start_time < timeout:
            time.sleep(poll_interval)

            current = self.get_source(epg_id)
            if not current:
                logger.debug(f"EPG refresh poll: could not get source {epg_id}")
                continue

            current_status = current.get('status', '')
            current_updated = current.get('updated_at')
            current_message = current.get('last_message', '')
            last_status = current_status
            last_message = current_message

            # Log status changes
            if current_status != last_logged_status:
                elapsed = time.time() - start_time
                logger.debug(f"EPG refresh poll: status={current_status}, message='{current_message}', elapsed={elapsed:.1f}s")
                last_logged_status = current_status

            # Check if refresh completed (status is success and updated_at changed)
            if current_status == 'success' and current_updated != before_updated:
                duration = time.time() - start_time
                return {
                    "success": True,
                    "message": current.get('last_message', 'EPG refresh completed'),
                    "duration": duration,
                    "source": current
                }
            elif current_status == 'error':
                duration = time.time() - start_time
                return {
                    "success": False,
                    "message": current.get('last_message', 'EPG refresh failed'),
                    "duration": duration,
                    "source": current
                }

            # Still in progress (fetching, parsing, idle)
            # Continue polling

        # Timeout - but check if status is actually success
        # When no channels are mapped, Dispatcharr completes instantly but updated_at doesn't change
        # Status 'success' means the EPG was parsed - "No channels mapped" is informational
        if last_status == 'success':
            return {
                "success": True,
                "message": last_message or 'EPG refresh completed (no channels mapped yet)',
                "duration": timeout,
                "source": None  # Don't have final source state
            }

        return {
            "success": False,
            "message": f"EPG refresh timed out after {timeout} seconds (last status: {last_status}, message: {last_message})",
            "duration": timeout,
            "last_status": last_status,
            "last_message": last_message
        }

    def refresh_by_name(self, name: str) -> Dict[str, Any]:
        """
        Refresh EPG source by name (partial match).

        Args:
            name: Name to search for

        Returns:
            Result dict with "success" and "message" keys
        """
        source = self.find_by_name(name)
        if not source:
            return {"success": False, "message": f"No EPG source found matching '{name}'"}

        return self.refresh(source["id"])

    def test_connection(self) -> Dict[str, Any]:
        """
        Test connection to Dispatcharr.

        Returns:
            Result dict with "success", "message", and optionally "sources" keys
        """
        try:
            token = self.auth.get_token()
            if not token:
                return {
                    "success": False,
                    "message": "Authentication failed - check credentials"
                }

            sources = self.list_sources()
            return {
                "success": True,
                "message": f"Connected successfully. Found {len(sources)} EPG source(s).",
                "sources": sources
            }

        except requests.exceptions.ConnectionError:
            return {
                "success": False,
                "message": "Connection failed - check URL and ensure Dispatcharr is running"
            }
        except requests.exceptions.Timeout:
            return {
                "success": False,
                "message": "Connection timed out"
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Error: {str(e)}"
            }


class M3UManager:
    """
    M3U account and stream management for Dispatcharr.

    Usage:
        manager = M3UManager("http://localhost:9191", "admin", "password")
        accounts = manager.list_m3u_accounts()
        groups = manager.list_channel_groups(search="NFL")
        streams = manager.list_streams(group_name="NFL Game Pass 🏈")
    """

    def __init__(self, url: str, username: str, password: str):
        self.auth = DispatcharrAuth(url, username, password)
        self._groups_cache: Optional[List[Dict]] = None

    def list_m3u_accounts(self) -> List[Dict]:
        """List all M3U accounts."""
        response = self.auth.get("/api/m3u/accounts/")
        if response is None or response.status_code != 200:
            logger.error(f"Failed to list M3U accounts: {response.status_code if response else 'No response'}")
            return []
        return response.json()

    def list_channel_groups(self, search: Optional[str] = None) -> List[Dict]:
        """
        List channel groups, optionally filtered by name.

        Args:
            search: Filter by group name (case-insensitive substring match)

        Returns:
            List of group dicts with id, name, m3u_accounts
        """
        response = self.auth.get("/api/channels/groups/")
        if response is None or response.status_code != 200:
            logger.error(f"Failed to list channel groups: {response.status_code if response else 'No response'}")
            return []

        groups = response.json()
        self._groups_cache = groups  # Cache for name lookups

        if search:
            search_lower = search.lower()
            groups = [g for g in groups if search_lower in g.get('name', '').lower()]

        return groups

    def get_group_name(self, group_id: int) -> Optional[str]:
        """Get exact group name by ID (needed for stream filtering)."""
        if self._groups_cache is None:
            self.list_channel_groups()

        group = next((g for g in (self._groups_cache or []) if g.get('id') == group_id), None)
        return group.get('name') if group else None

    def list_streams(
        self,
        group_name: Optional[str] = None,
        group_id: Optional[int] = None,
        account_id: Optional[int] = None,
        limit: Optional[int] = None
    ) -> List[Dict]:
        """
        List streams from Dispatcharr.

        Filter by group using exact group_name (preferred) or group_id (requires lookup).
        The API's channel_group_name filter requires exact match including emoji.

        Args:
            group_name: Exact group name (e.g., "NFL Game Pass 🏈")
            group_id: Group ID (will lookup name if group_name not provided)
            account_id: Filter by M3U account ID
            limit: Maximum streams to return

        Returns:
            List of stream dicts with id, name, url, channel_group, tvg_id, etc.
        """
        import urllib.parse

        # Resolve group_name from group_id if needed
        if group_name is None and group_id is not None:
            group_name = self.get_group_name(group_id)

        # Build query params
        params = ["page_size=1000"]
        if group_name:
            params.append(f"channel_group_name={urllib.parse.quote(group_name)}")
        if account_id is not None:
            params.append(f"m3u_account={account_id}")

        response = self.auth.get(f"/api/channels/streams/?{'&'.join(params)}")
        if response is None or response.status_code != 200:
            logger.error(f"Failed to list streams: {response.status_code if response else 'No response'}")
            return []

        data = response.json()
        streams = data.get('results', []) if isinstance(data, dict) else data

        if limit:
            streams = streams[:limit]

        return streams

    def get_group_with_streams(self, group_id: int, stream_limit: int = None) -> Optional[Dict]:
        """
        Get group info with its streams for UI preview.

        Args:
            group_id: Dispatcharr group ID
            stream_limit: Max streams to return (None = no limit)

        Returns:
            {"group": {...}, "streams": [...], "total_streams": int}
        """
        if self._groups_cache is None:
            self.list_channel_groups()

        group = next((g for g in (self._groups_cache or []) if g.get('id') == group_id), None)
        if not group:
            return None

        streams = self.list_streams(group_name=group.get('name'))

        return {
            "group": group,
            "streams": streams[:stream_limit] if stream_limit else streams,
            "total_streams": len(streams)
        }

    def refresh_m3u_account(self, account_id: int) -> Dict[str, Any]:
        """Trigger M3U refresh for an account (async, returns immediately)."""
        response = self.auth.post(f"/api/m3u/refresh/{account_id}/")

        if response is None:
            return {"success": False, "message": "Request failed"}

        if response.status_code in (200, 202):
            return {"success": True, "message": "M3U refresh initiated"}
        return {"success": False, "message": f"HTTP {response.status_code}"}

    def get_account(self, account_id: int) -> Optional[Dict]:
        """Get a single M3U account by ID."""
        response = self.auth.get(f"/api/m3u/accounts/{account_id}/")
        if response is None or response.status_code != 200:
            return None
        return response.json()

    def wait_for_refresh(
        self,
        account_id: int,
        timeout: int = 120,
        poll_interval: int = 2,
        skip_if_recent_minutes: int = 60
    ) -> Dict[str, Any]:
        """
        Trigger M3U refresh and wait for completion.

        This ensures streams are updated before we fetch them for EPG generation.
        Uses polling to detect when refresh completes by monitoring updated_at.
        Skips refresh if account was updated within skip_if_recent_minutes.

        Args:
            account_id: M3U account ID to refresh
            timeout: Maximum seconds to wait (default: 120)
            poll_interval: Seconds between status checks (default: 2)
            skip_if_recent_minutes: Skip refresh if updated within this many minutes (default: 60)

        Returns:
            Result dict with:
            - success: bool
            - message: str
            - duration: float (seconds taken)
            - account: dict (final account state if successful)
            - skipped: bool (True if refresh was skipped due to recent update)
        """
        import time
        from datetime import datetime, timezone

        # Get current state
        before = self.get_account(account_id)
        if not before:
            return {"success": False, "message": f"Account {account_id} not found"}

        before_updated = before.get('updated_at')

        # Check if recently refreshed - skip if within threshold
        if skip_if_recent_minutes > 0 and before_updated:
            try:
                # Parse ISO timestamp (handle both Z and +00:00 formats)
                updated_str = before_updated.replace('Z', '+00:00')
                updated_dt = datetime.fromisoformat(updated_str)
                now = datetime.now(timezone.utc)
                age_minutes = (now - updated_dt).total_seconds() / 60

                if age_minutes < skip_if_recent_minutes:
                    logger.info(f"M3U account {account_id} refreshed {age_minutes:.1f} min ago, skipping refresh")
                    return {
                        "success": True,
                        "message": f"Skipped - refreshed {age_minutes:.0f} min ago",
                        "duration": 0,
                        "skipped": True,
                        "account": before
                    }
            except (ValueError, TypeError) as e:
                logger.debug(f"Could not parse updated_at '{before_updated}': {e}")

        # Trigger refresh
        trigger_result = self.refresh_m3u_account(account_id)
        if not trigger_result.get('success'):
            return trigger_result

        # Poll until updated_at changes or status indicates completion/error
        start_time = time.time()
        while time.time() - start_time < timeout:
            time.sleep(poll_interval)

            current = self.get_account(account_id)
            if not current:
                continue

            current_status = current.get('status', '')
            current_updated = current.get('updated_at')

            # Check if refresh completed (updated_at changed)
            if current_updated != before_updated:
                duration = time.time() - start_time
                if current_status == 'success':
                    return {
                        "success": True,
                        "message": current.get('last_message', 'Refresh completed'),
                        "duration": duration,
                        "account": current
                    }
                elif current_status == 'error':
                    return {
                        "success": False,
                        "message": current.get('last_message', 'Refresh failed'),
                        "duration": duration,
                        "account": current
                    }

            # Check for error status even if updated_at hasn't changed
            if current_status == 'error':
                return {
                    "success": False,
                    "message": current.get('last_message', 'Refresh failed'),
                    "duration": time.time() - start_time
                }

        # Timeout
        return {
            "success": False,
            "message": f"Refresh timed out after {timeout} seconds",
            "duration": timeout
        }

    def refresh_multiple_accounts(
        self,
        account_ids: List[int],
        timeout: int = 120,
        poll_interval: int = 2,
        skip_if_recent_minutes: int = 60
    ) -> Dict[str, Any]:
        """
        Refresh multiple M3U accounts in parallel and wait for all to complete.

        Triggers all accounts simultaneously, then polls until all complete or timeout.
        This is more efficient than sequential refreshes when multiple event groups
        share the same M3U provider. Skips accounts refreshed within skip_if_recent_minutes.

        Args:
            account_ids: List of unique M3U account IDs to refresh
            timeout: Maximum seconds to wait for all (default: 120)
            poll_interval: Seconds between status checks (default: 2)
            skip_if_recent_minutes: Skip refresh if updated within this many minutes (default: 60)

        Returns:
            Result dict with:
            - success: bool (True if ALL succeeded)
            - results: dict mapping account_id -> result dict
            - duration: float (total seconds taken)
            - failed_count: int
            - succeeded_count: int
            - skipped_count: int
        """
        import time
        from concurrent.futures import ThreadPoolExecutor
        from datetime import datetime, timezone

        if not account_ids:
            return {
                "success": True,
                "results": {},
                "duration": 0,
                "failed_count": 0,
                "succeeded_count": 0,
                "skipped_count": 0
            }

        # Deduplicate account IDs
        unique_ids = list(set(account_ids))

        # Get initial state for all accounts and check which need refresh
        initial_states = {}
        results = {}
        ids_needing_refresh = []
        now = datetime.now(timezone.utc)

        for account_id in unique_ids:
            account = self.get_account(account_id)
            if not account:
                initial_states[account_id] = None
                ids_needing_refresh.append(account_id)
                continue

            initial_states[account_id] = account.get('updated_at')

            # Check if recently refreshed
            if skip_if_recent_minutes > 0 and account.get('updated_at'):
                try:
                    updated_str = account['updated_at'].replace('Z', '+00:00')
                    updated_dt = datetime.fromisoformat(updated_str)
                    age_minutes = (now - updated_dt).total_seconds() / 60

                    if age_minutes < skip_if_recent_minutes:
                        logger.info(f"M3U account {account_id} refreshed {age_minutes:.1f} min ago, skipping")
                        results[account_id] = {
                            "success": True,
                            "message": f"Skipped - refreshed {age_minutes:.0f} min ago",
                            "duration": 0,
                            "skipped": True
                        }
                        continue
                except (ValueError, TypeError):
                    pass

            ids_needing_refresh.append(account_id)

        # If all accounts were skipped, return early
        if not ids_needing_refresh:
            skipped_count = len(results)
            return {
                "success": True,
                "results": results,
                "duration": 0,
                "failed_count": 0,
                "succeeded_count": skipped_count,
                "skipped_count": skipped_count
            }

        # Trigger refreshes in parallel (only for accounts that need it)
        def trigger_refresh(account_id):
            return (account_id, self.refresh_m3u_account(account_id))

        trigger_results = {}
        with ThreadPoolExecutor(max_workers=len(ids_needing_refresh)) as executor:
            futures = {executor.submit(trigger_refresh, aid): aid for aid in ids_needing_refresh}
            for future in futures:
                try:
                    account_id, result = future.result()
                    trigger_results[account_id] = result
                except Exception as e:
                    account_id = futures[future]
                    trigger_results[account_id] = {"success": False, "message": str(e)}

        # Track which accounts we're waiting for
        pending = set()

        for account_id in ids_needing_refresh:
            trigger_result = trigger_results.get(account_id, {"success": False, "message": "Trigger failed"})

            if trigger_result.get('success'):
                pending.add(account_id)
            else:
                results[account_id] = {
                    "success": False,
                    "message": trigger_result.get('message', 'Failed to trigger refresh')
                }

        # Poll until all complete or timeout
        start_time = time.time()
        while pending and (time.time() - start_time) < timeout:
            time.sleep(poll_interval)

            # Check all pending accounts
            still_pending = set()
            for account_id in pending:
                current = self.get_account(account_id)
                if not current:
                    still_pending.add(account_id)
                    continue

                current_status = current.get('status', '')
                current_updated = current.get('updated_at')
                initial_updated = initial_states.get(account_id)

                # Check if refresh completed
                if current_updated != initial_updated:
                    duration = time.time() - start_time
                    if current_status == 'success':
                        results[account_id] = {
                            "success": True,
                            "message": current.get('last_message', 'Refresh completed'),
                            "duration": duration
                        }
                    elif current_status == 'error':
                        results[account_id] = {
                            "success": False,
                            "message": current.get('last_message', 'Refresh failed'),
                            "duration": duration
                        }
                    else:
                        # Status unclear but updated_at changed - assume success
                        results[account_id] = {
                            "success": True,
                            "message": "Refresh completed",
                            "duration": duration
                        }
                elif current_status == 'error':
                    results[account_id] = {
                        "success": False,
                        "message": current.get('last_message', 'Refresh failed'),
                        "duration": time.time() - start_time
                    }
                else:
                    still_pending.add(account_id)

            pending = still_pending

        # Handle any remaining pending (timed out)
        for account_id in pending:
            results[account_id] = {
                "success": False,
                "message": f"Refresh timed out after {timeout} seconds",
                "duration": timeout
            }

        # Calculate summary
        total_duration = time.time() - start_time
        skipped = sum(1 for r in results.values() if r.get('skipped'))
        succeeded = sum(1 for r in results.values() if r.get('success'))
        failed = len(results) - succeeded

        return {
            "success": failed == 0,
            "results": results,
            "duration": total_duration,
            "failed_count": failed,
            "succeeded_count": succeeded,
            "skipped_count": skipped
        }

    def test_connection(self) -> Dict[str, Any]:
        """Test connection to Dispatcharr."""
        try:
            if not self.auth.get_token():
                return {"success": False, "message": "Authentication failed"}

            accounts = self.list_m3u_accounts()
            return {
                "success": True,
                "message": f"Connected. Found {len(accounts)} M3U account(s).",
                "accounts": accounts
            }
        except Exception as e:
            return {"success": False, "message": str(e)}


class ChannelManager:
    """
    Channel management for Dispatcharr.

    Handles channel CRUD operations for the Channel Lifecycle Management feature.
    Creates, updates, and deletes channels in Dispatcharr when event streams are matched.

    Performance optimizations:
    - Channel cache: Avoids repeated get_channels() calls during EPG generation
    - Logo cache: Avoids repeated logo lookups for duplicate URL checks
    - Caches are class-level (shared across instances) keyed by URL
    - Call clear_cache() at the start of each EPG generation cycle

    Usage:
        manager = ChannelManager("http://localhost:9191", "admin", "password")
        manager.clear_cache()  # Start fresh for EPG generation
        channel = manager.create_channel(
            name="Giants @ Cowboys",
            channel_number=5001,
            stream_ids=[456],
            tvg_id="teamarr-event-12345"
        )
        manager.delete_channel(channel['id'])
    """

    # Class-level caches shared across all instances (keyed by URL)
    # This ensures multiple get_lifecycle_manager() calls benefit from same cache
    _caches: Dict[str, Dict] = {}

    def __init__(self, url: str, username: str, password: str):
        self.auth = DispatcharrAuth(url, username, password)
        self._url = url.rstrip("/")

        # Initialize cache structure for this URL if not exists
        if self._url not in self._caches:
            self._caches[self._url] = {
                'channels_cache': None,
                'channels_by_id': {},
                'channels_by_tvg_id': {},
                'channels_by_number': {},
                'logos_cache': None,
                'logos_by_url': {},
            }

    @property
    def _cache(self) -> Dict:
        """Get cache dict for this URL"""
        return self._caches[self._url]

    @property
    def _channels_cache(self) -> Optional[List[Dict]]:
        return self._cache['channels_cache']

    @_channels_cache.setter
    def _channels_cache(self, value):
        self._cache['channels_cache'] = value

    @property
    def _channels_by_id(self) -> Dict[int, Dict]:
        return self._cache['channels_by_id']

    @_channels_by_id.setter
    def _channels_by_id(self, value):
        self._cache['channels_by_id'] = value

    @property
    def _channels_by_tvg_id(self) -> Dict[str, Dict]:
        return self._cache['channels_by_tvg_id']

    @_channels_by_tvg_id.setter
    def _channels_by_tvg_id(self, value):
        self._cache['channels_by_tvg_id'] = value

    @property
    def _channels_by_number(self) -> Dict[str, Dict]:
        return self._cache['channels_by_number']

    @_channels_by_number.setter
    def _channels_by_number(self, value):
        self._cache['channels_by_number'] = value

    @property
    def _logos_cache(self) -> Optional[List[Dict]]:
        return self._cache['logos_cache']

    @_logos_cache.setter
    def _logos_cache(self, value):
        self._cache['logos_cache'] = value

    @property
    def _logos_by_url(self) -> Dict[str, Dict]:
        return self._cache['logos_by_url']

    @_logos_by_url.setter
    def _logos_by_url(self, value):
        self._cache['logos_by_url'] = value

    def clear_cache(self):
        """
        Clear all caches. Call at the start of each EPG generation cycle.
        """
        self._cache['channels_cache'] = None
        self._cache['channels_by_id'] = {}
        self._cache['channels_by_tvg_id'] = {}
        self._cache['channels_by_number'] = {}
        self._cache['logos_cache'] = None
        self._cache['logos_by_url'] = {}
        logger.debug("ChannelManager caches cleared")

    def _ensure_channels_cache(self) -> List[Dict]:
        """
        Ensure channels cache is populated. Returns cached channels list.
        """
        if self._channels_cache is None:
            self._channels_cache = self._paginated_get(
                "/api/channels/channels/?page_size=1000",
                error_context="channels"
            )
            # Build lookup indexes
            self._channels_by_id = {}
            self._channels_by_tvg_id = {}
            self._channels_by_number = {}
            for ch in self._channels_cache:
                ch_id = ch.get('id')
                if ch_id:
                    self._channels_by_id[ch_id] = ch
                tvg_id = ch.get('tvg_id')
                if tvg_id:
                    self._channels_by_tvg_id[tvg_id] = ch
                ch_num = ch.get('channel_number')
                if ch_num:
                    self._channels_by_number[str(ch_num)] = ch
            logger.debug(f"Cached {len(self._channels_cache)} channels")
        return self._channels_cache

    def _invalidate_channel_in_cache(self, channel_id: int):
        """
        Remove a channel from cache after deletion.
        """
        if channel_id in self._channels_by_id:
            channel = self._channels_by_id.pop(channel_id)
            tvg_id = channel.get('tvg_id')
            if tvg_id and tvg_id in self._channels_by_tvg_id:
                del self._channels_by_tvg_id[tvg_id]
            ch_num = channel.get('channel_number')
            if ch_num and str(ch_num) in self._channels_by_number:
                del self._channels_by_number[str(ch_num)]
            if self._channels_cache:
                self._channels_cache = [c for c in self._channels_cache if c.get('id') != channel_id]

    def _update_channel_in_cache(self, channel: Dict):
        """
        Update a channel in cache after create/update.
        """
        ch_id = channel.get('id')
        if ch_id:
            # Remove old version first
            self._invalidate_channel_in_cache(ch_id)
            # Add new version
            self._channels_by_id[ch_id] = channel
            tvg_id = channel.get('tvg_id')
            if tvg_id:
                self._channels_by_tvg_id[tvg_id] = channel
            ch_num = channel.get('channel_number')
            if ch_num:
                self._channels_by_number[str(ch_num)] = channel
            if self._channels_cache is not None:
                self._channels_cache.append(channel)

    # =========================================================================
    # HELPER METHODS - Consolidated patterns for pagination and error handling
    # =========================================================================

    def _paginated_get(
        self,
        initial_endpoint: str,
        error_context: str = "items"
    ) -> List[Dict]:
        """
        Fetch all items from a paginated API endpoint.

        Handles both paginated dict responses (with 'results' and 'next')
        and simple list responses.

        Args:
            initial_endpoint: Starting endpoint with page_size (e.g., "/api/channels/channels/?page_size=1000")
            error_context: Context for error logging (e.g., "channels", "EPG data")

        Returns:
            List of all items from all pages
        """
        from urllib.parse import urlparse

        all_items = []
        next_page = initial_endpoint

        while next_page:
            response = self.auth.get(next_page)
            if response is None or response.status_code != 200:
                logger.error(f"Failed to get {error_context}: {response.status_code if response else 'No response'}")
                break

            data = response.json()

            if isinstance(data, dict) and 'results' in data:
                all_items.extend(data['results'])
                next_url = data.get('next')
                if next_url:
                    # Handle absolute URLs by extracting path+query
                    if next_url.startswith('http'):
                        parsed = urlparse(next_url)
                        next_page = f"{parsed.path}?{parsed.query}" if parsed.query else parsed.path
                    else:
                        next_page = next_url
                else:
                    next_page = None
            elif isinstance(data, list):
                all_items.extend(data)
                next_page = None
            else:
                next_page = None

        return all_items

    def _parse_api_error(self, response) -> str:
        """
        Parse error message from API response.

        Handles various error response formats from Dispatcharr API.

        Args:
            response: requests.Response object

        Returns:
            Human-readable error message
        """
        if response is None:
            return "Request failed - no response"

        try:
            error_data = response.json()
            if isinstance(error_data, dict):
                # Format field errors (e.g., {"name": ["This field is required"]})
                errors = []
                for field, msgs in error_data.items():
                    if isinstance(msgs, list):
                        errors.append(f"{field}: {', '.join(str(m) for m in msgs)}")
                    else:
                        errors.append(f"{field}: {msgs}")
                return "; ".join(errors) if errors else str(error_data)
            else:
                return str(error_data)
        except Exception:
            return f"HTTP {response.status_code}"

    def get_channels(self, page_size: int = 1000, use_cache: bool = True) -> List[Dict]:
        """
        Get all channels from Dispatcharr.

        Handles pagination automatically. Uses cache by default during EPG generation.

        Args:
            page_size: Page size for API pagination (default: 1000)
            use_cache: Whether to use/populate the cache (default: True)

        Returns:
            List of channel dicts
        """
        if use_cache:
            return self._ensure_channels_cache()
        return self._paginated_get(
            f"/api/channels/channels/?page_size={page_size}",
            error_context="channels"
        )

    def get_channel(self, channel_id: int, use_cache: bool = True) -> Optional[Dict]:
        """
        Get a single channel by ID.

        Uses cache by default during EPG generation for O(1) lookup.

        Args:
            channel_id: Dispatcharr channel ID
            use_cache: Whether to use cache (default: True)

        Returns:
            Channel dict or None if not found
        """
        if use_cache:
            self._ensure_channels_cache()
            cached = self._channels_by_id.get(channel_id)
            if cached:
                return cached

        # Cache miss or cache disabled - fetch from API
        response = self.auth.get(f"/api/channels/channels/{channel_id}/")
        if response and response.status_code == 200:
            channel = response.json()
            if use_cache:
                self._update_channel_in_cache(channel)
            return channel
        return None

    def create_channel(
        self,
        name: str,
        channel_number: int,
        stream_ids: List[int] = None,
        tvg_id: str = None,
        channel_group_id: int = None,
        logo_id: int = None,
        stream_profile_id: int = None
    ) -> Dict[str, Any]:
        """
        Create a new channel in Dispatcharr.

        Args:
            name: Channel name (e.g., "Giants @ Cowboys")
            channel_number: Channel number
            stream_ids: List of stream IDs to attach (order = priority)
            tvg_id: TVG ID for XMLTV EPG matching
            channel_group_id: Optional group to assign channel to
            logo_id: Optional logo ID
            stream_profile_id: Optional stream profile ID

        Returns:
            Result dict with:
            - success: bool
            - channel: dict (created channel data) if successful
            - error: str if failed
        """
        payload = {
            'name': name,
            'channel_number': str(channel_number),
            'streams': stream_ids or []
        }

        if tvg_id:
            payload['tvg_id'] = tvg_id

        if channel_group_id:
            payload['channel_group_id'] = channel_group_id

        if logo_id:
            payload['logo_id'] = logo_id

        if stream_profile_id:
            payload['stream_profile_id'] = stream_profile_id

        response = self.auth.post("/api/channels/channels/", payload)

        if response is None:
            return {"success": False, "error": self._parse_api_error(response)}

        if response.status_code in (200, 201):
            channel = response.json()
            self._update_channel_in_cache(channel)
            return {"success": True, "channel": channel}

        return {"success": False, "error": self._parse_api_error(response)}

    def update_channel(self, channel_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update an existing channel.

        Args:
            channel_id: Dispatcharr channel ID
            data: Fields to update (name, channel_number, tvg_id, streams, etc.)

        Returns:
            Result dict with success, channel (if successful), or error
        """
        # Convert channel_number to string if present
        if 'channel_number' in data:
            data['channel_number'] = str(data['channel_number'])

        response = self.auth.request("PATCH", f"/api/channels/channels/{channel_id}/", data)

        if response is None:
            return {"success": False, "error": self._parse_api_error(response)}

        if response.status_code == 200:
            channel = response.json()
            self._update_channel_in_cache(channel)
            return {"success": True, "channel": channel}

        return {"success": False, "error": self._parse_api_error(response)}

    def delete_channel(self, channel_id: int) -> Dict[str, Any]:
        """
        Delete a channel from Dispatcharr.

        Args:
            channel_id: Dispatcharr channel ID

        Returns:
            Result dict with success or error
        """
        logger.debug(f"Deleting channel {channel_id} from Dispatcharr")
        response = self.auth.request("DELETE", f"/api/channels/channels/{channel_id}/")

        if response is None:
            logger.warning(f"Delete channel {channel_id}: No response from Dispatcharr")
            return {"success": False, "error": self._parse_api_error(response)}

        if response.status_code in (200, 204):
            logger.debug(f"Delete channel {channel_id}: Success (status {response.status_code})")
            self._invalidate_channel_in_cache(channel_id)
            return {"success": True}

        if response.status_code == 404:
            logger.debug(f"Delete channel {channel_id}: Not found (already deleted?)")
            self._invalidate_channel_in_cache(channel_id)
            return {"success": False, "error": "Channel not found"}

        logger.warning(f"Delete channel {channel_id}: Failed (status {response.status_code})")
        return {"success": False, "error": self._parse_api_error(response)}

    def assign_streams(self, channel_id: int, stream_ids: List[int]) -> Dict[str, Any]:
        """
        Assign streams to a channel (replaces existing streams).

        Args:
            channel_id: Dispatcharr channel ID
            stream_ids: List of stream IDs (order = priority)

        Returns:
            Result dict with success, channel, or error
        """
        return self.update_channel(channel_id, {'streams': stream_ids})

    def get_channel_streams(self, channel_id: int) -> List[Dict]:
        """
        Get streams assigned to a channel.

        Args:
            channel_id: Dispatcharr channel ID

        Returns:
            List of stream dicts
        """
        response = self.auth.get(f"/api/channels/channels/{channel_id}/streams/")
        if response and response.status_code == 200:
            return response.json()
        return []

    def find_channel_by_number(self, channel_number: int) -> Optional[Dict]:
        """
        Find a channel by its channel number.

        Uses O(1) lookup via cache index.

        Args:
            channel_number: Channel number to search for

        Returns:
            Channel dict or None if not found
        """
        self._ensure_channels_cache()
        return self._channels_by_number.get(str(channel_number))

    def find_channel_by_tvg_id(self, tvg_id: str) -> Optional[Dict]:
        """
        Find a channel by its TVG ID.

        Uses O(1) lookup via cache index.

        Args:
            tvg_id: TVG ID to search for

        Returns:
            Channel dict or None if not found
        """
        self._ensure_channels_cache()
        return self._channels_by_tvg_id.get(tvg_id)

    def set_channel_epg(self, channel_id: int, epg_data_id: int) -> Dict[str, Any]:
        """
        Set EPG data source for a channel and trigger refresh.

        This links the channel to a specific EPG source in Dispatcharr,
        which can be useful for direct EPG assignment rather than tvg_id matching.

        Args:
            channel_id: Dispatcharr channel ID
            epg_data_id: EPG data/source ID to link

        Returns:
            Result dict with success or error
        """
        response = self.auth.post(
            f"/api/channels/channels/{channel_id}/set-epg/",
            {"epg_data_id": epg_data_id}
        )

        if response is None:
            return {"success": False, "error": self._parse_api_error(response)}

        if response.status_code == 200:
            return {"success": True}

        return {"success": False, "error": self._parse_api_error(response)}

    def get_epg_data_list(self, epg_source_id: int = None) -> List[Dict]:
        """
        Get all EPGData entries from Dispatcharr.

        EPGData represents individual channel entries within an EPG source.
        Each entry has a tvg_id that can be used to match channels.

        Args:
            epg_source_id: Optional filter by EPG source ID

        Returns:
            List of EPGData dicts with id, tvg_id, name, icon_url, epg_source
        """
        all_epg_data = self._paginated_get(
            "/api/epg/epgdata/?page_size=500",
            error_context="EPG data"
        )

        # Filter by epg_source_id if specified
        if epg_source_id is not None:
            all_epg_data = [
                e for e in all_epg_data
                if e.get('epg_source') == epg_source_id
            ]

        return all_epg_data

    def find_epg_data_by_tvg_id(
        self,
        tvg_id: str,
        epg_source_id: int = None,
        epg_lookup: Dict[str, Dict] = None
    ) -> Optional[Dict]:
        """
        Find EPGData by tvg_id, optionally filtered by EPG source.

        This mimics Dispatcharr's internal EPG matching logic:
        epg_data = EPGData.objects.filter(tvg_id=tvg_id, epg_source=epg_source).first()

        Args:
            tvg_id: The tvg_id to search for (e.g., "teamarr-event-401547679")
            epg_source_id: Optional EPG source ID to filter by
            epg_lookup: Optional pre-built dict of tvg_id -> epg_data for batch lookups.
                        If provided, uses this instead of fetching the EPG data list.
                        Build with build_epg_lookup() for efficiency.

        Returns:
            EPGData dict if found, None otherwise
        """
        # Use pre-built lookup if provided (batch optimization)
        if epg_lookup is not None:
            return epg_lookup.get(tvg_id)

        # Otherwise fetch and search (single lookup fallback)
        epg_data_list = self.get_epg_data_list(epg_source_id)

        for epg_data in epg_data_list:
            if epg_data.get('tvg_id') == tvg_id:
                return epg_data

        return None

    def build_epg_lookup(self, epg_source_id: int = None) -> Dict[str, Dict]:
        """
        Build a tvg_id -> epg_data lookup dict for efficient batch lookups.

        Fetches the EPG data list once and returns a dict for O(1) lookups.
        Use this when you need to look up multiple tvg_ids.

        Args:
            epg_source_id: Optional EPG source ID to filter by

        Returns:
            Dict mapping tvg_id -> epg_data dict
        """
        epg_data_list = self.get_epg_data_list(epg_source_id)
        return {e.get('tvg_id'): e for e in epg_data_list if e.get('tvg_id')}

    def upload_logo(self, name: str, url: str) -> Dict[str, Any]:
        """
        Upload a logo to Dispatcharr.

        If the logo URL already exists, finds and returns the existing logo_id.
        Based on channelidentifiarr's logo upload pattern.

        Args:
            name: Logo name (e.g., "Celtics @ Lakers Logo")
            url: URL of the logo image

        Returns:
            Result dict with:
            - success: bool
            - logo_id: int (if successful)
            - error: str (if failed)
            - status: 'created' | 'found_existing' | 'error'
        """
        if not url:
            return {"success": False, "error": "No logo URL provided"}

        # Try to create the logo (optimistic)
        payload = {'name': name, 'url': url}
        response = self.auth.post("/api/channels/logos/", payload)

        # Note: Response objects with non-2xx status codes are falsy in boolean context
        # so we must check explicitly for None
        if response is None:
            return {"success": False, "error": "Request failed - no response", "status": "error"}

        if response.status_code in (200, 201):
            logo_data = response.json()
            return {
                "success": True,
                "logo_id": logo_data.get('id'),
                "status": "created"
            }

        # Check if it's a duplicate collision
        try:
            error_data = response.json()
            error_str = str(error_data).lower()
            if 'already exists' in error_str or 'unique' in error_str:
                # Logo URL already exists - search for it
                existing_logo = self._find_logo_by_url(url)
                if existing_logo:
                    return {
                        "success": True,
                        "logo_id": existing_logo.get('id'),
                        "status": "found_existing"
                    }
        except Exception:
            pass

        return {"success": False, "error": f"HTTP {response.status_code}", "status": "error"}

    def _ensure_logos_cache(self) -> List[Dict]:
        """Ensure logos cache is populated."""
        if self._logos_cache is None:
            self._logos_cache = self._paginated_get(
                "/api/channels/logos/?page_size=500",
                error_context="logos"
            )
            self._logos_by_url = {logo.get('url'): logo for logo in self._logos_cache if logo.get('url')}
            logger.debug(f"Cached {len(self._logos_cache)} logos")
        return self._logos_cache

    def _find_logo_by_url(self, url: str) -> Optional[Dict]:
        """
        Find an existing logo by its URL.

        Uses O(1) lookup via cache index.

        Args:
            url: Logo URL to search for

        Returns:
            Logo dict or None if not found
        """
        self._ensure_logos_cache()
        return self._logos_by_url.get(url)

    def get_logo(self, logo_id: int) -> Optional[Dict]:
        """
        Get logo details by ID.

        Args:
            logo_id: Dispatcharr logo ID

        Returns:
            Logo dict with id, name, url, etc. or None if not found
        """
        if not logo_id:
            return None

        response = self.auth.get(f"/api/channels/logos/{logo_id}/")
        if response and response.status_code == 200:
            return response.json()
        return None

    def delete_logo(self, logo_id: int) -> Dict[str, Any]:
        """
        Delete a logo from Dispatcharr.

        Should be called when deleting channels to prevent logo buildup.
        Only deletes if the logo is not used by any other channels.

        Args:
            logo_id: Dispatcharr logo ID

        Returns:
            Result dict with:
            - success: bool
            - error: str (if failed)
            - status: 'deleted' | 'in_use' | 'not_found' | 'error'
        """
        if not logo_id:
            return {"success": False, "error": "No logo_id provided", "status": "error"}

        # First check if this logo is still used by any other channels
        # Query channels that have this logo_id
        try:
            response = self.auth.get(f"/api/channels/channels/?logo_id={logo_id}")
            if response and response.status_code == 200:
                data = response.json()
                # Handle both list and paginated responses
                channels = data.get('results', data) if isinstance(data, dict) else data
                if isinstance(channels, list) and len(channels) > 0:
                    logger.debug(f"Logo {logo_id} still in use by {len(channels)} channel(s) - keeping")
                    return {
                        "success": True,
                        "status": "in_use",
                        "channel_count": len(channels)
                    }
        except Exception as e:
            logger.warning(f"Could not check logo usage: {e}")
            # Continue with deletion attempt anyway

        # Delete the logo
        response = self.auth.request("DELETE", f"/api/channels/logos/{logo_id}/")

        if response is None:
            return {"success": False, "error": "Request failed - no response", "status": "error"}

        if response.status_code in (200, 204):
            logger.info(f"Deleted logo {logo_id}")
            return {"success": True, "status": "deleted"}

        if response.status_code == 404:
            logger.debug(f"Logo {logo_id} not found (already deleted?)")
            return {"success": True, "status": "not_found"}

        # Check for "in use" errors
        try:
            error_data = response.json()
            error_str = str(error_data).lower()
            if 'in use' in error_str or 'referenced' in error_str or 'channels' in error_str:
                return {"success": True, "status": "in_use"}
        except Exception:
            pass

        return {
            "success": False,
            "error": f"HTTP {response.status_code}",
            "status": "error"
        }

    def test_connection(self) -> Dict[str, Any]:
        """Test connection to Dispatcharr."""
        try:
            if not self.auth.get_token():
                return {"success": False, "message": "Authentication failed"}

            channels = self.get_channels()
            return {
                "success": True,
                "message": f"Connected. Found {len(channels)} channel(s).",
                "channel_count": len(channels)
            }
        except Exception as e:
            return {"success": False, "message": str(e)}

    # ========================================================================
    # Channel Groups Management
    # ========================================================================

    def get_channel_groups(self, exclude_m3u: bool = False) -> List[Dict]:
        """
        Get all channel groups from Dispatcharr.

        Args:
            exclude_m3u: If True, exclude groups originating from M3U accounts

        Returns:
            List of group dicts with id, name, m3u_account_count, channel_count
        """
        response = self.auth.get("/api/channels/groups/")
        if response is None or response.status_code != 200:
            logger.error(f"Failed to get channel groups: {response.status_code if response else 'No response'}")
            return []

        groups = response.json()

        if exclude_m3u:
            # Filter out groups that have M3U accounts associated
            groups = [g for g in groups if not g.get('m3u_account_count', 0)]

        return groups

    def create_channel_group(self, name: str) -> Dict[str, Any]:
        """
        Create a new channel group in Dispatcharr.

        Args:
            name: Group name

        Returns:
            Result dict with:
            - success: bool
            - group: dict (created group data) if successful
            - group_id: int if successful
            - error: str if failed
        """
        if not name or not name.strip():
            return {"success": False, "error": "Group name is required"}

        payload = {'name': name.strip()}
        response = self.auth.post("/api/channels/groups/", payload)

        if response is None:
            return {"success": False, "error": "Request failed - no response"}

        if response.status_code in (200, 201):
            group_data = response.json()
            return {
                "success": True,
                "group": group_data,
                "group_id": group_data.get('id')
            }

        return {"success": False, "error": self._parse_api_error(response)}

    def get_channel_group(self, group_id: int) -> Optional[Dict]:
        """
        Get a single channel group by ID.

        Args:
            group_id: Dispatcharr group ID

        Returns:
            Group dict or None if not found
        """
        response = self.auth.get(f"/api/channels/groups/{group_id}/")
        if response and response.status_code == 200:
            return response.json()
        return None

    def update_channel_group(self, group_id: int, name: str) -> Dict[str, Any]:
        """
        Update a channel group's name.

        Args:
            group_id: Dispatcharr group ID
            name: New group name

        Returns:
            Result dict with success, group, or error
        """
        payload = {'name': name.strip()}
        response = self.auth.request("PATCH", f"/api/channels/groups/{group_id}/", payload)

        if response is None:
            return {"success": False, "error": self._parse_api_error(response)}

        if response.status_code == 200:
            return {"success": True, "group": response.json()}

        return {"success": False, "error": self._parse_api_error(response)}

    def delete_channel_group(self, group_id: int) -> Dict[str, Any]:
        """
        Delete a channel group from Dispatcharr.

        Note: Cannot delete groups that have channels or M3U associations.

        Args:
            group_id: Dispatcharr group ID

        Returns:
            Result dict with success or error
        """
        response = self.auth.request("DELETE", f"/api/channels/groups/{group_id}/")

        if response is None:
            return {"success": False, "error": "Request failed - no response"}

        if response.status_code in (200, 204):
            return {"success": True}

        if response.status_code == 404:
            return {"success": False, "error": "Group not found"}

        # Check for "cannot delete" errors (special case for groups with channels)
        try:
            error_data = response.json()
            error_str = str(error_data).lower()
            if 'cannot delete' in error_str or 'has channels' in error_str:
                return {"success": False, "error": "Cannot delete group with existing channels"}
        except Exception:
            pass

        return {"success": False, "error": self._parse_api_error(response)}

    # ========================================================================
    # Stream Profiles Management
    # ========================================================================

    def get_stream_profiles(self, active_only: bool = False) -> List[Dict]:
        """
        Get all stream profiles from Dispatcharr.

        Stream profiles define how streams are processed (e.g., with yt-dlp, streamlink).

        Args:
            active_only: If True, only return active profiles

        Returns:
            List of profile dicts with id, name, command, parameters, is_active
        """
        response = self.auth.get("/api/core/streamprofiles/")
        if response is None or response.status_code != 200:
            logger.error(f"Failed to get stream profiles: {response.status_code if response else 'No response'}")
            return []

        profiles = response.json()

        if active_only:
            profiles = [p for p in profiles if p.get('is_active', True)]

        return profiles

    def get_stream_profile(self, profile_id: int) -> Optional[Dict]:
        """
        Get a single stream profile by ID.

        Args:
            profile_id: Dispatcharr profile ID

        Returns:
            Profile dict or None if not found
        """
        response = self.auth.get(f"/api/core/streamprofiles/{profile_id}/")
        if response and response.status_code == 200:
            return response.json()
        return None

    def create_stream_profile(
        self,
        name: str,
        command: str = "",
        parameters: str = "",
        is_active: bool = True
    ) -> Dict[str, Any]:
        """
        Create a new stream profile in Dispatcharr.

        Args:
            name: Profile name (e.g., "IPTV Direct")
            command: Command to execute (e.g., "streamlink", "yt-dlp")
            parameters: Command-line parameters with placeholders
            is_active: Whether the profile is active

        Returns:
            Result dict with:
            - success: bool
            - profile: dict (created profile data) if successful
            - profile_id: int if successful
            - error: str if failed
        """
        if not name or not name.strip():
            return {"success": False, "error": "Profile name is required"}

        payload = {
            'name': name.strip(),
            'command': command,
            'parameters': parameters,
            'is_active': is_active
        }

        response = self.auth.post("/api/core/streamprofiles/", payload)

        if response is None:
            return {"success": False, "error": "Request failed - no response"}

        if response.status_code in (200, 201):
            profile_data = response.json()
            return {
                "success": True,
                "profile": profile_data,
                "profile_id": profile_data.get('id')
            }

        return {"success": False, "error": self._parse_api_error(response)}

    def update_stream_profile(self, profile_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update a stream profile.

        Args:
            profile_id: Dispatcharr profile ID
            data: Fields to update (name, command, parameters, is_active)

        Returns:
            Result dict with success, profile, or error
        """
        response = self.auth.request("PATCH", f"/api/core/streamprofiles/{profile_id}/", data)

        if response is None:
            return {"success": False, "error": self._parse_api_error(response)}

        if response.status_code == 200:
            return {"success": True, "profile": response.json()}

        return {"success": False, "error": self._parse_api_error(response)}

    def delete_stream_profile(self, profile_id: int) -> Dict[str, Any]:
        """
        Delete a stream profile from Dispatcharr.

        Note: Cannot delete locked profiles.

        Args:
            profile_id: Dispatcharr profile ID

        Returns:
            Result dict with success or error
        """
        response = self.auth.request("DELETE", f"/api/core/streamprofiles/{profile_id}/")

        if response is None:
            return {"success": False, "error": "Request failed - no response"}

        if response.status_code in (200, 204):
            return {"success": True}

        if response.status_code == 404:
            return {"success": False, "error": "Profile not found"}

        return {"success": False, "error": self._parse_api_error(response)}

    # ========================================================================
    # Channel Profiles Management
    # ========================================================================

    def get_channel_profiles(self) -> List[Dict]:
        """
        Get all channel profiles from Dispatcharr.

        Channel profiles group channels together for organization/filtering.

        Returns:
            List of profile dicts with id, name, channels (list of channel IDs)
        """
        response = self.auth.get("/api/channels/profiles/")
        if response is None or response.status_code != 200:
            logger.error(f"Failed to get channel profiles: {response.status_code if response else 'No response'}")
            return []

        return response.json()

    def get_channel_profile(self, profile_id: int) -> Optional[Dict]:
        """
        Get a single channel profile by ID.

        Args:
            profile_id: Dispatcharr profile ID

        Returns:
            Profile dict or None if not found
        """
        response = self.auth.get(f"/api/channels/profiles/{profile_id}/")
        if response and response.status_code == 200:
            return response.json()
        return None

    def create_channel_profile(self, name: str) -> Dict[str, Any]:
        """
        Create a new channel profile in Dispatcharr.

        Args:
            name: Profile name (required, max 100 chars)

        Returns:
            Result dict with:
            - success: bool
            - profile: dict (created profile data) if successful
            - profile_id: int if successful
            - error: str if failed
        """
        if not name or not name.strip():
            return {"success": False, "error": "Profile name is required"}

        payload = {'name': name.strip()}

        response = self.auth.post("/api/channels/profiles/", payload)

        if response is None:
            return {"success": False, "error": "Request failed - no response"}

        if response.status_code in (200, 201):
            profile_data = response.json()
            return {
                "success": True,
                "profile": profile_data,
                "profile_id": profile_data.get('id')
            }

        return {"success": False, "error": self._parse_api_error(response)}

    def add_channel_to_profile(self, profile_id: int, channel_id: int) -> Dict[str, Any]:
        """
        Add a channel to a channel profile.

        Uses the per-channel endpoint to enable the channel in the profile.

        Args:
            profile_id: Dispatcharr channel profile ID
            channel_id: Dispatcharr channel ID to add

        Returns:
            Result dict with success or error
        """
        response = self.auth.request(
            "PATCH",
            f"/api/channels/profiles/{profile_id}/channels/{channel_id}/",
            {'enabled': True}
        )

        if response and response.status_code == 200:
            return {"success": True}

        return {"success": False, "error": self._parse_api_error(response)}

    def remove_channel_from_profile(self, profile_id: int, channel_id: int) -> Dict[str, Any]:
        """
        Remove a channel from a channel profile.

        Uses the per-channel endpoint to disable the channel in the profile.

        Args:
            profile_id: Dispatcharr channel profile ID
            channel_id: Dispatcharr channel ID to remove

        Returns:
            Result dict with success or error
        """
        response = self.auth.request(
            "PATCH",
            f"/api/channels/profiles/{profile_id}/channels/{channel_id}/",
            {'enabled': False}
        )

        if response and response.status_code == 200:
            return {"success": True}

        return {"success": False, "error": self._parse_api_error(response)}
