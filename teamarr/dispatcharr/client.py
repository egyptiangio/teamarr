"""Base HTTP client for Dispatcharr API.

Provides authenticated HTTP requests with automatic retry logic
using exponential backoff with jitter.

Retry Strategy:
- Exponential backoff: 1s, 2s, 4s, 8s, 16s, 32s (capped)
- Jitter: ±50% randomization to prevent thundering herd
- Max retries: 5 (configurable)
- Retryable: ConnectionError, Timeout, 502, 503, 504
"""

import logging
import random
import time
from urllib.parse import urlparse

import httpx

from teamarr.dispatcharr.auth import TokenManager

logger = logging.getLogger(__name__)

# Retryable HTTP status codes (server-side transient errors)
RETRYABLE_STATUS_CODES = {502, 503, 504}


def _calculate_backoff(
    attempt: int,
    base_delay: float = 1.0,
    max_delay: float = 32.0,
) -> float:
    """Calculate delay with exponential backoff and jitter.

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
    delay = min(max_delay, base_delay * (2**attempt))
    # Add jitter: ±50%
    jitter = random.uniform(0.5, 1.5)
    return delay * jitter


class DispatcharrClient:
    """Low-level HTTP client for Dispatcharr API.

    Provides authenticated requests with automatic retry logic.

    Features:
    - JWT authentication via TokenManager
    - Exponential backoff with jitter for transient errors
    - Automatic re-authentication on 401
    - Connection pooling via httpx
    - Context manager support

    Usage:
        with DispatcharrClient("http://localhost:9191", "admin", "pass") as client:
            response = client.get("/api/epg/sources/")
            if response:
                sources = response.json()
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        timeout: float = 30.0,
        max_retries: int = 5,
    ):
        """Initialize Dispatcharr client.

        Args:
            base_url: Base URL of Dispatcharr instance
            username: Dispatcharr username
            password: Dispatcharr password
            timeout: Request timeout in seconds (default: 30.0)
            max_retries: Maximum retry attempts for transient errors (default: 5)
        """
        self._base_url = base_url.rstrip("/")
        self._auth = TokenManager(base_url, username, password, timeout)
        self._timeout = timeout
        self._max_retries = max_retries
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        """Get or create HTTP client with connection pooling."""
        if self._client is None:
            self._client = httpx.Client(
                timeout=self._timeout,
                limits=httpx.Limits(
                    max_connections=100,
                    max_keepalive_connections=20,
                ),
            )
        return self._client

    def request(
        self,
        method: str,
        endpoint: str,
        data: dict | None = None,
        retry_on_401: bool = True,
        as_form: bool = False,
    ) -> httpx.Response | None:
        """Make an authenticated request with retry logic.

        Args:
            method: HTTP method (GET, POST, PATCH, DELETE)
            endpoint: API endpoint (e.g., "/api/epg/sources/")
            data: JSON data for POST/PATCH requests
            retry_on_401: Whether to retry with fresh token on 401

        Returns:
            Response object or None if request fails after all retries
        """
        token = self._auth.get_token()
        if not token:
            logger.error("[DISPATCHARR] Failed to obtain authentication token")
            return None

        headers = {
            "Authorization": f"Bearer {token}",
        }
        if not as_form:
            headers["Content-Type"] = "application/json"

        full_url = f"{self._base_url}{endpoint}"
        client = self._get_client()
        last_exception: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                if method.upper() == "GET":
                    response = client.get(full_url, headers=headers)
                elif method.upper() == "POST":
                    if as_form:
                        response = client.post(full_url, headers=headers, data=data)
                    else:
                        response = client.post(full_url, headers=headers, json=data)
                elif method.upper() == "PATCH":
                    if as_form:
                        response = client.patch(full_url, headers=headers, data=data)
                    else:
                        response = client.patch(full_url, headers=headers, json=data)
                elif method.upper() == "DELETE":
                    response = client.delete(full_url, headers=headers)
                else:
                    logger.error("[DISPATCHARR] Unsupported HTTP method: %s", method)
                    return None

                # Handle 401 with re-authentication (not counted as retry)
                if response.status_code == 401 and retry_on_401:
                    logger.debug("[DISPATCHARR] Received 401, clearing session and retrying...")
                    self._auth.clear()
                    return self.request(method, endpoint, data, retry_on_401=False)

                # Check for retryable HTTP status codes
                if response.status_code in RETRYABLE_STATUS_CODES:
                    if attempt < self._max_retries:
                        delay = _calculate_backoff(attempt)
                        logger.warning(
                            "[DISPATCHARR] Retryable HTTP %d for %s %s, retry %d/%d after %.1fs",
                            response.status_code,
                            method,
                            endpoint,
                            attempt + 1,
                            self._max_retries,
                            delay,
                        )
                        time.sleep(delay)
                        continue
                    else:
                        logger.error(
                            "[DISPATCHARR] Max retries exceeded for %s %s (HTTP %d)",
                            method,
                            endpoint,
                            response.status_code,
                        )

                return response

            except (httpx.ConnectError, httpx.TimeoutException) as e:
                last_exception = e

                if attempt < self._max_retries:
                    delay = _calculate_backoff(attempt)
                    logger.warning(
                        "[DISPATCHARR] Retryable error for %s %s: %s, retry %d/%d after %.1fs",
                        method,
                        endpoint,
                        type(e).__name__,
                        attempt + 1,
                        self._max_retries,
                        delay,
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        "[DISPATCHARR] Max retries exceeded for %s %s: %s", method, endpoint, e
                    )

            except httpx.RequestError as e:
                # Non-retryable request exception
                logger.error("[DISPATCHARR] Request failed (non-retryable): %s", e)
                return None

        # All retries exhausted
        if last_exception:
            logger.error(
                "[DISPATCHARR] Request failed after %d retries: %s",
                self._max_retries,
                last_exception,
            )
        return None

    def get(self, endpoint: str) -> httpx.Response | None:
        """Make authenticated GET request."""
        return self.request("GET", endpoint)

    def post(self, endpoint: str, data: dict | None = None) -> httpx.Response | None:
        """Make authenticated POST request."""
        return self.request("POST", endpoint, data)

    def post_form(self, endpoint: str, data: dict | None = None) -> httpx.Response | None:
        """Make authenticated POST request with form-encoded data."""
        return self.request("POST", endpoint, data, as_form=True)

    def patch(self, endpoint: str, data: dict) -> httpx.Response | None:
        """Make authenticated PATCH request."""
        return self.request("PATCH", endpoint, data)

    def delete(self, endpoint: str) -> httpx.Response | None:
        """Make authenticated DELETE request."""
        return self.request("DELETE", endpoint)

    def paginated_get(
        self,
        initial_endpoint: str,
        error_context: str = "items",
    ) -> list[dict]:
        """Fetch all items from a paginated API endpoint.

        Handles both paginated dict responses (with 'results' and 'next')
        and simple list responses.

        Args:
            initial_endpoint: Starting endpoint with page_size
                (e.g., "/api/channels/channels/?page_size=1000")
            error_context: Context for error logging (e.g., "channels")

        Returns:
            List of all items from all pages
        """
        all_items: list[dict] = []
        next_page: str | None = initial_endpoint

        while next_page:
            response = self.get(next_page)
            if response is None or response.status_code != 200:
                status = response.status_code if response else "No response"
                logger.error("[DISPATCHARR] Failed to get %s: %s", error_context, status)
                break

            data = response.json()

            if isinstance(data, dict) and "results" in data:
                all_items.extend(data["results"])
                next_url = data.get("next")
                if next_url:
                    # Handle absolute URLs by extracting path+query
                    if next_url.startswith("http"):
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

    def parse_api_error(self, response: httpx.Response | None) -> str:
        """Parse error message from API response.

        Handles various error response formats from Dispatcharr API.

        Args:
            response: httpx Response object or None

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

    def test_connection(self) -> dict:
        """Test connection to Dispatcharr.

        Returns:
            Dict with success (bool), message (str), and optionally error details
        """
        try:
            token = self._auth.get_token()
            if not token:
                return {
                    "success": False,
                    "message": "Authentication failed - check credentials",
                }

            response = self.get("/api/epg/sources/")
            if response and response.status_code == 200:
                sources = response.json()
                return {
                    "success": True,
                    "message": f"Connected successfully. Found {len(sources)} EPG source(s).",
                    "sources": sources,
                }

            status = response.status_code if response else "no response"
            return {
                "success": False,
                "message": f"Connection failed: HTTP {status}",
            }

        except httpx.ConnectError:
            return {
                "success": False,
                "message": "Connection failed - check URL and ensure Dispatcharr is running",
            }
        except httpx.TimeoutException:
            return {
                "success": False,
                "message": "Connection timed out",
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Error: {e!s}",
            }

    def close(self) -> None:
        """Close HTTP client and clear auth."""
        if self._client:
            self._client.close()
            self._client = None
        self._auth.clear()

    def __enter__(self) -> "DispatcharrClient":
        """Context manager entry."""
        return self

    def __exit__(self, *args) -> None:
        """Context manager exit."""
        self.close()
