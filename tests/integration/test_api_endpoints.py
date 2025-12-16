"""Integration tests for API endpoints.

Tests the stats, keywords, and presets API endpoints using FastAPI TestClient.
Uses unique identifiers to avoid conflicts with existing data.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from teamarr.api.app import create_app


def unique_id() -> str:
    """Generate unique identifier for test data."""
    return uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def client():
    """Create a test client using the real database."""
    app = create_app()
    with TestClient(app) as client:
        yield client


# =============================================================================
# HEALTH CHECK
# =============================================================================


class TestHealthEndpoint:
    """Test health check endpoint."""

    def test_health_check(self, client):
        """Health endpoint returns healthy."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"


# =============================================================================
# STATS ENDPOINTS
# =============================================================================


class TestStatsEndpoints:
    """Test stats API endpoints."""

    def test_get_stats_empty(self, client):
        """Stats endpoint returns empty stats initially."""
        response = client.get("/api/v1/stats")
        assert response.status_code == 200
        data = response.json()

        # Should have expected structure
        assert "overall" in data
        assert "streams" in data
        assert "channels" in data
        assert "programmes" in data
        assert "last_24h" in data

    def test_get_stats_history(self, client):
        """Stats history endpoint returns empty list initially."""
        response = client.get("/api/v1/stats/history")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_get_stats_history_with_days_param(self, client):
        """Stats history respects days parameter."""
        response = client.get("/api/v1/stats/history?days=30")
        assert response.status_code == 200

    def test_get_recent_runs_empty(self, client):
        """Recent runs endpoint returns empty list initially."""
        response = client.get("/api/v1/stats/runs")
        assert response.status_code == 200
        data = response.json()
        assert "runs" in data
        assert "count" in data
        assert isinstance(data["runs"], list)


# =============================================================================
# EXCEPTION KEYWORDS ENDPOINTS
# =============================================================================


class TestKeywordsEndpoints:
    """Test exception keywords API endpoints."""

    def test_list_keywords_has_defaults(self, client):
        """Keywords list includes seeded defaults."""
        response = client.get("/api/v1/keywords")
        assert response.status_code == 200
        data = response.json()

        # Schema seeds default language keywords
        assert "keywords" in data
        assert "total" in data
        assert len(data["keywords"]) > 0

        # Check one of the defaults exists
        keywords_list = [k["keywords"] for k in data["keywords"]]
        spanish_exists = any("Spanish" in k for k in keywords_list)
        assert spanish_exists, "Expected seeded Spanish keywords"

    def test_create_keyword(self, client):
        """Can create a new exception keyword."""
        uid = unique_id()
        response = client.post(
            "/api/v1/keywords",
            json={
                "keywords": f"TestLang_{uid}, Test Language_{uid}",
                "behavior": "consolidate",
                "display_name": f"Test Language {uid}",
            },
        )
        assert response.status_code == 201
        data = response.json()

        assert f"TestLang_{uid}" in data["keywords"]
        assert data["behavior"] == "consolidate"
        assert data["id"] is not None

    def test_get_keyword_by_id(self, client):
        """Can retrieve a keyword by ID."""
        uid = unique_id()
        # First create one
        create_resp = client.post(
            "/api/v1/keywords",
            json={"keywords": f"GetTest_{uid}, GT_{uid}", "behavior": "separate"},
        )
        keyword_id = create_resp.json()["id"]

        # Then get it
        response = client.get(f"/api/v1/keywords/{keyword_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == keyword_id
        assert f"GetTest_{uid}" in data["keywords"]

    def test_update_keyword(self, client):
        """Can update an existing keyword."""
        uid = unique_id()
        # Create
        create_resp = client.post(
            "/api/v1/keywords",
            json={"keywords": f"UpdateMe_{uid}", "behavior": "consolidate"},
        )
        keyword_id = create_resp.json()["id"]

        # Update (uses PUT not PATCH)
        response = client.put(
            f"/api/v1/keywords/{keyword_id}",
            json={"behavior": "ignore", "display_name": f"Updated_{uid}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["behavior"] == "ignore"
        assert f"Updated_{uid}" in data["display_name"]

    def test_delete_keyword(self, client):
        """Can delete a keyword."""
        uid = unique_id()
        # Create
        create_resp = client.post(
            "/api/v1/keywords",
            json={"keywords": f"DeleteMe_{uid}"},
        )
        keyword_id = create_resp.json()["id"]

        # Delete
        response = client.delete(f"/api/v1/keywords/{keyword_id}")
        assert response.status_code == 204

        # Verify deleted
        get_resp = client.get(f"/api/v1/keywords/{keyword_id}")
        assert get_resp.status_code == 404

    def test_create_duplicate_keyword_fails(self, client):
        """Creating duplicate keywords fails."""
        uid = unique_id()
        # Create first
        client.post("/api/v1/keywords", json={"keywords": f"UniqueKeyword_{uid}"})

        # Try to create duplicate
        response = client.post(
            "/api/v1/keywords", json={"keywords": f"UniqueKeyword_{uid}"}
        )
        assert response.status_code == 409  # Conflict


# =============================================================================
# CONDITION PRESETS ENDPOINTS
# =============================================================================


class TestPresetsEndpoints:
    """Test condition presets API endpoints."""

    def test_list_presets(self, client):
        """Presets list returns proper structure."""
        response = client.get("/api/v1/presets")
        assert response.status_code == 200
        data = response.json()
        assert "presets" in data
        assert "total" in data

    def test_create_preset(self, client):
        """Can create a condition preset."""
        uid = unique_id()
        response = client.post(
            "/api/v1/presets",
            json={
                "name": f"Win Streak Emphasis_{uid}",
                "description": "Highlight teams on win streaks",
                "conditions": [
                    {
                        "condition": "win_streak",
                        "value": "5",
                        "priority": 10,
                        "template": "{team_name} on a {streak} game win streak!",
                    }
                ],
            },
        )
        assert response.status_code == 201
        data = response.json()

        assert f"Win Streak Emphasis_{uid}" in data["name"]
        assert data["description"] == "Highlight teams on win streaks"
        assert len(data["conditions"]) == 1
        assert data["id"] is not None

    def test_get_preset_by_id(self, client):
        """Can retrieve a preset by ID."""
        uid = unique_id()
        # Create
        create_resp = client.post(
            "/api/v1/presets",
            json={"name": f"Get Test Preset_{uid}", "conditions": []},
        )
        preset_id = create_resp.json()["id"]

        # Get
        response = client.get(f"/api/v1/presets/{preset_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == preset_id
        assert f"Get Test Preset_{uid}" in data["name"]

    def test_update_preset(self, client):
        """Can update an existing preset."""
        uid = unique_id()
        # Create
        create_resp = client.post(
            "/api/v1/presets",
            json={"name": f"Update Test_{uid}", "conditions": []},
        )
        preset_id = create_resp.json()["id"]

        # Update (uses PUT not PATCH)
        response = client.put(
            f"/api/v1/presets/{preset_id}",
            json={
                "name": f"Updated Name_{uid}",
                "description": f"New description_{uid}",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert f"Updated Name_{uid}" in data["name"]
        assert f"New description_{uid}" in data["description"]

    def test_delete_preset(self, client):
        """Can delete a preset."""
        uid = unique_id()
        # Create
        create_resp = client.post(
            "/api/v1/presets",
            json={"name": f"Delete Test_{uid}"},
        )
        preset_id = create_resp.json()["id"]

        # Delete
        response = client.delete(f"/api/v1/presets/{preset_id}")
        assert response.status_code == 204

        # Verify deleted
        get_resp = client.get(f"/api/v1/presets/{preset_id}")
        assert get_resp.status_code == 404

    def test_create_duplicate_preset_fails(self, client):
        """Creating duplicate preset name fails."""
        uid = unique_id()
        # Create first
        client.post("/api/v1/presets", json={"name": f"UniquePreset_{uid}"})

        # Try duplicate
        response = client.post("/api/v1/presets", json={"name": f"UniquePreset_{uid}"})
        assert response.status_code == 409  # Conflict


# =============================================================================
# SETTINGS ENDPOINT
# =============================================================================


class TestSettingsEndpoint:
    """Test settings API endpoint."""

    def test_get_settings(self, client):
        """Can retrieve global settings."""
        response = client.get("/api/v1/settings")
        assert response.status_code == 200
        data = response.json()

        # Settings response has nested structure
        assert "epg" in data or "team_schedule_days_ahead" in data
        # Check expected nested fields
        if "epg" in data:
            assert "epg_output_days_ahead" in data["epg"]
        if "durations" in data:
            assert "basketball" in data["durations"]


# =============================================================================
# TEMPLATES ENDPOINT
# =============================================================================


class TestTemplatesEndpoint:
    """Test templates API endpoint."""

    def test_list_templates(self, client):
        """Can list templates."""
        response = client.get("/api/v1/templates")
        assert response.status_code == 200
        data = response.json()
        # Templates endpoint returns list with templates key
        assert "templates" in data or isinstance(data, list)

    def test_create_template(self, client):
        """Can create a template."""
        uid = unique_id()
        response = client.post(
            "/api/v1/templates",
            json={
                "name": f"Test Template_{uid}",
                "template_type": "team",
                "title_format": "{team_name} {sport}",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert f"Test Template_{uid}" in data["name"]
