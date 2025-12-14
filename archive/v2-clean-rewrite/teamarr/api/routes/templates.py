"""Templates API endpoints."""

import json

from fastapi import APIRouter, HTTPException, status

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
    """List all templates."""
    with get_db() as conn:
        cursor = conn.execute("SELECT * FROM templates ORDER BY name")
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
                    title_format, subtitle_template, program_art_url,
                    game_duration_mode, game_duration_override,
                    xmltv_flags, xmltv_categories, categories_apply_to,
                    pregame_enabled, pregame_periods, pregame_fallback,
                    postgame_enabled, postgame_periods, postgame_fallback, postgame_conditional,
                    idle_enabled, idle_content, idle_conditional,
                    event_channel_name, event_channel_logo_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    template.name,
                    template.template_type,
                    template.sport,
                    template.league,
                    template.title_format,
                    template.subtitle_template,
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


@router.put("/templates/{template_id}", response_model=TemplateResponse)
def update_template(template_id: int, template: TemplateUpdate):
    """Update a template."""
    updates = {k: v for k, v in template.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")

    set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
    values = list(updates.values()) + [template_id]

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
