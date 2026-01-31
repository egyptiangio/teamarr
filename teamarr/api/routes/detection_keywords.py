"""Detection Keywords API routes.

CRUD operations for user-defined detection patterns that extend
the built-in patterns in DetectionKeywordService.
"""

import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from teamarr.database.connection import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/detection-keywords", tags=["Detection Keywords"])

# Valid categories for detection keywords
CategoryType = Literal[
    "combat_sports",
    "league_hints",
    "sport_hints",
    "placeholders",
    "card_segments",
    "exclusions",
    "separators",
]


# =============================================================================
# Pydantic Models
# =============================================================================


class DetectionKeywordCreate(BaseModel):
    """Request to create a detection keyword."""

    category: CategoryType
    keyword: str = Field(..., min_length=1, max_length=200)
    is_regex: bool = False
    target_value: str | None = None
    enabled: bool = True
    priority: int = 0
    description: str | None = None


class DetectionKeywordUpdate(BaseModel):
    """Request to update a detection keyword."""

    keyword: str | None = Field(None, min_length=1, max_length=200)
    is_regex: bool | None = None
    target_value: str | None = None
    enabled: bool | None = None
    priority: int | None = None
    description: str | None = None
    clear_target_value: bool = False
    clear_description: bool = False


class DetectionKeywordResponse(BaseModel):
    """Response for a detection keyword."""

    id: int
    category: str
    keyword: str
    is_regex: bool
    target_value: str | None
    enabled: bool
    priority: int
    description: str | None
    created_at: str
    updated_at: str


class DetectionKeywordListResponse(BaseModel):
    """Response for listing detection keywords."""

    total: int
    keywords: list[DetectionKeywordResponse]


class BulkImportRequest(BaseModel):
    """Request to bulk import detection keywords."""

    keywords: list[DetectionKeywordCreate]
    replace_category: bool = False  # If true, deletes existing in category first


class BulkImportResponse(BaseModel):
    """Response for bulk import."""

    created: int
    updated: int
    failed: int
    errors: list[str]


# =============================================================================
# Helper Functions
# =============================================================================


def _row_to_response(row: dict) -> DetectionKeywordResponse:
    """Convert database row to response model."""
    return DetectionKeywordResponse(
        id=row["id"],
        category=row["category"],
        keyword=row["keyword"],
        is_regex=bool(row["is_regex"]),
        target_value=row["target_value"],
        enabled=bool(row["enabled"]),
        priority=row["priority"] or 0,
        description=row["description"],
        created_at=row["created_at"] or "",
        updated_at=row["updated_at"] or "",
    )


# =============================================================================
# API Routes
# =============================================================================


@router.get("", response_model=DetectionKeywordListResponse)
def list_keywords(
    category: CategoryType | None = None,
    enabled_only: bool = False,
    conn=Depends(get_db),
):
    """List all detection keywords, optionally filtered by category."""
    query = "SELECT * FROM detection_keywords WHERE 1=1"
    params: list = []

    if category:
        query += " AND category = ?"
        params.append(category)

    if enabled_only:
        query += " AND enabled = 1"

    query += " ORDER BY category, priority DESC, keyword"

    rows = conn.execute(query, params).fetchall()

    return DetectionKeywordListResponse(
        total=len(rows),
        keywords=[_row_to_response(dict(r)) for r in rows],
    )


@router.get("/categories")
def list_categories():
    """List available keyword categories with descriptions."""
    return {
        "categories": [
            {
                "id": "combat_sports",
                "name": "Combat Sports",
                "description": "Keywords that indicate EVENT_CARD category (UFC, Boxing, MMA)",
                "has_target": False,
            },
            {
                "id": "league_hints",
                "name": "League Hints",
                "description": "Patterns that map to league code(s)",
                "has_target": True,
                "target_description": "League code or JSON array of codes",
            },
            {
                "id": "sport_hints",
                "name": "Sport Hints",
                "description": "Patterns that map to sport name",
                "has_target": True,
                "target_description": "Sport name (e.g., 'Hockey', 'Soccer')",
            },
            {
                "id": "placeholders",
                "name": "Placeholders",
                "description": "Patterns for placeholder/filler streams to skip",
                "has_target": False,
            },
            {
                "id": "card_segments",
                "name": "Card Segments",
                "description": "Patterns for UFC card segments",
                "has_target": True,
                "target_description": "Segment name: early_prelims, prelims, main_card, combined",
            },
            {
                "id": "exclusions",
                "name": "Exclusions",
                "description": "Patterns to exclude from matching (weigh-ins, etc.)",
                "has_target": False,
            },
            {
                "id": "separators",
                "name": "Separators",
                "description": "Game separators (vs, @, at)",
                "has_target": False,
            },
        ]
    }


@router.get("/{category}", response_model=DetectionKeywordListResponse)
def list_by_category(
    category: CategoryType,
    enabled_only: bool = False,
    conn=Depends(get_db),
):
    """List detection keywords for a specific category."""
    query = "SELECT * FROM detection_keywords WHERE category = ?"
    params: list = [category]

    if enabled_only:
        query += " AND enabled = 1"

    query += " ORDER BY priority DESC, keyword"

    rows = conn.execute(query, params).fetchall()

    return DetectionKeywordListResponse(
        total=len(rows),
        keywords=[_row_to_response(dict(r)) for r in rows],
    )


@router.post("", response_model=DetectionKeywordResponse, status_code=201)
def create_keyword(
    request: DetectionKeywordCreate,
    conn=Depends(get_db),
):
    """Create a new detection keyword."""
    try:
        cursor = conn.execute(
            """INSERT INTO detection_keywords
               (category, keyword, is_regex, target_value, enabled, priority, description)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                request.category,
                request.keyword,
                int(request.is_regex),
                request.target_value,
                int(request.enabled),
                request.priority,
                request.description,
            ),
        )
        conn.commit()
        keyword_id = cursor.lastrowid

        row = conn.execute(
            "SELECT * FROM detection_keywords WHERE id = ?", (keyword_id,)
        ).fetchone()

        logger.info(
            "[DETECTION_KW] Created keyword id=%d category=%s keyword=%s",
            keyword_id,
            request.category,
            request.keyword,
        )

        # Invalidate detection service cache
        from teamarr.services.detection_keywords import DetectionKeywordService

        DetectionKeywordService.invalidate_cache()

        return _row_to_response(dict(row))

    except Exception as e:
        if "UNIQUE constraint failed" in str(e):
            raise HTTPException(
                status_code=409,
                detail=f"Keyword '{request.keyword}' already exists in "
                f"category '{request.category}'",
            ) from None
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/id/{keyword_id}", response_model=DetectionKeywordResponse)
def get_keyword(
    keyword_id: int,
    conn=Depends(get_db),
):
    """Get a specific detection keyword by ID."""
    row = conn.execute(
        "SELECT * FROM detection_keywords WHERE id = ?", (keyword_id,)
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Keyword not found")

    return _row_to_response(dict(row))


@router.put("/id/{keyword_id}", response_model=DetectionKeywordResponse)
def update_keyword(
    keyword_id: int,
    request: DetectionKeywordUpdate,
    conn=Depends(get_db),
):
    """Update a detection keyword."""
    # Check exists
    existing = conn.execute(
        "SELECT * FROM detection_keywords WHERE id = ?", (keyword_id,)
    ).fetchone()

    if not existing:
        raise HTTPException(status_code=404, detail="Keyword not found")

    updates = ["updated_at = CURRENT_TIMESTAMP"]
    values: list = []

    if request.keyword is not None:
        updates.append("keyword = ?")
        values.append(request.keyword)

    if request.is_regex is not None:
        updates.append("is_regex = ?")
        values.append(int(request.is_regex))

    if request.target_value is not None:
        updates.append("target_value = ?")
        values.append(request.target_value)
    elif request.clear_target_value:
        updates.append("target_value = NULL")

    if request.enabled is not None:
        updates.append("enabled = ?")
        values.append(int(request.enabled))

    if request.priority is not None:
        updates.append("priority = ?")
        values.append(request.priority)

    if request.description is not None:
        updates.append("description = ?")
        values.append(request.description)
    elif request.clear_description:
        updates.append("description = NULL")

    if len(updates) > 1:  # More than just updated_at
        values.append(keyword_id)
        conn.execute(
            f"UPDATE detection_keywords SET {', '.join(updates)} WHERE id = ?",
            values,
        )
        conn.commit()

        logger.info("[DETECTION_KW] Updated keyword id=%d", keyword_id)

        # Invalidate detection service cache
        from teamarr.services.detection_keywords import DetectionKeywordService

        DetectionKeywordService.invalidate_cache()

    row = conn.execute(
        "SELECT * FROM detection_keywords WHERE id = ?", (keyword_id,)
    ).fetchone()

    return _row_to_response(dict(row))


@router.delete("/id/{keyword_id}", status_code=204)
def delete_keyword(
    keyword_id: int,
    conn=Depends(get_db),
):
    """Delete a detection keyword."""
    existing = conn.execute(
        "SELECT * FROM detection_keywords WHERE id = ?", (keyword_id,)
    ).fetchone()

    if not existing:
        raise HTTPException(status_code=404, detail="Keyword not found")

    conn.execute("DELETE FROM detection_keywords WHERE id = ?", (keyword_id,))
    conn.commit()

    logger.info(
        "[DETECTION_KW] Deleted keyword id=%d category=%s keyword=%s",
        keyword_id,
        existing["category"],
        existing["keyword"],
    )

    # Invalidate detection service cache
    from teamarr.services.detection_keywords import DetectionKeywordService

    DetectionKeywordService.invalidate_cache()


@router.post("/import", response_model=BulkImportResponse)
def bulk_import(
    request: BulkImportRequest,
    conn=Depends(get_db),
):
    """Bulk import detection keywords."""
    created = 0
    updated = 0
    failed = 0
    errors: list[str] = []

    # If replacing category, delete existing first
    if request.replace_category:
        categories = set(kw.category for kw in request.keywords)
        for cat in categories:
            conn.execute("DELETE FROM detection_keywords WHERE category = ?", (cat,))
            logger.info("[DETECTION_KW] Cleared category %s for replace import", cat)

    for kw in request.keywords:
        try:
            # Try insert, update on conflict
            cursor = conn.execute(
                """INSERT INTO detection_keywords
                   (category, keyword, is_regex, target_value, enabled, priority, description)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(category, keyword) DO UPDATE SET
                   is_regex = excluded.is_regex,
                   target_value = excluded.target_value,
                   enabled = excluded.enabled,
                   priority = excluded.priority,
                   description = excluded.description,
                   updated_at = CURRENT_TIMESTAMP""",
                (
                    kw.category,
                    kw.keyword,
                    int(kw.is_regex),
                    kw.target_value,
                    int(kw.enabled),
                    kw.priority,
                    kw.description,
                ),
            )
            if cursor.rowcount > 0:
                # Check if it was insert or update
                existing = conn.execute(
                    """SELECT created_at, updated_at FROM detection_keywords
                       WHERE category = ? AND keyword = ?""",
                    (kw.category, kw.keyword),
                ).fetchone()
                if existing and existing["created_at"] == existing["updated_at"]:
                    created += 1
                else:
                    updated += 1
        except Exception as e:
            failed += 1
            errors.append(f"{kw.category}/{kw.keyword}: {e}")

    conn.commit()

    logger.info(
        "[DETECTION_KW] Bulk import: created=%d updated=%d failed=%d",
        created,
        updated,
        failed,
    )

    # Invalidate detection service cache
    from teamarr.services.detection_keywords import DetectionKeywordService

    DetectionKeywordService.invalidate_cache()

    return BulkImportResponse(
        created=created,
        updated=updated,
        failed=failed,
        errors=errors,
    )


@router.get("/export")
def bulk_export(
    category: CategoryType | None = None,
    conn=Depends(get_db),
):
    """Export detection keywords as JSON."""
    query = "SELECT * FROM detection_keywords"
    params: list = []

    if category:
        query += " WHERE category = ?"
        params.append(category)

    query += " ORDER BY category, priority DESC, keyword"

    rows = conn.execute(query, params).fetchall()

    keywords = []
    for row in rows:
        keywords.append(
            {
                "category": row["category"],
                "keyword": row["keyword"],
                "is_regex": bool(row["is_regex"]),
                "target_value": row["target_value"],
                "enabled": bool(row["enabled"]),
                "priority": row["priority"] or 0,
                "description": row["description"],
            }
        )

    return {
        "exported_at": datetime.now().isoformat(),
        "count": len(keywords),
        "keywords": keywords,
    }
