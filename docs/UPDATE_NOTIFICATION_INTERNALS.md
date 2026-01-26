# Update Notification Implementation Details

This document explains how the update notification system works internally, including version detection, comparison logic, and frontend integration status.

## Current Implementation Status

### ✅ Implemented (Backend)
- Update checking via GitHub Releases API (stable)
- Update checking via GHCR manifests (dev)
- Configurable repository settings
- API endpoints for status and configuration
- Health endpoint integration
- Caching and rate limiting

### ❌ Not Implemented (Frontend)
- **No UI notifications** - Users must manually check API endpoints
- **No visual indicators** - No banners, toasts, or alerts in the web interface
- **No settings page** - Configuration must be done via API calls
- **No automatic polling** - Frontend doesn't check for updates periodically

## Version Detection Deep Dive

### How the Current Version is Determined

The application determines its version through a multi-step process:

#### 1. Base Version (from `pyproject.toml`)

```python
# Location: teamarr/config/__init__.py
BASE_VERSION = _get_base_version()  # Reads from pyproject.toml
```

The base version is the **source of truth** stored in `pyproject.toml`:

```toml
[project]
name = "teamarr"
version = "2.0.11"  # <-- This is the base version
```

#### 2. Build Information (Branch + SHA)

The system appends build information to create the full version:

**For Docker Builds:**
- Build args are passed: `GIT_BRANCH` and `GIT_SHA`
- Dockerfile creates files: `.git-branch` and `.git-sha`
- Application reads these files at runtime

```dockerfile
# Dockerfile excerpt
ARG GIT_BRANCH=unknown
ARG GIT_SHA=unknown

RUN echo "${GIT_BRANCH}" > /app/.git-branch && \
    echo "${GIT_SHA}" > /app/.git-sha
```

**For Development:**
- Executes git commands: `git rev-parse --abbrev-ref HEAD` (branch)
- Executes git commands: `git rev-parse --short=6 HEAD` (SHA)

#### 3. Version String Construction

```python
# main/master branch
version = "2.0.11"

# dev branch with commit abc123
version = "2.0.11-dev+abc123"

# feature branch with commit def456
version = "2.0.11-copilot/add-update-notification-feature+def456"
```

### Version Detection Reliability

**Strengths:**
✅ Single source of truth (`pyproject.toml`)
✅ Consistent across Docker and dev environments
✅ Automatic dev build detection
✅ Git integration for accurate commit tracking

**Potential Issues:**
⚠️ Requires `pyproject.toml` to be present (fails gracefully to "0.0.0")
⚠️ Docker builds require correct build args
⚠️ Dev environment requires `.git` directory

## Version Comparison Logic

### Stable Releases (Semantic Versioning)

```python
def _compare_versions(current: str, latest: str) -> bool:
    """Compare semantic versions."""
    current_parts = [int(x) for x in current.split(".")]
    latest_parts = [int(x) for x in latest.split(".")]
    
    # Pad to same length
    max_len = max(len(current_parts), len(latest_parts))
    current_parts.extend([0] * (max_len - len(current_parts)))
    latest_parts.extend([0] * (max_len - len(latest_parts)))
    
    return latest_parts > current_parts
```

**Examples:**

| Current | Latest | Update? | Reason |
|---------|--------|---------|--------|
| 2.0.11 | 2.0.12 | ✅ Yes | Patch version bump |
| 2.0.11 | 2.1.0 | ✅ Yes | Minor version bump |
| 2.0.11 | 3.0.0 | ✅ Yes | Major version bump |
| 2.0.11 | 2.0.11 | ❌ No | Same version |
| 2.0.12 | 2.0.11 | ❌ No | Current is newer |
| 2.0.11.1 | 2.0.11.2 | ✅ Yes | Works with 4-part versions |

**Comparison Method:**
- Splits version by "."
- Converts each part to integer
- Pads shorter version with zeros
- Compares as arrays (Python does element-by-element)

**Limitations:**
⚠️ Doesn't handle pre-release tags (e.g., "2.0.11-beta")
⚠️ Doesn't handle build metadata (e.g., "2.0.11+20240101")
⚠️ Simple integer comparison (not a full semver implementation)

### Dev Builds (GHCR Manifest Digests)

**Current Implementation:**
```python
# Always returns update_available = False
# Only fetches the manifest digest for visibility
```

**Why Dev Updates Don't Work:**
- ❌ No digest persistence - can't compare current vs latest
- ❌ No timestamp comparison - GHCR doesn't expose build times easily
- ❌ No state storage - would need database table to track last seen digest

**To make dev updates work, you would need to:**
1. Store the current manifest digest in the database
2. Compare current digest with latest from GHCR
3. Update the stored digest when user updates

## Version Detection Examples

### Scenario 1: Stable Release in Docker

```bash
# Docker build command
docker build --build-arg GIT_BRANCH=main --build-arg GIT_SHA=abc123 .

# Files created in container
/app/.git-branch: "main"
/app/.git-sha: "abc123"

# Version detected
"2.0.11"  # main branch = stable, no suffix
```

### Scenario 2: Dev Build in Docker

```bash
# Docker build command  
docker build --build-arg GIT_BRANCH=dev --build-arg GIT_SHA=def456 .

# Files created
/app/.git-branch: "dev"
/app/.git-sha: "def456"

# Version detected
"2.0.11-dev+def456"  # dev branch = dev build
```

### Scenario 3: Development Environment

```bash
# Developer checks out feature branch
git checkout -b feature/new-thing

# Application reads from git
git rev-parse --abbrev-ref HEAD  # "feature/new-thing"
git rev-parse --short=6 HEAD     # "abc123"

# Version detected
"2.0.11-feature/new-thing+abc123"
```

### Scenario 4: Production Tagged Release

```bash
# Tagged release built from main
docker build --build-arg GIT_BRANCH=main --build-arg GIT_SHA=v2.0.11 .

# Version detected
"2.0.11"  # main branch, clean version
```

## Comparison Accuracy Testing

### Test the Version Comparison

You can test the comparison logic directly:

```python
from teamarr.services.update_checker import StableUpdateChecker

checker = StableUpdateChecker(current_version="2.0.11")

# Test various scenarios
print(checker._compare_versions("2.0.11", "2.0.12"))  # True
print(checker._compare_versions("2.0.11", "2.1.0"))   # True
print(checker._compare_versions("2.0.11", "2.0.11"))  # False
print(checker._compare_versions("2.0.11", "2.0.10"))  # False
```

### Verify Current Version Detection

```bash
# Check what version the app detects
curl -s http://localhost:9195/health | jq .version

# Examples:
"2.0.11"                                    # Stable release
"2.0.11-dev+abc123"                         # Dev build
"2.0.11-copilot/feature-name+def456"        # Feature branch
```

## Adding Frontend UI Notifications

To add UI notifications, you would need to modify the frontend:

### 1. Create Update Notification Component

```tsx
// frontend/src/components/UpdateNotification.tsx
import { useEffect, useState } from 'react';

interface UpdateInfo {
  update_available: boolean;
  latest_version: string;
  current_version: string;
  download_url: string;
}

export function UpdateNotification() {
  const [updateInfo, setUpdateInfo] = useState<UpdateInfo | null>(null);

  useEffect(() => {
    // Check for updates every 6 hours
    const checkUpdates = async () => {
      const response = await fetch('/api/v1/updates/status');
      const data = await response.json();
      if (data.update_available) {
        setUpdateInfo(data);
      }
    };

    checkUpdates();
    const interval = setInterval(checkUpdates, 6 * 60 * 60 * 1000);
    return () => clearInterval(interval);
  }, []);

  if (!updateInfo?.update_available) return null;

  return (
    <div className="update-banner">
      <p>
        Update available: {updateInfo.current_version} → {updateInfo.latest_version}
      </p>
      <a href={updateInfo.download_url} target="_blank" rel="noopener noreferrer">
        View Release
      </a>
    </div>
  );
}
```

### 2. Add to Main App Component

```tsx
// frontend/src/App.tsx
import { UpdateNotification } from './components/UpdateNotification';

function App() {
  return (
    <>
      <UpdateNotification />
      {/* Rest of app */}
    </>
  );
}
```

### 3. Add Settings Page

Create a settings page where users can configure update checking:

```tsx
// frontend/src/pages/Settings.tsx
function UpdateSettings() {
  const [settings, setSettings] = useState({
    enabled: true,
    check_interval_hours: 24,
    notify_stable_updates: true,
    notify_dev_updates: false,
  });

  const saveSettings = async () => {
    await fetch('/api/v1/updates/settings', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    });
  };

  return (
    <div>
      <h2>Update Notifications</h2>
      {/* Form fields for settings */}
      <button onClick={saveSettings}>Save</button>
    </div>
  );
}
```

## Security Considerations

### Version Spoofing Prevention

**Concern:** Could someone spoof the version to prevent update notifications?

**Mitigation:**
- Version is built into the Docker image at build time
- Cannot be modified without rebuilding the image
- In dev, version comes from `pyproject.toml` and git (not user-editable at runtime)

### API Security

**Current State:**
- ✅ Read-only GitHub Releases API (public, no auth needed)
- ✅ GHCR manifest API (public for public images)
- ✅ No secrets or credentials required
- ✅ Rate limiting via caching

**Future Enhancements:**
- Add authentication token support for private forks
- Implement user-specific update preferences
- Add webhook support for push notifications

## Troubleshooting Version Detection

### Problem: Version shows "0.0.0"

**Cause:** Can't read `pyproject.toml`

**Solution:**
```bash
# Verify file exists
ls -la /app/pyproject.toml

# Check Python can read it
python -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb')))"
```

### Problem: Version shows "2.0.11-unknown+unknown"

**Cause:** Git info not available

**Solution for Docker:**
```bash
# Ensure build args are passed
docker build --build-arg GIT_BRANCH=main --build-arg GIT_SHA=$(git rev-parse --short HEAD) .
```

**Solution for Dev:**
```bash
# Ensure .git directory exists
ls -la .git/

# Test git commands work
git rev-parse --abbrev-ref HEAD
git rev-parse --short=6 HEAD
```

### Problem: Update check says "No update" but one exists

**Causes:**
1. Cache not expired (default 24 hours)
2. Version comparison logic issue
3. GitHub API rate limit

**Solutions:**
```bash
# Force fresh check
curl "http://localhost:9195/api/v1/updates/status?force=true"

# Check current vs latest
curl http://localhost:9195/api/v1/updates/status | jq '{current_version, latest_version, update_available}'

# Check GitHub directly
curl https://api.github.com/repos/Pharaoh-Labs/teamarr/releases/latest | jq .tag_name
```

## Summary

### Current Capabilities
✅ **Backend** update checking works reliably
✅ **Version detection** is accurate for Docker and dev environments
✅ **Comparison logic** handles standard semantic versions correctly
✅ **Configurable** for forks and custom deployments

### Limitations
❌ **No UI** - Users must manually check
❌ **No notifications** - No visual alerts
❌ **Dev updates** - Can't detect digest changes without state
❌ **Pre-release** - Doesn't handle beta/rc versions

### Recommended Next Steps
1. Add frontend React component for update banner
2. Add settings page for configuration
3. Implement digest persistence for dev builds
4. Add webhook support for real-time notifications
