"""Template database operations.

CRUD operations for EPG templates and conversion to runtime configs.
Templates control EPG title/description formatting and filler content.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from sqlite3 import Connection, Row
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from teamarr.core.filler_types import FillerConfig


# =============================================================================
# DATA MODELS
# =============================================================================


@dataclass
class Template:
    """EPG template from database.

    Controls programme formatting (title, description, subtitle) and
    filler content (pregame, postgame, idle).
    """

    id: int
    name: str
    template_type: str  # 'team' or 'event'

    # Optional filters
    sport: str | None = None
    league: str | None = None

    # Programme formatting
    title_format: str = "{team_name} {sport}"
    subtitle_template: str = "{venue_full}"
    program_art_url: str | None = None

    # Game duration
    game_duration_mode: str = "sport"  # 'sport', 'default', 'custom'
    game_duration_override: float | None = None

    # XMLTV metadata
    xmltv_flags: dict = field(default_factory=lambda: {"new": True, "live": False, "date": False})
    xmltv_categories: list[str] = field(default_factory=lambda: ["Sports"])
    categories_apply_to: str = "events"  # 'all' or 'events'

    # Filler: Pregame
    pregame_enabled: bool = True
    pregame_periods: list[dict] = field(default_factory=list)
    pregame_fallback: dict = field(default_factory=dict)

    # Filler: Postgame
    postgame_enabled: bool = True
    postgame_periods: list[dict] = field(default_factory=list)
    postgame_fallback: dict = field(default_factory=dict)
    postgame_conditional: dict = field(
        default_factory=lambda: {
            "enabled": False,
            "description_final": None,
            "description_not_final": None,
        }
    )

    # Filler: Idle
    idle_enabled: bool = True
    idle_content: dict = field(default_factory=dict)
    idle_conditional: dict = field(
        default_factory=lambda: {
            "enabled": False,
            "description_final": None,
            "description_not_final": None,
        }
    )
    idle_offseason: dict = field(
        default_factory=lambda: {"enabled": False, "subtitle": None, "description": None}
    )

    # Conditional descriptions
    conditional_descriptions: list[dict] = field(default_factory=list)

    # Event template specific
    event_channel_name: str | None = None
    event_channel_logo_url: str | None = None

    # Timestamps
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class EventTemplateConfig:
    """Runtime config for event-based EPG generation.

    Used by EventEPGGenerator. No suffix support - single event context.
    """

    title_format: str = "{away_team} @ {home_team}"
    channel_name_format: str = "{away_team_abbrev} @ {home_team_abbrev}"
    description_format: str = "{matchup} | {venue_full} | {broadcast_simple}"
    subtitle_format: str = "{venue_city}"
    category: str = "Sports"
    program_art_url: str | None = None

    # XMLTV metadata
    xmltv_flags: dict = field(default_factory=lambda: {"new": True, "live": False, "date": False})
    xmltv_categories: list[str] = field(default_factory=lambda: ["Sports"])

    # Conditional descriptions (evaluated against single event)
    conditional_descriptions: list[dict] = field(default_factory=list)


# =============================================================================
# ROW CONVERSION
# =============================================================================


def _parse_json(value: str | None, default: Any = None) -> Any:
    """Parse JSON string, returning default on failure."""
    if value is None:
        return default if default is not None else {}
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else {}


def _row_to_template(row: Row) -> Template:
    """Convert database row to Template object."""
    return Template(
        id=row["id"],
        name=row["name"],
        template_type=row["template_type"] or "team",
        sport=row["sport"],
        league=row["league"],
        title_format=row["title_format"] or "{team_name} {sport}",
        subtitle_template=row["subtitle_template"] or "{venue_full}",
        program_art_url=row["program_art_url"],
        game_duration_mode=row["game_duration_mode"] or "sport",
        game_duration_override=row["game_duration_override"],
        xmltv_flags=_parse_json(row["xmltv_flags"], {"new": True, "live": False, "date": False}),
        xmltv_categories=_parse_json(row["xmltv_categories"], ["Sports"]),
        categories_apply_to=row["categories_apply_to"] or "events",
        pregame_enabled=bool(row["pregame_enabled"]),
        pregame_periods=_parse_json(row["pregame_periods"], []),
        pregame_fallback=_parse_json(row["pregame_fallback"], {}),
        postgame_enabled=bool(row["postgame_enabled"]),
        postgame_periods=_parse_json(row["postgame_periods"], []),
        postgame_fallback=_parse_json(row["postgame_fallback"], {}),
        postgame_conditional=_parse_json(
            row["postgame_conditional"],
            {"enabled": False, "description_final": None, "description_not_final": None},
        ),
        idle_enabled=bool(row["idle_enabled"]),
        idle_content=_parse_json(row["idle_content"], {}),
        idle_conditional=_parse_json(
            row["idle_conditional"],
            {"enabled": False, "description_final": None, "description_not_final": None},
        ),
        idle_offseason=_parse_json(
            row["idle_offseason"], {"enabled": False, "subtitle": None, "description": None}
        ),
        conditional_descriptions=_parse_json(row["conditional_descriptions"], []),
        event_channel_name=row["event_channel_name"],
        event_channel_logo_url=row["event_channel_logo_url"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# =============================================================================
# READ OPERATIONS
# =============================================================================


def get_template(conn: Connection, template_id: int) -> Template | None:
    """Get a template by ID.

    Args:
        conn: Database connection
        template_id: Template ID

    Returns:
        Template or None if not found
    """
    cursor = conn.execute("SELECT * FROM templates WHERE id = ?", (template_id,))
    row = cursor.fetchone()
    return _row_to_template(row) if row else None


def get_template_by_name(conn: Connection, name: str) -> Template | None:
    """Get a template by name.

    Args:
        conn: Database connection
        name: Template name

    Returns:
        Template or None if not found
    """
    cursor = conn.execute("SELECT * FROM templates WHERE name = ?", (name,))
    row = cursor.fetchone()
    return _row_to_template(row) if row else None


def get_all_templates(conn: Connection, template_type: str | None = None) -> list[Template]:
    """Get all templates, optionally filtered by type.

    Args:
        conn: Database connection
        template_type: Optional filter ('team' or 'event')

    Returns:
        List of Template objects
    """
    if template_type:
        cursor = conn.execute(
            "SELECT * FROM templates WHERE template_type = ? ORDER BY name", (template_type,)
        )
    else:
        cursor = conn.execute("SELECT * FROM templates ORDER BY name")

    return [_row_to_template(row) for row in cursor.fetchall()]


def get_templates_for_sport(conn: Connection, sport: str) -> list[Template]:
    """Get templates filtered by sport.

    Args:
        conn: Database connection
        sport: Sport name (e.g., 'football', 'basketball')

    Returns:
        List of matching templates (including templates with no sport filter)
    """
    cursor = conn.execute(
        "SELECT * FROM templates WHERE sport IS NULL OR sport = ? ORDER BY name", (sport,)
    )
    return [_row_to_template(row) for row in cursor.fetchall()]


def get_templates_for_league(conn: Connection, league: str) -> list[Template]:
    """Get templates filtered by league.

    Args:
        conn: Database connection
        league: League code (e.g., 'nfl', 'nba')

    Returns:
        List of matching templates (including templates with no league filter)
    """
    cursor = conn.execute(
        "SELECT * FROM templates WHERE league IS NULL OR league = ? ORDER BY name", (league,)
    )
    return [_row_to_template(row) for row in cursor.fetchall()]


# =============================================================================
# CREATE OPERATIONS
# =============================================================================


def create_template(
    conn: Connection,
    name: str,
    template_type: str = "team",
    **kwargs,
) -> int:
    """Create a new template.

    Args:
        conn: Database connection
        name: Template name (must be unique)
        template_type: 'team' or 'event'
        **kwargs: Additional template fields

    Returns:
        New template ID
    """
    # Build column list and values
    columns = ["name", "template_type"]
    values: list[Any] = [name, template_type]

    # JSON fields need serialization
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

    for key, value in kwargs.items():
        if value is not None:
            columns.append(key)
            if key in json_fields:
                values.append(json.dumps(value))
            else:
                values.append(value)

    placeholders = ", ".join("?" * len(values))
    column_str = ", ".join(columns)

    cursor = conn.execute(
        f"INSERT INTO templates ({column_str}) VALUES ({placeholders})", values
    )
    conn.commit()
    return cursor.lastrowid


# =============================================================================
# UPDATE OPERATIONS
# =============================================================================


def update_template(conn: Connection, template_id: int, **kwargs) -> bool:
    """Update a template.

    Args:
        conn: Database connection
        template_id: Template ID
        **kwargs: Fields to update

    Returns:
        True if updated
    """
    if not kwargs:
        return False

    # JSON fields need serialization
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

    sets = []
    values = []
    for key, value in kwargs.items():
        sets.append(f"{key} = ?")
        if key in json_fields and value is not None:
            values.append(json.dumps(value))
        else:
            values.append(value)

    values.append(template_id)
    set_str = ", ".join(sets)

    cursor = conn.execute(f"UPDATE templates SET {set_str} WHERE id = ?", values)
    conn.commit()
    return cursor.rowcount > 0


# =============================================================================
# DELETE OPERATIONS
# =============================================================================


def delete_template(conn: Connection, template_id: int) -> bool:
    """Delete a template.

    Args:
        conn: Database connection
        template_id: Template ID

    Returns:
        True if deleted
    """
    cursor = conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))
    conn.commit()
    return cursor.rowcount > 0


# =============================================================================
# CONVERSION TO RUNTIME CONFIGS
# =============================================================================


def template_to_filler_config(template: Template) -> FillerConfig:
    """Convert Template to FillerConfig for filler generation.

    Used by team-based EPG. Supports .next and .last suffixes in templates.

    Args:
        template: Template from database

    Returns:
        FillerConfig ready for FillerGenerator
    """
    # Import from core layer (proper layer isolation)
    from teamarr.core.filler_types import (
        ConditionalFillerTemplate,
        FillerConfig,
        FillerTemplate,
        OffseasonFillerTemplate,
    )

    # Build pregame template from fallback
    pregame_fb = template.pregame_fallback or {}
    pregame_desc_default = "{team_name} vs {opponent.next} at {game_time.next}"
    pregame_template = FillerTemplate(
        title=pregame_fb.get("title", "Pregame Coverage"),
        subtitle=pregame_fb.get("subtitle"),
        description=pregame_fb.get("description", pregame_desc_default),
        art_url=pregame_fb.get("art_url"),
    )

    # Build postgame template from fallback
    postgame_fb = template.postgame_fallback or {}
    postgame_desc_default = "{team_name} {result_text.last} {final_score.last}"
    postgame_template = FillerTemplate(
        title=postgame_fb.get("title", "Postgame Recap"),
        subtitle=postgame_fb.get("subtitle"),
        description=postgame_fb.get("description", postgame_desc_default),
        art_url=postgame_fb.get("art_url"),
    )

    # Postgame conditional
    pg_cond = template.postgame_conditional or {}
    postgame_conditional = ConditionalFillerTemplate(
        enabled=pg_cond.get("enabled", False),
        description_final=pg_cond.get("description_final"),
        description_not_final=pg_cond.get("description_not_final"),
    )

    # Build idle template
    idle_ct = template.idle_content or {}
    idle_template = FillerTemplate(
        title=idle_ct.get("title", "{team_name} Programming"),
        subtitle=idle_ct.get("subtitle"),
        description=idle_ct.get("description", "Next game: {game_date.next} vs {opponent.next}"),
        art_url=idle_ct.get("art_url"),
    )

    # Idle conditional
    idle_cond = template.idle_conditional or {}
    idle_conditional = ConditionalFillerTemplate(
        enabled=idle_cond.get("enabled", False),
        description_final=idle_cond.get("description_final"),
        description_not_final=idle_cond.get("description_not_final"),
    )

    # Idle offseason
    idle_off = template.idle_offseason or {}
    idle_offseason = OffseasonFillerTemplate(
        enabled=idle_off.get("enabled", False),
        title=idle_off.get("title"),
        subtitle=idle_off.get("subtitle"),
        description=idle_off.get("description"),
    )

    # Get category from xmltv_categories
    categories = template.xmltv_categories or ["Sports"]
    category = categories[0] if categories else "Sports"

    return FillerConfig(
        pregame_enabled=template.pregame_enabled,
        pregame_template=pregame_template,
        postgame_enabled=template.postgame_enabled,
        postgame_template=postgame_template,
        postgame_conditional=postgame_conditional,
        idle_enabled=template.idle_enabled,
        idle_template=idle_template,
        idle_conditional=idle_conditional,
        idle_offseason=idle_offseason,
        category=category,
    )


def template_to_event_config(template: Template) -> EventTemplateConfig:
    """Convert Template to EventTemplateConfig for event-based EPG.

    Used by event-based EPG. NO suffix support - single event context only.
    Variables use positional form: {home_team}, {away_team} not {team_name}, {opponent}.

    Args:
        template: Template from database

    Returns:
        EventTemplateConfig ready for EventEPGGenerator
    """
    # Get category from xmltv_categories
    categories = template.xmltv_categories or ["Sports"]

    # Default channel name format
    channel_name_default = "{away_team_abbrev} @ {home_team_abbrev}"

    return EventTemplateConfig(
        title_format=template.title_format or "{away_team} @ {home_team}",
        channel_name_format=template.event_channel_name or channel_name_default,
        description_format=template.subtitle_template or "{matchup} | {venue_full}",
        subtitle_format="{venue_city}",
        category=categories[0] if categories else "Sports",
        program_art_url=template.program_art_url,
        xmltv_flags=template.xmltv_flags or {"new": True, "live": False, "date": False},
        xmltv_categories=categories,
        conditional_descriptions=template.conditional_descriptions or [],
    )


# =============================================================================
# DEFAULT TEMPLATE SEEDING
# =============================================================================


def seed_default_templates(conn: Connection) -> None:
    """Seed default templates if none exist.

    Creates a basic team template and event template for getting started.
    """
    existing = get_all_templates(conn)
    if existing:
        return  # Don't overwrite existing templates

    # Default team template
    create_template(
        conn,
        name="Default Team",
        template_type="team",
        title_format="{team_name} {sport_title}",
        subtitle_template="{venue_full}",
        pregame_fallback={
            "title": "Pregame Coverage",
            "description": "{team_name} vs {opponent.next} | {game_time.next} | {venue.next}",
        },
        postgame_fallback={
            "title": "Postgame Recap",
            "description": "{team_name} {result_text.last} {opponent.last} {final_score.last}",
        },
        idle_content={
            "title": "{team_name} Programming",
            "description": "Next game: {game_day.next} {game_date_short.next} vs {opponent.next}",
        },
        conditional_descriptions=[
            {
                "condition": "is_home",
                "priority": 50,
                "template": "{team_name} hosts {opponent} at {venue}",
            },
            {
                "condition": "is_away",
                "priority": 50,
                "template": "{team_name} travels to face {opponent}",
            },
            {"priority": 100, "template": "{team_name} vs {opponent}"},
        ],
    )

    # Default event template
    create_template(
        conn,
        name="Default Event",
        template_type="event",
        title_format="{away_team} @ {home_team}",
        subtitle_template="{venue_full}",
        event_channel_name="{away_team_abbrev} @ {home_team_abbrev}",
        conditional_descriptions=[
            {"priority": 100, "template": "{matchup} | {venue_city} | {game_time}"},
        ],
    )
