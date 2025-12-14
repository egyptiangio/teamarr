# Dispatcharr API Integration Guide

This document provides comprehensive documentation for integrating with Dispatcharr's API, including authentication, EPG management, and best practices for implementing a Just-In-Time (JIT) authentication system.

---

## Table of Contents

1. [Overview](#overview)
2. [Authentication](#authentication)
   - [JWT Token Flow](#jwt-token-flow)
   - [JIT Authentication Implementation](#jit-authentication-implementation)
3. [API Endpoints](#api-endpoints)
   - [Authentication Endpoints](#authentication-endpoints)
   - [EPG Endpoints](#epg-endpoints)
4. [EPG Refresh Functionality](#epg-refresh-functionality)
5. [Code Examples](#code-examples)
   - [Basic Authentication](#basic-authentication)
   - [Complete JIT Auth Class](#complete-jit-auth-class)
   - [EPG Management Functions](#epg-management-functions)
6. [Best Practices](#best-practices)
7. [Error Handling](#error-handling)

---

## Overview

Dispatcharr is an IPTV/DVR management application that provides REST APIs for managing channels, EPG (Electronic Program Guide) data, M3U sources, and more. This guide focuses on:

- **Authentication**: JWT-based Bearer token authentication
- **EPG Management**: Listing sources, triggering refreshes
- **Integration Patterns**: Best practices for external service integration

### Base Configuration

| Setting | Value |
|---------|-------|
| Default Port | 9191 |
| API Base Path | `/api/` |
| Auth Type | JWT Bearer Token |
| Token Validity | ~5 minutes |

---

## Authentication

### JWT Token Flow

Dispatcharr uses JSON Web Tokens (JWT) for API authentication. The flow consists of:

1. **Initial Login**: Exchange credentials for access + refresh tokens
2. **API Requests**: Include access token in `Authorization: Bearer <token>` header
3. **Token Refresh**: Use refresh token to get new access token before expiry
4. **Re-authentication**: Full login when refresh token expires

```
┌─────────────┐     POST /api/accounts/token/      ┌─────────────┐
│   Client    │ ─────────────────────────────────► │ Dispatcharr │
│             │  {username, password}              │             │
│             │ ◄───────────────────────────────── │             │
│             │  {access, refresh}                 │             │
│             │                                    │             │
│             │     GET /api/epg/sources/          │             │
│             │ ─────────────────────────────────► │             │
│             │  Authorization: Bearer <access>    │             │
│             │ ◄───────────────────────────────── │             │
│             │  [EPG sources list]                │             │
│             │                                    │             │
│  (on 401)   │  POST /api/accounts/token/refresh/ │             │
│             │ ─────────────────────────────────► │             │
│             │  {refresh: <refresh_token>}        │             │
│             │ ◄───────────────────────────────── │             │
│             │  {access: <new_token>}             │             │
└─────────────┘                                    └─────────────┘
```

### JIT Authentication Implementation

Just-In-Time (JIT) authentication ensures tokens are always valid by:

1. Caching tokens per session (keyed by `{url}_{username}`)
2. Checking token expiry before each request
3. Proactively refreshing tokens before they expire
4. Automatically re-authenticating on 401 responses
5. Supporting multiple Dispatcharr instances simultaneously

**Key Design Principles:**

- **Lazy Authentication**: Only authenticate when actually needed
- **Token Caching**: Avoid redundant auth requests
- **Proactive Refresh**: Refresh at 4 minutes (before 5-minute expiry)
- **Graceful Fallback**: Full re-auth if refresh fails
- **Thread Safety**: Session storage should be thread-safe in production

---

## API Endpoints

### Authentication Endpoints

#### Login (Get Tokens)

```
POST /api/accounts/token/
Content-Type: application/json

{
  "username": "admin",
  "password": "your_password"
}
```

**Response (200 OK):**
```json
{
  "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

**Errors:**
- `401`: Invalid credentials
- `403`: Account disabled or forbidden

#### Refresh Token

```
POST /api/accounts/token/refresh/
Content-Type: application/json

{
  "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

**Response (200 OK):**
```json
{
  "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

### EPG Endpoints

#### List EPG Sources

```
GET /api/epg/sources/
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
[
  {
    "id": 21,
    "name": "_Teamarr - Detroit Sports",
    "source_type": "url",
    "url": "http://example.com/epg.xml",
    "is_active": true,
    "last_refresh": "2025-11-24T19:02:30Z"
  },
  {
    "id": 10,
    "name": "_Dummy - All Sports",
    "source_type": "dummy",
    "is_active": true
  }
]
```

**Fields:**
| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Unique EPG source identifier |
| `name` | string | Display name |
| `source_type` | string | `url`, `file`, or `dummy` |
| `url` | string | EPG XML URL (if type=url) |
| `is_active` | bool | Whether source is enabled |
| `last_refresh` | datetime | Last successful refresh time |

#### Trigger EPG Refresh

```
POST /api/epg/import/
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "id": 21
}
```

**Response (202 Accepted):**
```json
{
  "success": true,
  "message": "EPG data import initiated."
}
```

**Errors:**
- `400`: Dummy EPG source (doesn't need refresh)
- `401`: Invalid/expired token
- `404`: EPG source not found

**Note:** This endpoint triggers an async Celery task. The response confirms the task was queued, not that it completed. Use the `wait_for_refresh()` method to poll until completion (see below).

#### Get EPG Data

```
GET /api/epg/epgdata/
Authorization: Bearer <access_token>
```

Returns parsed EPG program data.

#### Get EPG Grid

```
GET /api/epg/grid/
Authorization: Bearer <access_token>
```

Returns EPG data formatted for grid display.

---

## EPG Refresh Functionality

### Single EPG Refresh

To refresh a specific EPG source by ID:

```python
def refresh_epg(epg_id: int) -> bool:
    """Refresh a single EPG source"""
    response = dispatcharr_request("POST", "/api/epg/import/", {"id": epg_id})
    return response.status_code == 202
```

### Multiple EPG Refresh

The API doesn't natively support batch refresh. Iterate over IDs:

```python
def refresh_multiple_epgs(epg_ids: list[int]) -> dict:
    """Refresh multiple EPG sources"""
    results = {}
    for epg_id in epg_ids:
        response = dispatcharr_request("POST", "/api/epg/import/", {"id": epg_id})
        results[epg_id] = {
            "success": response.status_code == 202,
            "message": response.json().get("message", "Unknown error")
        }
    return results
```

**Note:** Celery tasks run asynchronously, so multiple refresh requests will process in parallel on the Dispatcharr side.

### Waiting for EPG Refresh Completion

Since EPG refresh is asynchronous, you may need to wait for completion before proceeding (e.g., before associating EPG data with channels). The `wait_for_refresh()` method polls the EPG source status until it transitions to `success` or `error`.

**EPG Source Status Values:**

| Status | Meaning |
|--------|---------|
| `idle` | Not currently refreshing |
| `fetching` | Downloading EPG data from URL |
| `parsing` | Processing downloaded EPG XML |
| `success` | Refresh completed successfully |
| `error` | Refresh failed |
| `disabled` | Source is disabled |

**Implementation in Teamarr:**

```python
def wait_for_refresh(
    self,
    epg_id: int,
    timeout: int = 60,
    poll_interval: int = 2
) -> Dict[str, Any]:
    """
    Trigger EPG refresh and wait for completion by polling status/updated_at.

    Args:
        epg_id: EPG source ID to refresh
        timeout: Maximum seconds to wait (default: 60)
        poll_interval: Seconds between status checks (default: 2)

    Returns:
        Dict with success, duration, final_status, message
    """
    # Get initial state before refresh
    source = self.get_source(epg_id)
    initial_updated_at = source.get('updated_at') if source else None

    # Trigger the refresh
    refresh_result = self.refresh(epg_id)
    if not refresh_result.get('success'):
        return refresh_result

    # Poll until completion
    start_time = time.time()
    while time.time() - start_time < timeout:
        time.sleep(poll_interval)
        source = self.get_source(epg_id)
        if not source:
            continue

        status = source.get('status', '')
        updated_at = source.get('updated_at')

        # Check if status indicates completion
        if status in ('success', 'error'):
            # Verify updated_at changed (confirms this refresh, not a previous one)
            if updated_at and updated_at != initial_updated_at:
                duration = time.time() - start_time
                return {
                    'success': status == 'success',
                    'duration': duration,
                    'final_status': status,
                    'message': f'EPG refresh {status} in {duration:.1f}s'
                }

    return {
        'success': False,
        'duration': timeout,
        'final_status': 'timeout',
        'message': f'EPG refresh timed out after {timeout}s'
    }
```

**Typical refresh duration:** ~14 seconds for a standard EPG source.

### EPG Source Types

| Type | Description | Refreshable |
|------|-------------|-------------|
| `url` | Remote XML URL | Yes |
| `file` | Local XML file | Yes |
| `dummy` | Placeholder/manual | No (returns 400) |

---

## Code Examples

### Basic Authentication

```python
import requests

def get_token(url: str, username: str, password: str) -> str | None:
    """Get access token from Dispatcharr"""
    response = requests.post(
        f"{url}/api/accounts/token/",
        json={"username": username, "password": password},
        timeout=10
    )
    if response.status_code == 200:
        return response.json().get("access")
    return None

# Usage
token = get_token("http://localhost:9191", "admin", "password")
headers = {"Authorization": f"Bearer {token}"}
```

### Complete JIT Auth Class

```python
"""
Dispatcharr JIT Authentication Module

Provides just-in-time authentication with automatic token refresh
and session management for Dispatcharr API integration.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Any
import requests

logger = logging.getLogger(__name__)


class DispatcharrAuth:
    """
    Just-In-Time authentication handler for Dispatcharr API.

    Features:
    - Automatic token caching per URL/username combination
    - Proactive token refresh before expiry
    - Automatic re-authentication on token failure
    - Thread-safe session management (use with locking in production)

    Usage:
        auth = DispatcharrAuth("http://localhost:9191", "admin", "password")

        # Get token (authenticates if needed)
        token = auth.get_token()

        # Make authenticated request
        response = auth.request("GET", "/api/epg/sources/")

        # Or use the convenience methods
        sources = auth.get("/api/epg/sources/")
        result = auth.post("/api/epg/import/", {"id": 21})
    """

    # Class-level session storage for multi-instance support
    _sessions: dict[str, dict] = {}

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

        # Initialize session if not exists
        if self._session_key not in self._sessions:
            self._sessions[self._session_key] = {
                "access_token": None,
                "refresh_token": None,
                "token_expiry": None
            }

    @property
    def _session(self) -> dict:
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
            response = requests.post(
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

            response = requests.post(
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
                logger.error(f"Authentication failed: Invalid credentials")
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

        This is the main JIT authentication method. It will:
        1. Return cached token if still valid
        2. Refresh token if refresh token available
        3. Perform full authentication if needed

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
        data: Optional[dict] = None,
        retry_on_401: bool = True
    ) -> Optional[requests.Response]:
        """
        Make an authenticated request to Dispatcharr API.

        Args:
            method: HTTP method (GET, POST, PATCH, DELETE)
            endpoint: API endpoint (e.g., "/api/epg/sources/")
            data: JSON data for POST/PATCH requests
            retry_on_401: Whether to retry with fresh token on 401

        Returns:
            Response object or None if request fails
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

        try:
            if method.upper() == "GET":
                response = requests.get(full_url, headers=headers, timeout=self.timeout)
            elif method.upper() == "POST":
                response = requests.post(full_url, headers=headers, json=data, timeout=self.timeout)
            elif method.upper() == "PATCH":
                response = requests.patch(full_url, headers=headers, json=data, timeout=self.timeout)
            elif method.upper() == "DELETE":
                response = requests.delete(full_url, headers=headers, timeout=self.timeout)
            else:
                logger.error(f"Unsupported HTTP method: {method}")
                return None

            # Handle 401 with retry
            if response.status_code == 401 and retry_on_401:
                logger.info("Received 401, clearing session and retrying...")
                self.clear_session()
                return self.request(method, endpoint, data, retry_on_401=False)

            return response

        except requests.RequestException as e:
            logger.error(f"Request failed: {e}")
            return None

    # Convenience methods
    def get(self, endpoint: str) -> Optional[requests.Response]:
        """Make authenticated GET request"""
        return self.request("GET", endpoint)

    def post(self, endpoint: str, data: dict = None) -> Optional[requests.Response]:
        """Make authenticated POST request"""
        return self.request("POST", endpoint, data)

    def patch(self, endpoint: str, data: dict = None) -> Optional[requests.Response]:
        """Make authenticated PATCH request"""
        return self.request("PATCH", endpoint, data)

    def delete(self, endpoint: str) -> Optional[requests.Response]:
        """Make authenticated DELETE request"""
        return self.request("DELETE", endpoint)


# Convenience function for simple use cases
def create_dispatcharr_client(
    url: str = None,
    username: str = None,
    password: str = None,
    settings: dict = None
) -> DispatcharrAuth:
    """
    Create a Dispatcharr client from explicit credentials or settings dict.

    Args:
        url: Dispatcharr URL (or from settings["dispatcharr"]["url"])
        username: Username (or from settings["dispatcharr"]["username"])
        password: Password (or from settings["dispatcharr"]["password"])
        settings: Settings dict with "dispatcharr" key

    Returns:
        Configured DispatcharrAuth instance
    """
    if settings and "dispatcharr" in settings:
        url = url or settings["dispatcharr"].get("url")
        username = username or settings["dispatcharr"].get("username")
        password = password or settings["dispatcharr"].get("password")

    if not all([url, username, password]):
        raise ValueError("Missing required Dispatcharr credentials")

    return DispatcharrAuth(url, username, password)
```

### EPG Management Functions

```python
"""
EPG Management Functions for Dispatcharr Integration
"""

from typing import Optional
from dispatcharr_auth import DispatcharrAuth


class EPGManager:
    """
    High-level EPG management interface for Dispatcharr.

    Usage:
        manager = EPGManager("http://localhost:9191", "admin", "password")

        # List all EPG sources
        sources = manager.list_sources()
        for src in sources:
            print(f"{src['id']}: {src['name']}")

        # Refresh specific EPG
        manager.refresh(21)

        # Refresh multiple EPGs
        manager.refresh_multiple([16, 17, 21])

        # Find EPG by name
        epg = manager.find_by_name("Teamarr")
        if epg:
            manager.refresh(epg["id"])
    """

    def __init__(self, url: str, username: str, password: str):
        self.auth = DispatcharrAuth(url, username, password)

    def list_sources(self, include_dummy: bool = True) -> list[dict]:
        """
        List all EPG sources.

        Args:
            include_dummy: Whether to include dummy sources (default: True)

        Returns:
            List of EPG source dictionaries
        """
        response = self.auth.get("/api/epg/sources/")
        if not response or response.status_code != 200:
            return []

        sources = response.json()

        if not include_dummy:
            sources = [s for s in sources if s.get("source_type") != "dummy"]

        return sources

    def get_source(self, epg_id: int) -> Optional[dict]:
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

    def find_by_name(self, name: str, exact: bool = False) -> Optional[dict]:
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

    def refresh(self, epg_id: int) -> dict:
        """
        Trigger refresh for a single EPG source.

        Args:
            epg_id: EPG source ID to refresh

        Returns:
            Result dict with "success" and "message" keys
        """
        response = self.auth.post("/api/epg/import/", {"id": epg_id})

        if not response:
            return {"success": False, "message": "Request failed"}

        if response.status_code == 202:
            return {"success": True, "message": "EPG refresh initiated"}
        elif response.status_code == 400:
            return {"success": False, "message": "Cannot refresh dummy EPG source"}
        else:
            return {
                "success": False,
                "message": response.json().get("message", f"HTTP {response.status_code}")
            }

    def refresh_multiple(self, epg_ids: list[int]) -> dict[int, dict]:
        """
        Trigger refresh for multiple EPG sources.

        Args:
            epg_ids: List of EPG source IDs to refresh

        Returns:
            Dict mapping EPG ID to result dict
        """
        results = {}
        for epg_id in epg_ids:
            results[epg_id] = self.refresh(epg_id)
        return results

    def refresh_all(self, skip_dummy: bool = True) -> dict[int, dict]:
        """
        Refresh all EPG sources.

        Args:
            skip_dummy: Skip dummy sources (default: True)

        Returns:
            Dict mapping EPG ID to result dict
        """
        sources = self.list_sources(include_dummy=not skip_dummy)
        epg_ids = [s["id"] for s in sources]
        return self.refresh_multiple(epg_ids)

    def refresh_by_name(self, name: str) -> dict:
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


# Example usage
if __name__ == "__main__":
    manager = EPGManager(
        url="http://localhost:9191",
        username="admin",
        password="your_password"
    )

    # List all sources
    print("EPG Sources:")
    for src in manager.list_sources():
        stype = src.get("source_type", "unknown")
        skip = " (dummy)" if stype == "dummy" else ""
        print(f"  {src['id']:3}: {src['name']}{skip}")

    # Refresh by ID
    result = manager.refresh(21)
    print(f"\nRefresh EPG #21: {result}")

    # Refresh by name
    result = manager.refresh_by_name("Teamarr")
    print(f"Refresh by name 'Teamarr': {result}")
```

---

## Best Practices

### 1. Token Management

```python
# DO: Cache tokens and refresh proactively
auth = DispatcharrAuth(url, username, password)
token = auth.get_token()  # Handles caching automatically

# DON'T: Authenticate on every request
token = requests.post(f"{url}/api/accounts/token/", ...).json()["access"]  # Wasteful!
```

### 2. Error Handling

```python
# DO: Handle specific error cases
response = auth.post("/api/epg/import/", {"id": epg_id})
if response.status_code == 202:
    print("Refresh started")
elif response.status_code == 400:
    print("Cannot refresh dummy source")
elif response.status_code == 401:
    print("Authentication failed")

# DON'T: Assume success
auth.post("/api/epg/import/", {"id": epg_id})
print("Done!")  # Might have failed!
```

### 3. Credential Storage

```python
# DO: Load from environment or config file
import os
url = os.environ.get("DISPATCHARR_URL")
username = os.environ.get("DISPATCHARR_USERNAME")
password = os.environ.get("DISPATCHARR_PASSWORD")

# DON'T: Hardcode credentials
auth = DispatcharrAuth("http://localhost:9191", "admin", "hunter2")  # Bad!
```

### 4. Async Awareness

```python
# DO: Use wait_for_refresh() when you need completion before next step
result = manager.wait_for_refresh(21, timeout=60)
if result["success"]:
    print(f"Refresh completed in {result['duration']:.1f}s")
    # Now safe to associate EPG with channels

# DO: Understand that refresh() alone is async
result = manager.refresh(21)
if result["success"]:
    print("Refresh STARTED (not completed)")
    # Use wait_for_refresh() or poll manually if you need to know when it's done

# DON'T: Assume immediate completion
manager.refresh(21)
print("Refresh complete!")  # Wrong - it's still running!
```

### 5. Connection Handling

```python
# DO: Set reasonable timeouts
auth = DispatcharrAuth(url, username, password, timeout=30)

# DO: Handle connection failures gracefully
try:
    sources = manager.list_sources()
except requests.exceptions.ConnectionError:
    print("Dispatcharr unavailable")
    sources = []
```

---

## Error Handling

### HTTP Status Codes

| Code | Meaning | Action |
|------|---------|--------|
| 200 | Success | Process response |
| 202 | Accepted | Async task started |
| 400 | Bad Request | Check request data (e.g., dummy EPG) |
| 401 | Unauthorized | Re-authenticate |
| 403 | Forbidden | Check user permissions |
| 404 | Not Found | Resource doesn't exist |
| 500 | Server Error | Retry or report bug |

### Common Error Scenarios

**Invalid Credentials:**
```python
response = requests.post(f"{url}/api/accounts/token/", json={...})
if response.status_code == 401:
    # {"detail": "No active account found with the given credentials"}
    raise AuthenticationError("Invalid username or password")
```

**Expired Token:**
```python
response = auth.get("/api/epg/sources/")
if response.status_code == 401:
    # {"detail": "Given token not valid for any token type"}
    auth.clear_session()
    response = auth.get("/api/epg/sources/")  # Retry with fresh token
```

**Dummy EPG Refresh:**
```python
response = auth.post("/api/epg/import/", {"id": 10})
if response.status_code == 400:
    # {"success": false, "message": "Dummy EPG sources do not require refreshing."}
    print("Skipping dummy source")
```

---

## Quick Reference

### Endpoints Summary

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/accounts/token/` | Get access + refresh tokens |
| POST | `/api/accounts/token/refresh/` | Refresh access token |
| GET | `/api/epg/sources/` | List all EPG sources |
| GET | `/api/epg/sources/{id}/` | Get specific EPG source |
| POST | `/api/epg/import/` | Trigger EPG refresh |
| GET | `/api/epg/epgdata/` | Get parsed EPG data |
| GET | `/api/epg/grid/` | Get EPG grid data |

### Minimal Working Example

```python
import requests

# Configuration
URL = "http://localhost:9191"
USERNAME = "admin"
PASSWORD = "your_password"
EPG_ID = 21

# Authenticate
auth_resp = requests.post(
    f"{URL}/api/accounts/token/",
    json={"username": USERNAME, "password": PASSWORD}
)
token = auth_resp.json()["access"]
headers = {"Authorization": f"Bearer {token}"}

# List EPG sources
sources = requests.get(f"{URL}/api/epg/sources/", headers=headers).json()
for s in sources:
    print(f"{s['id']}: {s['name']}")

# Trigger refresh
result = requests.post(
    f"{URL}/api/epg/import/",
    headers=headers,
    json={"id": EPG_ID}
)
print(f"Refresh result: {result.json()}")
```

---

## Appendix: EPG Sources Reference (Current Setup)

| ID | Name | Type | Refreshable |
|----|------|------|-------------|
| 7 | @Provider EPG - MSX | url | Yes |
| 9 | @Provider EPG - Infinity | url | Yes |
| 16 | ^EPG USA - Jesmann 14-day | url | Yes |
| 17 | ^EPG USA FAST - Jesmann 14-day | url | Yes |
| 18 | ^EPG UK - Jesmann 14-day | url | Yes |
| 19 | ^EPG Canada - Jesmann 14-day | url | Yes |
| 21 | _Teamarr - Detroit Sports | url | Yes |
| 8 | _Dummy - FlareTV NFL | dummy | No |
| 10 | _Dummy - Infinity All Sports US | dummy | No |
| 11 | _Dummy - Infinity All Sports UK | dummy | No |
| 20 | _Dummy - Infinity NCAAM BB US (Copy) | dummy | No |

---

## Teamarr Integration: Automatic EPG Refresh

### Feature Overview

**"Automatically Refresh EPG in Dispatcharr after Creation"**

This feature allows Teamarr to automatically trigger a refresh of the corresponding Dispatcharr EPG source after successfully generating a new XMLTV file.

### Integration Workflow

```
┌─────────────────────────────────────────────────────────────────┐
│                    TEAMARR WORKFLOW                              │
└─────────────────────────────────────────────────────────────────┘

1. User enables "Auto Refresh EPG in Dispatcharr" in Advanced Settings
   ├─ Input: Dispatcharr URL (e.g., http://localhost:9191)
   ├─ Input: Username
   └─ Input: Password (stored securely, encrypted in database)

2. On Enable: Validate credentials and discover EPG source
   ├─ Authenticate to Dispatcharr using JIT auth
   ├─ Call GET /api/epg/sources/ to list all EPG sources
   ├─ Get Teamarr's output filename from settings (e.g., "teamarr.xml")
   ├─ Match EPG source by URL containing the filename
   │  Example: source["url"] = "http://teamarr:9195/epg.xml"
   │           matches filename "epg.xml" from Teamarr settings
   ├─ Store matched EPG source ID in Teamarr settings
   └─ Show success/error message to user

3. EPG Generation Hook: After successful XMLTV generation
   ├─ Check if auto-refresh is enabled in settings
   ├─ If enabled:
   │  ├─ Get stored Dispatcharr EPG source ID
   │  ├─ Authenticate using stored credentials (JIT)
   │  ├─ POST /api/epg/import/ with {"id": <epg_id>}
   │  └─ Log result (success/failure)
   └─ Continue normal flow

4. Settings Management
   ├─ Allow user to test connection at any time
   ├─ Re-discover EPG source if filename changes
   └─ Disable auto-refresh if credentials fail
```

### Implementation Checklist

#### Phase 1: Database & Settings Schema
- [ ] Add Dispatcharr settings fields to `settings` table:
  - `dispatcharr_enabled` (BOOLEAN, default: 0)
  - `dispatcharr_url` (TEXT, nullable)
  - `dispatcharr_username` (TEXT, nullable)
  - `dispatcharr_password` (TEXT, nullable) - encrypted/hashed
  - `dispatcharr_epg_id` (INTEGER, nullable) - discovered EPG source ID
  - `dispatcharr_last_sync` (TEXT, nullable) - ISO datetime of last successful refresh

#### Phase 2: Backend Authentication Module
- [ ] Create `api/dispatcharr_client.py` module
  - [ ] Implement `DispatcharrAuth` class (copy from this doc)
  - [ ] Add JIT token caching and refresh logic
  - [ ] Add session management
  - [ ] Add timeout handling (default: 30s)

- [ ] Create `api/dispatcharr_manager.py` module
  - [ ] Implement `EPGManager` class (copy from this doc)
  - [ ] Add `list_sources()` method
  - [ ] Add `find_by_url()` method - match EPG by URL containing filename
  - [ ] Add `refresh(epg_id)` method
  - [ ] Add error handling and logging

#### Phase 3: Settings UI - Advanced Settings Page
- [ ] Add "Dispatcharr Integration" section in Advanced Settings
  - [ ] Toggle: "Automatically Refresh EPG in Dispatcharr after Creation"
  - [ ] Input: Dispatcharr URL (text field with validation)
  - [ ] Input: Username (text field)
  - [ ] Input: Password (password field, show/hide toggle)
  - [ ] Button: "Test Connection & Discover EPG"
  - [ ] Display: Discovered EPG source name (if found)
  - [ ] Display: Last sync timestamp
  - [ ] Button: "Save Settings"

- [ ] Add JavaScript validation
  - [ ] Validate URL format (http/https)
  - [ ] Test connection on button click
  - [ ] Show loading spinner during test
  - [ ] Display success/error toast messages
  - [ ] Auto-populate EPG source name on successful discovery

#### Phase 4: Backend API Endpoints
- [ ] Add `/api/dispatcharr/test` endpoint (POST)
  - Input: `{url, username, password}`
  - Output: `{success, message, epg_sources: []}`
  - Function: Authenticate and return list of EPG sources

- [ ] Add `/api/dispatcharr/discover` endpoint (POST)
  - Input: `{url, username, password, filename}`
  - Output: `{success, epg_id, epg_name, message}`
  - Function: Find EPG source matching the filename

- [ ] Update `/settings` endpoint (POST)
  - Add support for Dispatcharr settings fields
  - Encrypt password before storing in database
  - Validate credentials on save

#### Phase 5: EPG Generation Hook
- [ ] Modify `epg/orchestrator.py` or `app.py` EPG generation flow
  - [ ] After successful XMLTV write to disk:
    - [ ] Check if `dispatcharr_enabled == 1`
    - [ ] If enabled, call Dispatcharr refresh hook
    - [ ] Log success/failure
    - [ ] Update `dispatcharr_last_sync` timestamp on success
    - [ ] Continue normal flow (don't block on errors)

- [ ] Create `utils/dispatcharr_hook.py` module
  - [ ] Function: `refresh_dispatcharr_epg(settings: dict) -> bool`
  - [ ] Load credentials from settings
  - [ ] Create DispatcharrAuth instance
  - [ ] Call EPGManager.refresh(epg_id)
  - [ ] Handle errors gracefully (don't crash EPG generation)
  - [ ] Return success/failure boolean

#### Phase 6: Security & Error Handling
- [ ] Password encryption
  - [ ] Use `cryptography` library (Fernet symmetric encryption)
  - [ ] Store encryption key in environment variable or secure location
  - [ ] Encrypt password before storing in database
  - [ ] Decrypt password when needed for API calls

- [ ] Error handling
  - [ ] Connection timeout (30s)
  - [ ] Invalid credentials (401/403)
  - [ ] EPG source not found (404)
  - [ ] Dispatcharr unavailable (connection refused)
  - [ ] Log all errors with context
  - [ ] Don't expose sensitive info in logs (mask password)

- [ ] Validation
  - [ ] URL format validation (http/https, valid port)
  - [ ] Username/password not empty
  - [ ] EPG source ID is valid integer

#### Phase 7: Testing & Documentation
- [ ] Unit tests
  - [ ] Test DispatcharrAuth token caching
  - [ ] Test EPGManager.find_by_url() matching logic
  - [ ] Test password encryption/decryption

- [ ] Integration tests
  - [ ] Test full workflow with mock Dispatcharr
  - [ ] Test error scenarios (invalid credentials, network failure)

- [ ] User documentation
  - [ ] Add section to README about Dispatcharr integration
  - [ ] Document setup steps (screenshots recommended)
  - [ ] Document troubleshooting common issues

### Key Implementation Details

#### Filename Matching Logic

When discovering the EPG source, match by URL containing the output filename:

```python
def find_epg_by_filename(sources: list, teamarr_filename: str) -> Optional[dict]:
    """
    Find Dispatcharr EPG source that matches Teamarr's output filename.

    Args:
        sources: List of EPG sources from Dispatcharr
        teamarr_filename: Output filename from Teamarr settings (e.g., "teamarr.xml")

    Returns:
        Matching EPG source or None
    """
    for source in sources:
        if source.get("source_type") != "url":
            continue

        url = source.get("url", "")
        # Extract filename from URL (e.g., "http://host/path/teamarr.xml" -> "teamarr.xml")
        url_filename = url.split("/")[-1]

        if url_filename == teamarr_filename:
            return source

    return None
```

#### Password Encryption

```python
from cryptography.fernet import Fernet
import os

# Load encryption key from environment (or generate and store securely)
ENCRYPTION_KEY = os.environ.get("TEAMARR_ENCRYPTION_KEY")
if not ENCRYPTION_KEY:
    # Generate on first run and save to .env or config
    ENCRYPTION_KEY = Fernet.generate_key().decode()

cipher = Fernet(ENCRYPTION_KEY.encode())

def encrypt_password(password: str) -> str:
    """Encrypt password for storage"""
    return cipher.encrypt(password.encode()).decode()

def decrypt_password(encrypted: str) -> str:
    """Decrypt password for use"""
    return cipher.decrypt(encrypted.encode()).decode()
```

#### Post-Generation Hook Location

Add to `app.py` after successful EPG generation:

```python
# In the /epg/generate endpoint, after writing XMLTV file:
xmltv_generator.write_file(xml_content, settings['output_file'])
app.logger.info(f"✅ EPG generated successfully: {settings['output_file']}")

# NEW: Dispatcharr auto-refresh hook
if settings.get('dispatcharr_enabled'):
    from utils.dispatcharr_hook import refresh_dispatcharr_epg
    try:
        success = refresh_dispatcharr_epg(settings)
        if success:
            app.logger.info("✅ Dispatcharr EPG refreshed successfully")
        else:
            app.logger.warning("⚠️ Dispatcharr EPG refresh failed (check logs)")
    except Exception as e:
        app.logger.error(f"❌ Dispatcharr refresh error: {e}")
        # Don't fail the whole generation - just log the error
```

### Configuration Example

Teamarr Settings after configuration:

```json
{
  "output_file": "teamarr.xml",
  "dispatcharr_enabled": true,
  "dispatcharr_url": "http://localhost:9191",
  "dispatcharr_username": "admin",
  "dispatcharr_password": "gAAAAABh... (encrypted)",
  "dispatcharr_epg_id": 21,
  "dispatcharr_last_sync": "2025-11-24T19:45:30Z"
}
```

Corresponding Dispatcharr EPG source:

```json
{
  "id": 21,
  "name": "_Teamarr - Detroit Sports",
  "source_type": "url",
  "url": "http://teamarr:9195/teamarr.xml",
  "is_active": true,
  "last_refresh": "2025-11-24T19:45:35Z"
}
```

### Notes & Considerations

1. **Don't hardcode filename**: Always pull from `settings['output_file']`
2. **Async refresh**: Dispatcharr refresh is async (Celery task), returns 202 immediately
3. **Error handling**: Don't fail EPG generation if Dispatcharr refresh fails
4. **Security**: Never log passwords in plaintext
5. **Multiple instances**: Support different Dispatcharr instances per Teamarr instance
6. **URL matching**: Be flexible with URL matching (consider both hostname and IP)

---

*Document generated: 2025-11-24*
*Updated with Teamarr integration workflow: 2025-11-24*
*Source: Investigation of Dispatcharr API and channelidentifiarr JIT auth implementation*
