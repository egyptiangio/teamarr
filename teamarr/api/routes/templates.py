"""Templates API endpoints."""

import json
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from teamarr.api.models import (
    TemplateCreate,
    TemplateFullResponse,
    TemplateResponse,
    TemplateUpdate,
)
from teamarr.database import get_db

router = APIRouter()


def _parse_json_fields(row: dict) -> dict:
    """Parse JSON string fields into Python objects."""
    result = dict(row)
    json_fields = [
        "xmltv_flags",
        "xmltv_categories",
        "pregame_periods",
        "pregame_fallback",
        "postgame_periods",
        "postgame_fallback",
        "postgame_conditional",
        "idle_content",
        "idle_conditional",
        "idle_offseason",
        "conditional_descriptions",
    ]
    for field in json_fields:
        if field in result and result[field]:
            try:
                result[field] = json.loads(result[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return result


@router.get("/templates", response_model=list[TemplateResponse])
def list_templates():
    """List all templates with usage counts."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            SELECT t.*,
                   COALESCE((SELECT COUNT(*) FROM teams WHERE template_id = t.id), 0) as team_count,
                   COALESCE((SELECT COUNT(*) FROM event_epg_groups WHERE template_id = t.id), 0) as group_count
            FROM templates t
            ORDER BY t.name
            """
        )
        return [dict(row) for row in cursor.fetchall()]


@router.post("/templates", response_model=TemplateResponse, status_code=status.HTTP_201_CREATED)
def create_template(template: TemplateCreate):
    """Create a new template."""
    with get_db() as conn:
        try:
            cursor = conn.execute(
                """
                INSERT INTO templates (
                    name, template_type, sport, league,
                    title_format, subtitle_template, description_template, program_art_url,
                    game_duration_mode, game_duration_override,
                    xmltv_flags, xmltv_categories, categories_apply_to,
                    pregame_enabled, pregame_periods, pregame_fallback,
                    postgame_enabled, postgame_periods, postgame_fallback, postgame_conditional,
                    idle_enabled, idle_content, idle_conditional, idle_offseason,
                    conditional_descriptions,
                    event_channel_name, event_channel_logo_url
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    template.name,
                    template.template_type,
                    template.sport,
                    template.league,
                    template.title_format,
                    template.subtitle_template,
                    template.description_template,
                    template.program_art_url,
                    template.game_duration_mode,
                    template.game_duration_override,
                    json.dumps(template.xmltv_flags) if template.xmltv_flags else None,
                    json.dumps(template.xmltv_categories) if template.xmltv_categories else None,
                    template.categories_apply_to,
                    template.pregame_enabled,
                    json.dumps([p.model_dump() for p in template.pregame_periods])
                    if template.pregame_periods
                    else None,
                    json.dumps(template.pregame_fallback.model_dump())
                    if template.pregame_fallback
                    else None,
                    template.postgame_enabled,
                    json.dumps([p.model_dump() for p in template.postgame_periods])
                    if template.postgame_periods
                    else None,
                    json.dumps(template.postgame_fallback.model_dump())
                    if template.postgame_fallback
                    else None,
                    json.dumps(template.postgame_conditional.model_dump())
                    if template.postgame_conditional
                    else None,
                    template.idle_enabled,
                    json.dumps(template.idle_content.model_dump())
                    if template.idle_content
                    else None,
                    json.dumps(template.idle_conditional.model_dump())
                    if template.idle_conditional
                    else None,
                    json.dumps(template.idle_offseason.model_dump())
                    if template.idle_offseason
                    else None,
                    json.dumps([c.model_dump() for c in template.conditional_descriptions])
                    if template.conditional_descriptions
                    else None,
                    template.event_channel_name,
                    template.event_channel_logo_url,
                ),
            )
            template_id = cursor.lastrowid
            cursor = conn.execute("SELECT * FROM templates WHERE id = ?", (template_id,))
            return dict(cursor.fetchone())
        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Template with this name already exists",
                ) from None
            raise


@router.get("/templates/{template_id}", response_model=TemplateFullResponse)
def get_template(template_id: int):
    """Get a template by ID with all JSON fields parsed."""
    with get_db() as conn:
        cursor = conn.execute("SELECT * FROM templates WHERE id = ?", (template_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
        return _parse_json_fields(dict(row))


def _serialize_for_db(key: str, value):
    """Serialize value for database storage."""
    json_fields = {
        "xmltv_flags",
        "xmltv_categories",
        "pregame_periods",
        "pregame_fallback",
        "postgame_periods",
        "postgame_fallback",
        "postgame_conditional",
        "idle_content",
        "idle_conditional",
        "idle_offseason",
        "conditional_descriptions",
    }
    if key in json_fields and value is not None:
        if hasattr(value, "model_dump"):
            return json.dumps(value.model_dump())
        elif isinstance(value, list):
            return json.dumps([v.model_dump() if hasattr(v, "model_dump") else v for v in value])
        elif isinstance(value, dict):
            return json.dumps(value)
    return value


@router.put("/templates/{template_id}", response_model=TemplateResponse)
def update_template(template_id: int, template: TemplateUpdate):
    """Update a template."""
    updates = {k: v for k, v in template.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")

    # Serialize JSON fields
    serialized = {k: _serialize_for_db(k, v) for k, v in updates.items()}

    set_clause = ", ".join(f"{k} = ?" for k in serialized.keys())
    values = list(serialized.values()) + [template_id]

    with get_db() as conn:
        cursor = conn.execute(f"UPDATE templates SET {set_clause} WHERE id = ?", values)
        if cursor.rowcount == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
        cursor = conn.execute("SELECT * FROM templates WHERE id = ?", (template_id,))
        return dict(cursor.fetchone())


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(template_id: int):
    """Delete a template."""
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")


# V1 Migration


class V1MigrateRequest(BaseModel):
    """Request to migrate templates from V1 database."""

    v1_db_path: str


class V1MigrateResponse(BaseModel):
    """Response from V1 migration."""

    success: bool
    migrated_count: int
    templates: list[str]
    message: str


def _parse_v1_json(value, default=None):
    """Parse JSON string from V1, returning default on failure."""
    if value is None:
        return default if default is not None else {}
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else {}


def _convert_v1_to_v2(v1_row: dict) -> dict:
    """Convert V1 template row to V2 format."""
    # Parse V1 JSON fields
    v1_flags = _parse_v1_json(v1_row.get("flags"), {"new": True, "live": False, "date": False})
    v1_categories = _parse_v1_json(v1_row.get("categories"), ["Sports"])
    v1_description_options = _parse_v1_json(v1_row.get("description_options"), [])
    v1_pregame_periods = _parse_v1_json(v1_row.get("pregame_periods"), [])
    v1_postgame_periods = _parse_v1_json(v1_row.get("postgame_periods"), [])

    # Build V2 pregame_fallback from V1 individual fields
    pregame_fallback = {
        "title": v1_row.get("pregame_title") or "Pregame Coverage",
        "subtitle": v1_row.get("pregame_subtitle"),
        "description": v1_row.get("pregame_description"),
        "art_url": v1_row.get("pregame_art_url"),
    }

    # Build V2 postgame_fallback from V1 individual fields
    postgame_fallback = {
        "title": v1_row.get("postgame_title") or "Postgame Recap",
        "subtitle": v1_row.get("postgame_subtitle"),
        "description": v1_row.get("postgame_description"),
        "art_url": v1_row.get("postgame_art_url"),
    }

    # Build V2 postgame_conditional from V1 fields
    postgame_conditional = {
        "enabled": bool(v1_row.get("postgame_conditional_enabled")),
        "description_final": v1_row.get("postgame_description_final"),
        "description_not_final": v1_row.get("postgame_description_not_final"),
    }

    # Build V2 idle_content from V1 individual fields
    idle_content = {
        "title": v1_row.get("idle_title") or "{team_name} Programming",
        "subtitle": v1_row.get("idle_subtitle"),
        "description": v1_row.get("idle_description"),
        "art_url": v1_row.get("idle_art_url"),
    }

    # Build V2 idle_conditional from V1 fields
    idle_conditional = {
        "enabled": bool(v1_row.get("idle_conditional_enabled")),
        "description_final": v1_row.get("idle_description_final"),
        "description_not_final": v1_row.get("idle_description_not_final"),
    }

    # Build V2 idle_offseason from V1 fields
    idle_offseason = {
        "title_enabled": bool(v1_row.get("idle_title_offseason_enabled")),
        "title": v1_row.get("idle_title_offseason"),
        "subtitle_enabled": bool(v1_row.get("idle_subtitle_offseason_enabled")),
        "subtitle": v1_row.get("idle_subtitle_offseason"),
        "description_enabled": bool(v1_row.get("idle_offseason_enabled")),
        "description": v1_row.get("idle_description_offseason"),
    }

    # Map V1 to V2 fields
    return {
        "name": v1_row.get("name"),
        "template_type": v1_row.get("template_type") or "team",
        "sport": v1_row.get("sport"),
        "league": v1_row.get("league"),
        "title_format": v1_row.get("title_format"),
        "subtitle_template": v1_row.get("subtitle_template"),
        "description_template": None,  # V2 uses conditional_descriptions instead
        "program_art_url": v1_row.get("program_art_url"),
        "game_duration_mode": v1_row.get("game_duration_mode") or "sport",
        "game_duration_override": v1_row.get("game_duration_override"),
        "xmltv_flags": json.dumps(v1_flags),
        "xmltv_categories": json.dumps(v1_categories),
        "categories_apply_to": v1_row.get("categories_apply_to") or "all",
        "pregame_enabled": bool(v1_row.get("pregame_enabled")),
        "pregame_periods": json.dumps(v1_pregame_periods),
        "pregame_fallback": json.dumps(pregame_fallback),
        "postgame_enabled": bool(v1_row.get("postgame_enabled")),
        "postgame_periods": json.dumps(v1_postgame_periods),
        "postgame_fallback": json.dumps(postgame_fallback),
        "postgame_conditional": json.dumps(postgame_conditional),
        "idle_enabled": bool(v1_row.get("idle_enabled")),
        "idle_content": json.dumps(idle_content),
        "idle_conditional": json.dumps(idle_conditional),
        "idle_offseason": json.dumps(idle_offseason),
        "conditional_descriptions": json.dumps(v1_description_options),
        "event_channel_name": v1_row.get("channel_name"),
        "event_channel_logo_url": v1_row.get("channel_logo_url"),
    }


@router.post("/templates/migrate-v1", response_model=V1MigrateResponse)
def migrate_v1_templates(request: V1MigrateRequest):
    """Migrate templates from V1 database.

    Converts V1 template format to V2's restructured format.
    Skips migration if V2 already has templates with the same name.
    """
    v1_path = Path(request.v1_db_path)

    if not v1_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"V1 database not found at {v1_path}",
        )

    # Read V1 templates
    try:
        v1_conn = sqlite3.connect(v1_path)
        v1_conn.row_factory = sqlite3.Row
        cursor = v1_conn.execute("SELECT * FROM templates")
        v1_templates = [dict(row) for row in cursor.fetchall()]
        v1_conn.close()
    except sqlite3.Error as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error reading V1 database: {e}",
        ) from None

    if not v1_templates:
        return V1MigrateResponse(
            success=True,
            migrated_count=0,
            templates=[],
            message="No templates found in V1 database",
        )

    # Get existing V2 template names to avoid duplicates
    with get_db() as conn:
        cursor = conn.execute("SELECT name FROM templates")
        existing_names = {row["name"] for row in cursor.fetchall()}

    # Convert and insert templates
    migrated = []
    skipped = []

    with get_db() as conn:
        for v1_row in v1_templates:
            v2_template = _convert_v1_to_v2(v1_row)
            name = v2_template["name"]

            if name in existing_names:
                skipped.append(name)
                continue

            columns = list(v2_template.keys())
            placeholders = ", ".join("?" * len(columns))
            column_str = ", ".join(columns)
            values = [v2_template[col] for col in columns]

            conn.execute(
                f"INSERT INTO templates ({column_str}) VALUES ({placeholders})",
                values,
            )
            migrated.append(name)

    message_parts = []
    if migrated:
        message_parts.append(f"Migrated {len(migrated)} template(s)")
    if skipped:
        message_parts.append(f"Skipped {len(skipped)} existing: {', '.join(skipped)}")

    return V1MigrateResponse(
        success=True,
        migrated_count=len(migrated),
        templates=migrated,
        message=". ".join(message_parts) if message_parts else "No templates to migrate",
    )


# V1 Teams Migration


class V1TeamsResponse(BaseModel):
    """Response from V1 teams migration."""

    success: bool
    migrated_count: int
    teams: list[str]
    message: str


def _convert_v1_team_to_v2(v1_row: dict, template_id_map: dict[int, int]) -> dict:
    """Convert V1 team row to V2 format."""
    league = v1_row.get("league") or ""

    # Map V1 template_id to V2 template_id (if template was migrated)
    v1_template_id = v1_row.get("template_id")
    v2_template_id = template_id_map.get(v1_template_id) if v1_template_id else None

    return {
        "provider": "espn",  # V1 only supported ESPN
        "provider_team_id": v1_row.get("espn_team_id"),
        "primary_league": league,
        "leagues": json.dumps([league] if league else []),
        "sport": v1_row.get("sport"),
        "team_name": v1_row.get("team_name"),
        "team_abbrev": v1_row.get("team_abbrev"),
        "team_logo_url": v1_row.get("team_logo_url"),
        "team_color": v1_row.get("team_color"),
        "channel_id": v1_row.get("channel_id"),
        "channel_logo_url": v1_row.get("channel_logo_url"),
        "template_id": v2_template_id,
        "active": v1_row.get("active", 1),
    }


@router.post("/templates/migrate-v1-teams", response_model=V1TeamsResponse)
def migrate_v1_teams(request: V1MigrateRequest):
    """Migrate teams from V1 database.

    Converts V1 team format to V2's restructured format.
    Skips teams with duplicate channel_id (V2 unique constraint).
    Template references are remapped if templates were migrated first.
    """
    v1_path = Path(request.v1_db_path)

    if not v1_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"V1 database not found at {v1_path}",
        )

    # Read V1 teams
    try:
        v1_conn = sqlite3.connect(v1_path)
        v1_conn.row_factory = sqlite3.Row
        cursor = v1_conn.execute("SELECT * FROM teams")
        v1_teams = [dict(row) for row in cursor.fetchall()]
        # Also get V1 templates for ID mapping
        cursor = v1_conn.execute("SELECT id, name FROM templates")
        v1_templates = {row["id"]: row["name"] for row in cursor.fetchall()}
        v1_conn.close()
    except sqlite3.Error as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error reading V1 database: {e}",
        ) from None

    if not v1_teams:
        return V1TeamsResponse(
            success=True,
            migrated_count=0,
            teams=[],
            message="No teams found in V1 database",
        )

    # Build template ID map: V1 id -> V2 id (by matching name)
    template_id_map: dict[int, int] = {}
    with get_db() as conn:
        for v1_id, v1_name in v1_templates.items():
            cursor = conn.execute("SELECT id FROM templates WHERE name = ?", (v1_name,))
            row = cursor.fetchone()
            if row:
                template_id_map[v1_id] = row["id"]

    # Get existing V2 channel_ids to avoid duplicates
    with get_db() as conn:
        cursor = conn.execute("SELECT channel_id FROM teams")
        existing_channel_ids = {row["channel_id"] for row in cursor.fetchall()}

    # Convert and insert teams
    migrated = []
    skipped = []

    with get_db() as conn:
        for v1_row in v1_teams:
            v2_team = _convert_v1_team_to_v2(v1_row, template_id_map)
            channel_id = v2_team["channel_id"]

            if channel_id in existing_channel_ids:
                skipped.append(channel_id)
                continue

            columns = list(v2_team.keys())
            placeholders = ", ".join("?" * len(columns))
            column_str = ", ".join(columns)
            values = [v2_team[col] for col in columns]

            conn.execute(
                f"INSERT INTO teams ({column_str}) VALUES ({placeholders})",
                values,
            )
            migrated.append(v2_team["team_name"])

    message_parts = []
    if migrated:
        message_parts.append(f"Migrated {len(migrated)} team(s)")
    if skipped:
        message_parts.append(f"Skipped {len(skipped)} existing")
    if template_id_map:
        message_parts.append(f"Remapped {len(template_id_map)} template reference(s)")

    return V1TeamsResponse(
        success=True,
        migrated_count=len(migrated),
        teams=migrated,
        message=". ".join(message_parts) if message_parts else "No teams to migrate",
    )


# V1 Event Groups Migration


class V1GroupsResponse(BaseModel):
    """Response from V1 event groups migration."""

    success: bool
    migrated_count: int
    groups: list[str]
    message: str


def _map_timing_value(v1_timing: str | None, timing_type: str) -> str:
    """Map V1 timing values to V2 format."""
    if not v1_timing:
        return "same_day" if timing_type == "create" else "same_day"

    # V1 used "day_of", V2 uses "same_day"
    mapping = {
        "day_of": "same_day",
        "stream_available": "stream_available",
        "day_before": "day_before",
        "2_days_before": "2_days_before",
        "3_days_before": "3_days_before",
        "1_week_before": "1_week_before",
        "manual": "manual",
        "stream_removed": "stream_removed",
        "day_after": "day_after",
        "2_days_after": "2_days_after",
        "3_days_after": "3_days_after",
        "1_week_after": "1_week_after",
    }
    return mapping.get(v1_timing, "same_day")


def _convert_v1_group_to_v2(v1_row: dict, template_id_map: dict[int, int]) -> dict:
    """Convert V1 event group row to V2 format."""
    # Build leagues array from assigned_league and enabled_leagues
    leagues = []
    assigned_league = v1_row.get("assigned_league")
    if assigned_league:
        leagues.append(assigned_league)

    # If multi-sport, parse enabled_leagues JSON
    enabled_leagues_str = v1_row.get("enabled_leagues")
    if enabled_leagues_str:
        try:
            enabled = json.loads(enabled_leagues_str)
            if isinstance(enabled, list):
                for lg in enabled:
                    if lg and lg not in leagues:
                        leagues.append(lg)
        except (json.JSONDecodeError, TypeError):
            pass

    # Map V1 template_id to V2 template_id
    v1_template_id = v1_row.get("event_template_id")
    v2_template_id = template_id_map.get(v1_template_id) if v1_template_id else None

    # Convert single channel_profile_id to JSON array
    channel_profile_id = v1_row.get("channel_profile_id")
    channel_profile_ids = json.dumps([channel_profile_id]) if channel_profile_id else None

    return {
        "name": v1_row.get("group_name"),
        "leagues": json.dumps(leagues),
        "template_id": v2_template_id,
        "channel_start_number": v1_row.get("channel_start"),
        "channel_group_id": v1_row.get("channel_group_id"),
        "stream_profile_id": v1_row.get("stream_profile_id"),
        "channel_profile_ids": channel_profile_ids,
        "create_timing": _map_timing_value(v1_row.get("channel_create_timing"), "create"),
        "delete_timing": _map_timing_value(v1_row.get("channel_delete_timing"), "delete"),
        "duplicate_event_handling": v1_row.get("duplicate_event_handling") or "consolidate",
        "channel_assignment_mode": v1_row.get("channel_assignment_mode") or "auto",
        "sort_order": v1_row.get("sort_order") or 0,
        "total_stream_count": v1_row.get("total_stream_count") or 0,
        "parent_group_id": None,  # Reset - parent relationships need manual setup
        "m3u_group_id": v1_row.get("dispatcharr_group_id"),
        "m3u_group_name": v1_row.get("channel_group_name"),
        "m3u_account_id": v1_row.get("dispatcharr_account_id"),
        "m3u_account_name": v1_row.get("account_name"),
        "last_refresh": None,  # Reset
        "stream_count": 0,  # Reset
        "matched_count": 0,  # Reset
        "stream_include_regex": v1_row.get("stream_include_regex"),
        "stream_include_regex_enabled": v1_row.get("stream_include_regex_enabled") or 0,
        "stream_exclude_regex": v1_row.get("stream_exclude_regex"),
        "stream_exclude_regex_enabled": v1_row.get("stream_exclude_regex_enabled") or 0,
        "custom_regex_teams": v1_row.get("custom_regex_teams") or v1_row.get("custom_regex"),
        "custom_regex_teams_enabled": v1_row.get("custom_regex_teams_enabled")
        or v1_row.get("custom_regex_enabled")
        or 0,
        "custom_regex_date": v1_row.get("custom_regex_date"),
        "custom_regex_date_enabled": v1_row.get("custom_regex_date_enabled") or 0,
        "custom_regex_time": v1_row.get("custom_regex_time"),
        "custom_regex_time_enabled": v1_row.get("custom_regex_time_enabled") or 0,
        "skip_builtin_filter": v1_row.get("skip_builtin_filter") or 0,
        "filtered_include_regex": 0,  # Reset
        "filtered_exclude_regex": 0,  # Reset
        "filtered_not_event": 0,  # Reset
        "failed_count": 0,  # Reset
        "channel_sort_order": v1_row.get("channel_sort_order") or "time",
        # Map V1 overlap_handling to V2 values ("consolidate" → "add_stream")
        "overlap_handling": "add_stream"
        if v1_row.get("overlap_handling") in (None, "consolidate")
        else v1_row.get("overlap_handling"),
        "enabled": v1_row.get("enabled", 1),
    }


@router.post("/templates/migrate-v1-groups", response_model=V1GroupsResponse)
def migrate_v1_groups(request: V1MigrateRequest):
    """Migrate event groups from V1 database.

    Converts V1 event group format to V2's restructured format.
    Skips groups with duplicate names (V2 unique constraint).
    Template references are remapped if templates were migrated first.
    """
    v1_path = Path(request.v1_db_path)

    if not v1_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"V1 database not found at {v1_path}",
        )

    # Read V1 event groups
    try:
        v1_conn = sqlite3.connect(v1_path)
        v1_conn.row_factory = sqlite3.Row
        cursor = v1_conn.execute("SELECT * FROM event_epg_groups")
        v1_groups = [dict(row) for row in cursor.fetchall()]
        # Also get V1 templates for ID mapping
        cursor = v1_conn.execute("SELECT id, name FROM templates")
        v1_templates = {row["id"]: row["name"] for row in cursor.fetchall()}
        v1_conn.close()
    except sqlite3.Error as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error reading V1 database: {e}",
        ) from None

    if not v1_groups:
        return V1GroupsResponse(
            success=True,
            migrated_count=0,
            groups=[],
            message="No event groups found in V1 database",
        )

    # Build template ID map: V1 id -> V2 id (by matching name)
    template_id_map: dict[int, int] = {}
    with get_db() as conn:
        for v1_id, v1_name in v1_templates.items():
            cursor = conn.execute("SELECT id FROM templates WHERE name = ?", (v1_name,))
            row = cursor.fetchone()
            if row:
                template_id_map[v1_id] = row["id"]

    # Get existing V2 group names to avoid duplicates
    with get_db() as conn:
        cursor = conn.execute("SELECT name FROM event_epg_groups")
        existing_names = {row["name"] for row in cursor.fetchall()}

    # Build V1 parent relationships: child_name -> parent_name
    v1_id_to_name = {row["id"]: row["group_name"] for row in v1_groups}
    v1_parent_map: dict[str, str] = {}  # child_name -> parent_name
    for v1_row in v1_groups:
        parent_id = v1_row.get("parent_group_id")
        if parent_id and parent_id in v1_id_to_name:
            child_name = v1_row["group_name"]
            parent_name = v1_id_to_name[parent_id]
            v1_parent_map[child_name] = parent_name

    # Convert and insert groups
    migrated = []
    skipped = []

    with get_db() as conn:
        for v1_row in v1_groups:
            v2_group = _convert_v1_group_to_v2(v1_row, template_id_map)
            name = v2_group["name"]

            if name in existing_names:
                skipped.append(name)
                continue

            columns = list(v2_group.keys())
            placeholders = ", ".join("?" * len(columns))
            column_str = ", ".join(columns)
            values = [v2_group[col] for col in columns]

            conn.execute(
                f"INSERT INTO event_epg_groups ({column_str}) VALUES ({placeholders})",
                values,
            )
            migrated.append(name)

    # Remap parent/child relationships by name
    parent_remapped = 0
    if v1_parent_map:
        with get_db() as conn:
            # Build V2 name -> id map
            cursor = conn.execute("SELECT id, name FROM event_epg_groups")
            v2_name_to_id = {row["name"]: row["id"] for row in cursor.fetchall()}

            for child_name, parent_name in v1_parent_map.items():
                child_id = v2_name_to_id.get(child_name)
                parent_id = v2_name_to_id.get(parent_name)
                if child_id and parent_id:
                    conn.execute(
                        "UPDATE event_epg_groups SET parent_group_id = ? WHERE id = ?",
                        (parent_id, child_id),
                    )
                    parent_remapped += 1

    message_parts = []
    if migrated:
        message_parts.append(f"Migrated {len(migrated)} group(s)")
    if skipped:
        message_parts.append(f"Skipped {len(skipped)} existing")
    if template_id_map:
        message_parts.append(f"Remapped {len(template_id_map)} template reference(s)")
    if parent_remapped:
        message_parts.append(f"Restored {parent_remapped} parent/child relationship(s)")

    return V1GroupsResponse(
        success=True,
        migrated_count=len(migrated),
        groups=migrated,
        message=". ".join(message_parts) if message_parts else "No groups to migrate",
    )


# Combined V1 Migration


class V1FullMigrateResponse(BaseModel):
    """Response from full V1 migration."""

    success: bool
    templates_migrated: int
    teams_migrated: int
    groups_migrated: int
    message: str


@router.post("/templates/migrate-v1-all", response_model=V1FullMigrateResponse)
def migrate_v1_all(request: V1MigrateRequest):
    """Migrate all data from V1 database: templates, teams, and event groups.

    Migrates in order: templates first (for ID remapping), then teams and groups.
    Skips duplicates in each category.
    """
    v1_path = Path(request.v1_db_path)

    if not v1_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"V1 database not found at {v1_path}",
        )

    # Migrate templates first
    templates_result = migrate_v1_templates(request)

    # Migrate teams
    teams_result = migrate_v1_teams(request)

    # Migrate event groups
    groups_result = migrate_v1_groups(request)

    # Build combined message
    parts = []
    if templates_result.migrated_count:
        parts.append(f"{templates_result.migrated_count} templates")
    if teams_result.migrated_count:
        parts.append(f"{teams_result.migrated_count} teams")
    if groups_result.migrated_count:
        parts.append(f"{groups_result.migrated_count} groups")

    message = (
        f"Migrated: {', '.join(parts)}" if parts else "Nothing to migrate (all items already exist)"
    )

    return V1FullMigrateResponse(
        success=True,
        templates_migrated=templates_result.migrated_count,
        teams_migrated=teams_result.migrated_count,
        groups_migrated=groups_result.migrated_count,
        message=message,
    )
