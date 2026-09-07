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
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import httpx

from teamarr.config.runtime import dry_run
from teamarr.dispatcharr.auth import TokenManager

logger = logging.getLogger(__name__)

# Retryable HTTP status codes (server-side transient errors)
RETRYABLE_STATUS_CODES = {502, 503, 504}

# Concurrency for page-numbered pagination (see paginated_get). Matches the M3U
# stream lister's bound (#610) — Dispatcharr is one Django app, often on the
# same host, so this is about overlapping latency, not saturating it.
_MAX_PAGE_WORKERS = 8

# Connection-pool size. Keepalive is held equal to it on purpose — see
# DispatcharrClient._get_client.
_POOL_MAX_CONNECTIONS = 100


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


def _next_path(data: dict) -> str | None:
    """Path+query of a paginated response's ``next`` link, if any.

    Dispatcharr sometimes answers with an absolute URL and sometimes a path;
    the client only ever speaks in paths, so absolutes are reduced.
    """
    next_url = data.get("next")
    if not next_url:
        return None
    if str(next_url).startswith("http"):
        parsed = urlparse(next_url)
        return f"{parsed.path}?{parsed.query}" if parsed.query else parsed.path
    return next_url


def _page_url(endpoint: str, page: int) -> str:
    """``endpoint`` with its ``page`` parameter replaced by ``page``."""
    base, _, query = endpoint.partition("?")
    params = [p for p in query.split("&") if p and not p.startswith("page=")]
    params.insert(0, f"page={page}")
    return f"{base}?{'&'.join(params)}"


def _page_count(first: dict, endpoint: str) -> int | None:
    """Total pages implied by a first page's ``count``, or None if unknowable.

    Needs both an integer ``count`` and the page size actually in effect. The
    endpoint's own ``page_size`` is not evidence of that — /api/epg/epgdata/
    ignores it entirely — so the length of page 1 is what we measure, and a
    first page shorter than ``count`` but not evenly divisible is left to the
    serial `next` walk rather than guessed at.
    """
    count = first.get("count")
    results = first.get("results")
    if not isinstance(count, int) or not isinstance(results, list):
        return None
    page_size = len(results)
    if page_size <= 0:
        return None
    if page_size >= count:
        return 1
    return -(-count // page_size)


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
        self._client_lock = threading.Lock()

    def _get_client(self) -> httpx.Client:
        """Get or create the pooled HTTP client (thread-safe).

        Double-checked locking, matching ``providers/base_client.py``: the
        generation run drives this client from thread pools, and an unguarded
        lazy init lets two threads each build a client, one of which is then
        dropped on the floor with its connections still open.

        Keepalive matches max_connections deliberately. Capping it lower means
        that past that many concurrent requests every extra one closes its
        connection on completion and the next pays a fresh TCP+TLS handshake —
        which is exactly the regime the parallel pushes put this client in.
        """
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    self._client = httpx.Client(
                        timeout=self._timeout,
                        limits=httpx.Limits(
                            max_connections=_POOL_MAX_CONNECTIONS,
                            max_keepalive_connections=_POOL_MAX_CONNECTIONS,
                        ),
                    )
        return self._client

    def request(
        self,
        method: str,
        endpoint: str,
        data: dict | None = None,
        retry_on_401: bool = True,
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
        if method.upper() != "GET" and dry_run():
            # DRY_RUN (#554): every Dispatcharr mutation funnels through here.
            # Log what would have happened and report "no response" so callers
            # take their failure path — nothing is persisted against a fake id.
            logger.info(
                "[DRY_RUN] Suppressed %s %s%s",
                method.upper(),
                endpoint,
                f" payload={data}" if data is not None else "",
            )
            return None

        token = self._auth.get_token()
        if not token:
            logger.error("[DISPATCHARR] Failed to obtain authentication token")
            return None

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        full_url = f"{self._base_url}{endpoint}"
        client = self._get_client()
        last_exception: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                if method.upper() == "GET":
                    response = client.get(full_url, headers=headers)
                elif method.upper() == "POST":
                    response = client.post(full_url, headers=headers, json=data)
                elif method.upper() == "PATCH":
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
        def fetch(url: str) -> dict | list | None:
            """One page. ``None`` means the request failed — never an empty page."""
            response = self.get(url)
            if response is None or response.status_code != 200:
                status = response.status_code if response else "No response"
                logger.error("[DISPATCHARR] Failed to get %s: %s", error_context, status)
                return None
            return response.json()

        def follow(data: dict, into: list[dict]) -> None:
            """Walk `next` links from an already-fetched page, appending."""
            next_page = _next_path(data)
            while next_page:
                page = fetch(next_page)
                if page is None:
                    return
                if not isinstance(page, dict):
                    into.extend(page)
                    return
                into.extend(page.get("results", []))
                next_page = _next_path(page)

        first = fetch(initial_endpoint)
        if first is None:
            return []

        # A non-paginated endpoint answers with the whole collection at once
        # (/api/epg/epgdata/ does exactly this, page_size and all).
        if isinstance(first, list):
            return list(first)
        if "results" not in first:
            return []

        all_items: list[dict] = list(first.get("results", []))
        pages = _page_count(first, initial_endpoint)

        if pages is None or pages <= 1:
            # No usable count, or nothing beyond page 1: walk `next` as before.
            follow(first, all_items)
            return all_items

        # Pages 2..N are independently addressable, so fetch them at once rather
        # than paying a round trip each (#735; same shape as m3u.list_streams,
        # #610). Order is restored by page number afterwards, because callers
        # like get_channel_maps build last-write-wins maps whose result depends
        # on it.
        by_page: dict[int, list[dict]] = {}
        last = first
        with ThreadPoolExecutor(
            max_workers=min(_MAX_PAGE_WORKERS, pages - 1),
            thread_name_prefix="dispatcharr-page",
        ) as executor:
            futures = {
                executor.submit(fetch, _page_url(initial_endpoint, n)): n
                for n in range(2, pages + 1)
            }
            for future in as_completed(futures):
                page_no = futures[future]
                try:
                    data = future.result()
                except Exception as e:  # noqa: BLE001 - treated as a failed page
                    logger.error("[DISPATCHARR] %s page %d failed: %s", error_context, page_no, e)
                    data = None
                if data is None:
                    continue
                by_page[page_no] = data.get("results", []) if isinstance(data, dict) else data
                if isinstance(data, dict) and page_no == pages:
                    last = data

        # The serial walk this replaces returned a contiguous PREFIX when a page
        # failed — it broke out of the loop — so that is the contract callers
        # were written against. Concurrency makes a hole possible instead (a
        # later page can land while an earlier one fails), which no caller can
        # distinguish from a genuinely smaller collection, so stop at the first
        # page that did not arrive and drop everything after it. Every page is
        # awaited before assembling: the requests were already in flight, and
        # bailing early is what lets a hole through.
        for page_no in range(2, pages + 1):
            page_items = by_page.get(page_no)
            if page_items is None:
                logger.error(
                    "[DISPATCHARR] Truncating %s at page %d of %d (page failed)",
                    error_context, page_no, pages,
                )
                return all_items
            all_items.extend(page_items)

        # The collection can grow between page 1 and the last page; whatever
        # `next` still points at is picked up here.
        follow(last, all_items)
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
            if dry_run():
                return "Dry run — write suppressed (DRY_RUN=true)"
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

    def get_stream_stats_by_ids(self, stream_ids: list[int]) -> list[dict]:
        """Fetch stream_stats for a batch of streams by their Dispatcharr stream IDs.

        Uses POST /api/channels/streams/by-ids/ which works for any stream
        regardless of channel assignment. Returns only the fields needed for
        stats caching; non-existent IDs are silently omitted by Dispatcharr.

        Args:
            stream_ids: List of Dispatcharr stream IDs to fetch stats for

        Returns:
            List of dicts with keys: id, stream_stats, stream_stats_updated_at
        """
        if not stream_ids:
            return []
        response = self.post(
            "/api/channels/streams/by-ids/?page_size=1000",
            {"ids": stream_ids},
        )
        if response is None or response.status_code != 200:
            status = response.status_code if response else "no response"
            logger.warning(
                "[STREAM STATS] Failed to fetch stats for %d streams: %s", len(stream_ids), status
            )
            return []
        data = response.json()
        results = data.get("results", data) if isinstance(data, dict) else data
        return [
            {
                "id": s["id"],
                "stream_stats": s.get("stream_stats"),
                "stream_stats_updated_at": s.get("stream_stats_updated_at"),
            }
            for s in results
            if "id" in s
        ]

    def close(self) -> None:
        """Close HTTP client.

        Auth tokens are shared across DispatcharrClient instances by TokenManager.
        Closing a short-lived client must not clear the shared token because
        concurrent API requests may be using it.
        """
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self) -> "DispatcharrClient":
        """Context manager entry."""
        return self

    def __exit__(self, *args) -> None:
        """Context manager exit."""
        self.close()
