"""UI routes - serves Jinja2 templates with V2 backend data."""

import logging
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from teamarr.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()

# Configure templates directory
TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

# App version
VERSION = "2.0.0"

# Route name to URL path mapping for url_for compatibility
ROUTE_MAP = {
    "index": "/",
    "templates_list": "/templates",
    "template_new": "/templates/new",
    "template_edit": "/templates/{template_id}/edit",
    "template_form": "/templates/{template_id}/edit",
    "teams_list": "/teams",
    "team_new": "/teams/new",
    "team_edit": "/teams/{team_id}/edit",
    "team_form": "/teams/{team_id}/edit",
    "team_import": "/teams/import",
    "teams_import": "/teams/import",
    "teams_add_form": "/teams/new",
    "epg_management": "/epg",
    "event_groups_list": "/events",
    "event_group_new": "/events/new",
    "event_group_edit": "/events/{group_id}/edit",
    "event_groups_import": "/events/import",
    "channels_list": "/channels",
    "settings_form": "/settings",
    "settings_update": "/settings",
    "static": "/static",
    # API endpoints (for AJAX calls)
    "teams_bulk_activate": "/api/v1/teams/bulk-activate",
    "teams_bulk_assign_template": "/api/v1/teams/bulk-assign-template",
    "teams_bulk_change_channel_id": "/api/v1/teams/bulk-change-channel-id",
    "teams_bulk_delete": "/api/v1/teams/bulk-delete",
}


def custom_url_for(name: str, **kwargs) -> str:
    """Flask-compatible url_for function for templates."""
    if name == "static":
        filename = kwargs.get("filename", "")
        return f"/static/{filename}"

    path = ROUTE_MAP.get(name, f"/{name}")

    # Replace path parameters
    for key, value in kwargs.items():
        path = path.replace(f"{{{key}}}", str(value))

    return path


# Add custom url_for to Jinja2 environment
templates.env.globals["url_for"] = custom_url_for


def get_flashed_messages(with_categories: bool = False):
    """Flask-compatible get_flashed_messages (returns empty for now)."""
    return []


# Add get_flashed_messages to Jinja2 environment
templates.env.globals["get_flashed_messages"] = get_flashed_messages


def get_base_context(request: Request) -> dict:
    """Get base context for all templates."""
    return {
        "request": request,
        "version": VERSION,
        "settings": get_settings_dict(),
    }


def get_settings_dict() -> dict:
    """Get settings as a dict for templates - V2 native field names."""
    with get_db() as conn:
        cursor = conn.execute("SELECT * FROM settings WHERE id = 1")
        row = cursor.fetchone()
        return dict(row) if row else {}


# =============================================================================
# Dashboard
# =============================================================================


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    """Dashboard - overview of templates and teams."""
    context = get_base_context(request)

    with get_db() as conn:
        # Template stats
        template_count = conn.execute("SELECT COUNT(*) FROM templates").fetchone()[0]
        team_template_count = conn.execute(
            "SELECT COUNT(*) FROM templates WHERE template_type = 'team'"
        ).fetchone()[0]
        event_template_count = conn.execute(
            "SELECT COUNT(*) FROM templates WHERE template_type = 'event'"
        ).fetchone()[0]

        # Team stats
        team_count = conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
        active_team_count = conn.execute(
            "SELECT COUNT(*) FROM teams WHERE active = 1"
        ).fetchone()[0]
        assigned_team_count = conn.execute(
            "SELECT COUNT(*) FROM teams WHERE template_id IS NOT NULL"
        ).fetchone()[0]
        team_league_count = conn.execute(
            "SELECT COUNT(DISTINCT league) FROM teams WHERE league IS NOT NULL"
        ).fetchone()[0]

        # Event group stats (if table exists)
        try:
            event_group_count = conn.execute(
                "SELECT COUNT(*) FROM event_epg_groups"
            ).fetchone()[0]
            enabled_event_group_count = conn.execute(
                "SELECT COUNT(*) FROM event_epg_groups WHERE enabled = 1"
            ).fetchone()[0]
        except Exception:
            event_group_count = 0
            enabled_event_group_count = 0

        # Managed channel stats (if table exists)
        try:
            managed_channel_count = conn.execute(
                "SELECT COUNT(*) FROM managed_channels WHERE deleted_at IS NULL"
            ).fetchone()[0]
        except Exception:
            managed_channel_count = 0

    # EPG stats structure expected by template
    epg_stats = {
        "total_programmes": 0,
        "total_channels": 0,
        "channels": {"team_based": 0, "event_based": 0},
        "events": {"team_based": 0, "event_based": 0, "total": 0},
        "filler": {
            "total": 0,
            "pregame": {"total": 0},
            "postgame": {"total": 0},
            "idle": {"total": 0},
        },
    }

    context.update({
        "template_count": template_count,
        "team_template_count": team_template_count,
        "event_template_count": event_template_count,
        "team_count": team_count,
        "active_team_count": active_team_count,
        "assigned_team_count": assigned_team_count,
        "team_league_count": team_league_count,
        "event_group_count": event_group_count,
        "enabled_event_group_count": enabled_event_group_count,
        "total_event_streams": 0,
        "matched_event_streams": 0,
        "managed_channel_count": managed_channel_count,
        "channels_with_logos": 0,
        "recently_deleted_count": 0,
        "dispatcharr_groups_count": 0,
        "team_leagues": [],
        "event_leagues": [],
        "event_groups": [],
        "epg_stats": epg_stats,
        "generation_history": [],
        "live_games": [],
    })

    return templates.TemplateResponse("index.html", context)


# =============================================================================
# Templates
# =============================================================================


@router.get("/templates", response_class=HTMLResponse, name="templates_list")
def templates_list(request: Request):
    """List all templates."""
    context = get_base_context(request)

    with get_db() as conn:
        cursor = conn.execute("""
            SELECT t.*,
                   (SELECT COUNT(*) FROM teams WHERE template_id = t.id) as team_count
            FROM templates t
            ORDER BY t.name
        """)
        template_list = [dict(row) for row in cursor.fetchall()]

    context["templates"] = template_list
    return templates.TemplateResponse("template_list.html", context)


@router.get("/templates/new", response_class=HTMLResponse, name="template_new")
def template_new(request: Request):
    """New template form."""
    context = get_base_context(request)
    context["template"] = None
    context["is_edit"] = False
    return templates.TemplateResponse("template_form.html", context)


@router.get("/templates/{template_id}/edit", response_class=HTMLResponse, name="template_edit")
def template_edit(request: Request, template_id: int):
    """Edit template form."""
    context = get_base_context(request)

    with get_db() as conn:
        cursor = conn.execute("SELECT * FROM templates WHERE id = ?", (template_id,))
        row = cursor.fetchone()
        if row:
            context["template"] = dict(row)
        else:
            context["template"] = None

    context["is_edit"] = True
    return templates.TemplateResponse("template_form.html", context)


@router.post("/templates/new", response_class=HTMLResponse, name="template_create")
async def template_create(request: Request):
    """Handle new template form submission."""
    from fastapi.responses import RedirectResponse

    form_data = await request.form()

    # Build template data from form
    template_data = _parse_template_form(form_data)

    with get_db() as conn:
        try:
            conn.execute(
                """
                INSERT INTO templates (
                    name, template_type, sport, league,
                    title_format, subtitle_template, program_art_url,
                    game_duration_mode, game_duration_override,
                    xmltv_flags, xmltv_categories, categories_apply_to,
                    pregame_enabled, pregame_fallback,
                    postgame_enabled, postgame_fallback, postgame_conditional,
                    idle_enabled, idle_content, idle_conditional, idle_offseason,
                    conditional_descriptions,
                    event_channel_name, event_channel_logo_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    template_data["name"],
                    template_data["template_type"],
                    template_data.get("sport"),
                    template_data.get("league"),
                    template_data.get("title_format"),
                    template_data.get("subtitle_template"),
                    template_data.get("program_art_url"),
                    template_data.get("game_duration_mode", "sport"),
                    template_data.get("game_duration_override"),
                    template_data.get("xmltv_flags"),
                    template_data.get("xmltv_categories"),
                    template_data.get("categories_apply_to", "events"),
                    template_data.get("pregame_enabled", True),
                    template_data.get("pregame_fallback"),
                    template_data.get("postgame_enabled", True),
                    template_data.get("postgame_fallback"),
                    template_data.get("postgame_conditional"),
                    template_data.get("idle_enabled", True),
                    template_data.get("idle_content"),
                    template_data.get("idle_conditional"),
                    template_data.get("idle_offseason"),
                    template_data.get("conditional_descriptions"),
                    template_data.get("event_channel_name"),
                    template_data.get("event_channel_logo_url"),
                ),
            )
            conn.commit()
            return RedirectResponse(url="/templates", status_code=303)
        except Exception as e:
            logger.error(f"Failed to create template: {e}")
            context = get_base_context(request)
            context["template"] = template_data
            context["is_edit"] = False
            context["error"] = str(e)
            return templates.TemplateResponse("template_form.html", context)


@router.post("/templates/{template_id}/edit", response_class=HTMLResponse, name="template_update")
async def template_update(request: Request, template_id: int):
    """Handle template edit form submission."""
    from fastapi.responses import RedirectResponse

    form_data = await request.form()

    # Build template data from form
    template_data = _parse_template_form(form_data)

    with get_db() as conn:
        try:
            conn.execute(
                """
                UPDATE templates SET
                    name = ?, template_type = ?, sport = ?, league = ?,
                    title_format = ?, subtitle_template = ?, program_art_url = ?,
                    game_duration_mode = ?, game_duration_override = ?,
                    xmltv_flags = ?, xmltv_categories = ?, categories_apply_to = ?,
                    pregame_enabled = ?, pregame_fallback = ?,
                    postgame_enabled = ?, postgame_fallback = ?, postgame_conditional = ?,
                    idle_enabled = ?, idle_content = ?, idle_conditional = ?, idle_offseason = ?,
                    conditional_descriptions = ?,
                    event_channel_name = ?, event_channel_logo_url = ?
                WHERE id = ?
                """,
                (
                    template_data["name"],
                    template_data["template_type"],
                    template_data.get("sport"),
                    template_data.get("league"),
                    template_data.get("title_format"),
                    template_data.get("subtitle_template"),
                    template_data.get("program_art_url"),
                    template_data.get("game_duration_mode", "sport"),
                    template_data.get("game_duration_override"),
                    template_data.get("xmltv_flags"),
                    template_data.get("xmltv_categories"),
                    template_data.get("categories_apply_to", "events"),
                    template_data.get("pregame_enabled", True),
                    template_data.get("pregame_fallback"),
                    template_data.get("postgame_enabled", True),
                    template_data.get("postgame_fallback"),
                    template_data.get("postgame_conditional"),
                    template_data.get("idle_enabled", True),
                    template_data.get("idle_content"),
                    template_data.get("idle_conditional"),
                    template_data.get("idle_offseason"),
                    template_data.get("conditional_descriptions"),
                    template_data.get("event_channel_name"),
                    template_data.get("event_channel_logo_url"),
                    template_id,
                ),
            )
            conn.commit()
            return RedirectResponse(url="/templates", status_code=303)
        except Exception as e:
            logger.error(f"Failed to update template: {e}")
            context = get_base_context(request)
            context["template"] = template_data
            context["template"]["id"] = template_id
            context["is_edit"] = True
            context["error"] = str(e)
            return templates.TemplateResponse("template_form.html", context)


def _parse_template_form(form_data) -> dict:
    """Parse form data into template dict with JSON structures."""
    import json as json_lib

    # Basic fields
    template = {
        "name": form_data.get("name", "").strip(),
        "template_type": form_data.get("template_type", "team"),
        "sport": form_data.get("sport") or None,
        "league": form_data.get("league") or None,
        "title_format": form_data.get("title_format", ""),
        "subtitle_template": form_data.get("subtitle_template", ""),
        "program_art_url": form_data.get("program_art_url") or None,
        "game_duration_mode": form_data.get("game_duration_mode", "sport"),
        "categories_apply_to": form_data.get("categories_apply_to", "events"),
    }

    # Game duration override
    duration_override = form_data.get("game_duration_override")
    if duration_override:
        try:
            template["game_duration_override"] = float(duration_override)
        except ValueError:
            template["game_duration_override"] = None
    else:
        template["game_duration_override"] = None

    # Parse JSON fields from hidden inputs
    flags_json = form_data.get("flags", "{}")
    try:
        flags = json_lib.loads(flags_json) if flags_json else {}
        template["xmltv_flags"] = json_lib.dumps(flags) if flags else None
    except json_lib.JSONDecodeError:
        template["xmltv_flags"] = None

    categories_json = form_data.get("categories", "[]")
    try:
        categories = json_lib.loads(categories_json) if categories_json else []
        template["xmltv_categories"] = json_lib.dumps(categories) if categories else None
    except json_lib.JSONDecodeError:
        template["xmltv_categories"] = None

    description_options_json = form_data.get("description_options", "[]")
    try:
        desc_options = json_lib.loads(description_options_json) if description_options_json else []
        template["conditional_descriptions"] = (
            json_lib.dumps(desc_options) if desc_options else None
        )
    except json_lib.JSONDecodeError:
        template["conditional_descriptions"] = None

    # Checkbox fields - present = checked (True), absent = unchecked (False)
    template["pregame_enabled"] = "pregame_enabled" in form_data
    template["postgame_enabled"] = "postgame_enabled" in form_data
    template["idle_enabled"] = "idle_enabled" in form_data

    # Build pregame_fallback JSON
    pregame_fallback = {
        "title": form_data.get("pregame_title") or None,
        "subtitle": form_data.get("pregame_subtitle") or None,
        "description": form_data.get("pregame_description") or None,
        "art_url": form_data.get("pregame_art_url") or None,
    }
    template["pregame_fallback"] = json_lib.dumps(pregame_fallback)

    # Build postgame_fallback JSON
    postgame_fallback = {
        "title": form_data.get("postgame_title") or None,
        "subtitle": form_data.get("postgame_subtitle") or None,
        "description": form_data.get("postgame_description") or None,
        "art_url": form_data.get("postgame_art_url") or None,
    }
    template["postgame_fallback"] = json_lib.dumps(postgame_fallback)

    # Build postgame_conditional JSON
    postgame_conditional = {
        "enabled": "postgame_conditional_enabled" in form_data,
        "description_final": form_data.get("postgame_description_final") or None,
        "description_not_final": form_data.get("postgame_description_not_final") or None,
    }
    template["postgame_conditional"] = json_lib.dumps(postgame_conditional)

    # Build idle_content JSON
    idle_content = {
        "title": form_data.get("idle_title") or None,
        "subtitle": form_data.get("idle_subtitle") or None,
        "description": form_data.get("idle_description") or None,
        "art_url": form_data.get("idle_art_url") or None,
    }
    template["idle_content"] = json_lib.dumps(idle_content)

    # Build idle_conditional JSON
    idle_conditional = {
        "enabled": "idle_conditional_enabled" in form_data,
        "description_final": form_data.get("idle_description_final") or None,
        "description_not_final": form_data.get("idle_description_not_final") or None,
    }
    template["idle_conditional"] = json_lib.dumps(idle_conditional)

    # Build idle_offseason JSON
    idle_offseason = {
        "enabled": "idle_offseason_enabled" in form_data,
        "subtitle": form_data.get("idle_subtitle_offseason") or None,
        "description": form_data.get("idle_description_offseason") or None,
    }
    template["idle_offseason"] = json_lib.dumps(idle_offseason)

    # Event template specific
    template["event_channel_name"] = form_data.get("channel_name") or None
    template["event_channel_logo_url"] = form_data.get("channel_logo_url") or None

    return template


# =============================================================================
# Teams
# =============================================================================


@router.get("/teams", response_class=HTMLResponse, name="teams_list")
def teams_list(request: Request):
    """List all teams."""
    context = get_base_context(request)

    with get_db() as conn:
        cursor = conn.execute("""
            SELECT t.*,
                   tpl.name as template_name
            FROM teams t
            LEFT JOIN templates tpl ON t.template_id = tpl.id
            ORDER BY t.team_name
        """)
        team_list = [dict(row) for row in cursor.fetchall()]

        # Map V2 field names to V1 expected names
        for team in team_list:
            team["espn_team_id"] = team.get("provider_team_id", "")
            team["team_logo_url"] = team.get("team_logo_url", "")

        # Get available templates for dropdown
        cursor = conn.execute(
            "SELECT id, name FROM templates WHERE template_type = 'team' ORDER BY name"
        )
        template_options = [dict(row) for row in cursor.fetchall()]

        # Get team stats
        total_teams = len(team_list)
        enabled_teams = sum(1 for t in team_list if t.get("active"))

        # Group teams by league and sport
        teams_by_league = {}
        teams_by_sport = {}
        for team in team_list:
            league = team.get("league", "unknown")
            sport = team.get("sport", "unknown")
            if league not in teams_by_league:
                teams_by_league[league] = {"total": 0, "enabled": 0}
            teams_by_league[league]["total"] += 1
            if team.get("active"):
                teams_by_league[league]["enabled"] += 1

            if sport not in teams_by_sport:
                teams_by_sport[sport] = {"total": 0, "enabled": 0}
            teams_by_sport[sport]["total"] += 1
            if team.get("active"):
                teams_by_sport[sport]["enabled"] += 1

    context["teams"] = team_list
    context["templates"] = template_options
    context["team_stats"] = {"total": total_teams, "enabled": enabled_teams}
    context["teams_by_league"] = teams_by_league
    context["teams_by_sport"] = teams_by_sport
    context["league_logos"] = {}  # Empty for now, can add league logos later
    return templates.TemplateResponse("team_list.html", context)


@router.get("/teams/new", response_class=HTMLResponse, name="team_new")
def team_new(request: Request):
    """New team form."""
    context = get_base_context(request)
    context["team"] = None
    context["is_edit"] = False

    with get_db() as conn:
        cursor = conn.execute(
            "SELECT id, name FROM templates WHERE template_type = 'team' ORDER BY name"
        )
        context["templates"] = [dict(row) for row in cursor.fetchall()]

    return templates.TemplateResponse("team_form.html", context)


@router.get("/teams/{team_id}/edit", response_class=HTMLResponse, name="team_edit")
def team_edit(request: Request, team_id: int):
    """Edit team form."""
    context = get_base_context(request)

    with get_db() as conn:
        cursor = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,))
        row = cursor.fetchone()
        if row:
            context["team"] = dict(row)
        else:
            context["team"] = None

        cursor = conn.execute(
            "SELECT id, name FROM templates WHERE template_type = 'team' ORDER BY name"
        )
        context["templates"] = [dict(row) for row in cursor.fetchall()]

    context["is_edit"] = True
    return templates.TemplateResponse("team_form.html", context)


@router.get("/teams/import", response_class=HTMLResponse, name="team_import")
def team_import(request: Request):
    """Team import page."""
    context = get_base_context(request)
    return templates.TemplateResponse("team_import.html", context)


# =============================================================================
# EPG Management
# =============================================================================


@router.get("/epg", response_class=HTMLResponse, name="epg_management")
def epg_management(request: Request):
    """EPG management page."""
    context = get_base_context(request)

    with get_db() as conn:
        # Get team count for display
        team_count = conn.execute(
            "SELECT COUNT(*) FROM teams WHERE active = 1"
        ).fetchone()[0]

    # EPG stats structure
    epg_stats = {
        "total_programmes": 0,
        "total_channels": 0,
        "channels": {"team_based": team_count, "event_based": 0},
        "events": {"team_based": 0, "event_based": 0, "total": 0},
        "filler": {
            "total": 0,
            "pregame": {"total": 0},
            "postgame": {"total": 0},
            "idle": {"total": 0},
        },
    }

    # EPG analysis structure
    epg_analysis = {
        "coverage_gaps": [],
        "unreplaced_variables": [],
        "filler_programs": {"pregame": 0, "postgame": 0, "idle": 0},
        "date_range": {"start": "20251214", "end": "20251228"},
    }

    context.update({
        "team_count": team_count,
        "epg_stats": epg_stats,
        "epg_analysis": epg_analysis,
        "live_games": [],
        "failed_matches": [],
        "failed_count": 0,
        "epg_filename": "teamarr.xml",
        "epg_file_size": "0 KB",
        "epg_total_lines": 0,
        "epg_url": "/api/v1/epg/xmltv",
        "epg_content": "",
        "latest_epg": None,
        "generation_history": [],
        "league_logos": {},
    })

    return templates.TemplateResponse("epg_management.html", context)


# =============================================================================
# Event Groups
# =============================================================================


@router.get("/events", response_class=HTMLResponse, name="event_groups_list")
def event_groups_list(request: Request):
    """Event groups list page."""
    context = get_base_context(request)

    with get_db() as conn:
        try:
            cursor = conn.execute("""
                SELECT eg.*,
                       tpl.name as template_name
                FROM event_epg_groups eg
                LEFT JOIN templates tpl ON eg.template_id = tpl.id
                ORDER BY eg.id
            """)
            groups = [dict(row) for row in cursor.fetchall()]
        except Exception:
            groups = []

    context["event_groups"] = groups
    return templates.TemplateResponse("event_epg.html", context)


@router.get("/events/new", response_class=HTMLResponse, name="event_group_new")
def event_group_new(request: Request):
    """New event group form."""
    context = get_base_context(request)
    context["event_group"] = None
    context["is_edit"] = False
    return templates.TemplateResponse("event_group_form.html", context)


@router.get("/events/{group_id}/edit", response_class=HTMLResponse, name="event_group_edit")
def event_group_edit(request: Request, group_id: int):
    """Edit event group form."""
    context = get_base_context(request)

    with get_db() as conn:
        try:
            cursor = conn.execute(
                "SELECT * FROM event_epg_groups WHERE id = ?", (group_id,)
            )
            row = cursor.fetchone()
            context["event_group"] = dict(row) if row else None
        except Exception:
            context["event_group"] = None

    context["is_edit"] = True
    return templates.TemplateResponse("event_group_form.html", context)


@router.get("/events/import", response_class=HTMLResponse, name="event_groups_import")
def event_groups_import(request: Request):
    """Event groups import page."""
    context = get_base_context(request)
    return templates.TemplateResponse("event_groups_import.html", context)


# =============================================================================
# Channels
# =============================================================================


@router.get("/channels", response_class=HTMLResponse, name="channels_list")
def channels_list(request: Request):
    """Managed channels list page."""
    context = get_base_context(request)

    with get_db() as conn:
        try:
            cursor = conn.execute("""
                SELECT mc.*
                FROM managed_channels mc
                WHERE mc.deleted_at IS NULL
                ORDER BY mc.channel_name
            """)
            channels = [dict(row) for row in cursor.fetchall()]
        except Exception:
            channels = []

    context["channels"] = channels
    return templates.TemplateResponse("channels.html", context)


# =============================================================================
# Settings
# =============================================================================


@router.get("/settings", response_class=HTMLResponse, name="settings_form")
def settings_form(request: Request):
    """Settings page."""
    context = get_base_context(request)

    with get_db() as conn:
        cursor = conn.execute("SELECT * FROM settings WHERE id = 1")
        row = cursor.fetchone()
        settings_dict = dict(row) if row else {}

    # Pass settings directly - template now uses V2 field names
    context["settings"] = settings_dict

    return templates.TemplateResponse("settings.html", context)


@router.post("/settings", response_class=HTMLResponse, name="settings_update")
async def settings_update(request: Request):
    """Handle settings form submission - V2 native field names."""
    from fastapi.responses import RedirectResponse

    form_data = await request.form()

    # Valid settings columns (prevents SQL injection)
    valid_columns = {
        # System
        "epg_timezone", "time_format", "show_timezone",
        # Team-Based Streams
        "epg_output_days_ahead", "midnight_crossover_mode", "channel_id_format",
        # Event-Based Streams
        "channel_create_timing", "channel_delete_timing", "include_final_events",
        "event_match_days_ahead", "default_duplicate_event_handling",
        "channel_range_start", "channel_range_end",
        # EPG Generation / Durations
        "epg_output_path", "duration_default", "duration_baseball",
        "duration_basketball", "duration_football", "duration_hockey",
        "duration_soccer", "duration_mma",
        # Scheduler
        "scheduler_enabled", "cron_expression",
        # Dispatcharr
        "dispatcharr_enabled", "dispatcharr_url", "dispatcharr_username",
        "dispatcharr_password", "dispatcharr_epg_id",
        # Caching
        "soccer_cache_refresh_frequency", "team_cache_refresh_frequency",
        # XMLTV
        "xmltv_generator_name", "xmltv_generator_url",
    }

    # Checkbox fields - need special handling (not present when unchecked)
    checkbox_fields = {"dispatcharr_enabled", "scheduler_enabled"}

    # Integer fields
    integer_fields = {
        "epg_output_days_ahead", "event_match_days_ahead",
        "channel_range_start", "channel_range_end", "dispatcharr_epg_id",
    }

    # Float fields
    float_fields = {
        "duration_default", "duration_baseball", "duration_basketball",
        "duration_football", "duration_hockey", "duration_soccer", "duration_mma",
    }

    # Boolean fields (radio buttons with 0/1 values)
    boolean_fields = {"show_timezone", "include_final_events"}

    # Build update values
    updates = {}
    for field in valid_columns:
        if field in form_data:
            value = form_data[field]
            if field in checkbox_fields:
                updates[field] = 1  # Present = checked
            elif field in boolean_fields:
                updates[field] = 1 if value == "1" else 0
            elif field in integer_fields:
                updates[field] = int(value) if value else None
            elif field in float_fields:
                updates[field] = float(value) if value else None
            else:
                updates[field] = value

    # Handle unchecked checkboxes (not in form_data when unchecked)
    for field in checkbox_fields:
        if field not in form_data:
            updates[field] = 0

    # Update database
    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values())

        with get_db() as conn:
            conn.execute(f"UPDATE settings SET {set_clause} WHERE id = 1", values)
            conn.commit()

    # Redirect back to settings page
    return RedirectResponse(url="/settings", status_code=303)
