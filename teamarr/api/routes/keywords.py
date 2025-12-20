"""Exception keywords API endpoints.

Provides REST API for managing consolidation exception keywords.
These keywords control how duplicate streams are handled during event matching.
"""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from teamarr.database import get_db

router = APIRouter()


ExceptionBehavior = Literal["consolidate", "separate", "ignore"]


# =============================================================================
# PYDANTIC MODELS
# =============================================================================


class ExceptionKeywordCreate(BaseModel):
    """Create exception keyword request."""

    keywords: str = Field(
        ...,
        min_length=1,
        description="Comma-separated keyword variants (e.g., 'Spanish, En Español, ESP')",
    )
    behavior: ExceptionBehavior = Field(
        default="consolidate",
        description="How to handle matched streams",
    )
    display_name: str | None = Field(
        None,
        description="Display name for UI",
    )
    enabled: bool = True


class ExceptionKeywordUpdate(BaseModel):
    """Update exception keyword request."""

    keywords: str | None = Field(None, min_length=1)
    behavior: ExceptionBehavior | None = None
    display_name: str | None = None
    enabled: bool | None = None
    clear_display_name: bool = False


class ExceptionKeywordResponse(BaseModel):
    """Exception keyword response."""

    id: int
    keywords: str
    keyword_list: list[str]
    behavior: str
    display_name: str | None = None
    enabled: bool
    created_at: str | None = None


class ExceptionKeywordListResponse(BaseModel):
    """List of exception keywords."""

    keywords: list[ExceptionKeywordResponse]
    total: int


# =============================================================================
# ENDPOINTS
# =============================================================================


@router.get("", response_model=ExceptionKeywordListResponse)
def list_keywords(
    include_disabled: bool = Query(False, description="Include disabled keywords"),
):
    """List all exception keywords."""
    from teamarr.database.exception_keywords import get_all_keywords

    with get_db() as conn:
        keywords = get_all_keywords(conn, include_disabled=include_disabled)

    return ExceptionKeywordListResponse(
        keywords=[
            ExceptionKeywordResponse(
                id=kw.id,
                keywords=kw.keywords,
                keyword_list=kw.keyword_list,
                behavior=kw.behavior,
                display_name=kw.display_name,
                enabled=kw.enabled,
                created_at=kw.created_at.isoformat() if kw.created_at else None,
            )
            for kw in keywords
        ],
        total=len(keywords),
    )


@router.get("/patterns")
def get_keyword_patterns() -> dict:
    """Get all enabled keyword patterns as a flat list.

    Useful for stream matching preview.
    """
    from teamarr.database.exception_keywords import get_all_keyword_patterns

    with get_db() as conn:
        patterns = get_all_keyword_patterns(conn)

    return {"patterns": patterns, "count": len(patterns)}


@router.get("/{keyword_id}", response_model=ExceptionKeywordResponse)
def get_keyword(keyword_id: int):
    """Get a single exception keyword by ID."""
    from teamarr.database.exception_keywords import get_keyword as db_get_keyword

    with get_db() as conn:
        keyword = db_get_keyword(conn, keyword_id)

    if not keyword:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Keyword {keyword_id} not found",
        )

    return ExceptionKeywordResponse(
        id=keyword.id,
        keywords=keyword.keywords,
        keyword_list=keyword.keyword_list,
        behavior=keyword.behavior,
        display_name=keyword.display_name,
        enabled=keyword.enabled,
        created_at=keyword.created_at.isoformat() if keyword.created_at else None,
    )


@router.post("", response_model=ExceptionKeywordResponse, status_code=status.HTTP_201_CREATED)
def create_keyword(request: ExceptionKeywordCreate):
    """Create a new exception keyword."""
    import sqlite3

    from teamarr.database.exception_keywords import (
        create_keyword as db_create_keyword,
    )
    from teamarr.database.exception_keywords import (
        get_keyword as db_get_keyword,
    )

    try:
        with get_db() as conn:
            keyword_id = db_create_keyword(
                conn,
                keywords=request.keywords,
                behavior=request.behavior,
                display_name=request.display_name,
                enabled=request.enabled,
            )
            keyword = db_get_keyword(conn, keyword_id)
    except sqlite3.IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Keywords '{request.keywords}' already exist",
        ) from e

    return ExceptionKeywordResponse(
        id=keyword.id,
        keywords=keyword.keywords,
        keyword_list=keyword.keyword_list,
        behavior=keyword.behavior,
        display_name=keyword.display_name,
        enabled=keyword.enabled,
        created_at=keyword.created_at.isoformat() if keyword.created_at else None,
    )


@router.put("/{keyword_id}", response_model=ExceptionKeywordResponse)
def update_keyword(keyword_id: int, request: ExceptionKeywordUpdate):
    """Update an exception keyword."""
    from teamarr.database.exception_keywords import (
        get_keyword as db_get_keyword,
    )
    from teamarr.database.exception_keywords import (
        update_keyword as db_update_keyword,
    )

    with get_db() as conn:
        keyword = db_get_keyword(conn, keyword_id)
        if not keyword:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Keyword {keyword_id} not found",
            )

        db_update_keyword(
            conn,
            keyword_id,
            keywords=request.keywords,
            behavior=request.behavior,
            display_name=request.display_name,
            enabled=request.enabled,
            clear_display_name=request.clear_display_name,
        )
        keyword = db_get_keyword(conn, keyword_id)

    return ExceptionKeywordResponse(
        id=keyword.id,
        keywords=keyword.keywords,
        keyword_list=keyword.keyword_list,
        behavior=keyword.behavior,
        display_name=keyword.display_name,
        enabled=keyword.enabled,
        created_at=keyword.created_at.isoformat() if keyword.created_at else None,
    )


@router.patch("/{keyword_id}/enabled")
def toggle_keyword(keyword_id: int, enabled: bool = Query(...)) -> dict:
    """Enable or disable an exception keyword."""
    from teamarr.database.exception_keywords import (
        get_keyword as db_get_keyword,
    )
    from teamarr.database.exception_keywords import (
        set_keyword_enabled,
    )

    with get_db() as conn:
        keyword = db_get_keyword(conn, keyword_id)
        if not keyword:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Keyword {keyword_id} not found",
            )

        set_keyword_enabled(conn, keyword_id, enabled)

    return {"id": keyword_id, "enabled": enabled}


@router.delete("/{keyword_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_keyword(keyword_id: int):
    """Delete an exception keyword."""
    from teamarr.database.exception_keywords import (
        delete_keyword as db_delete_keyword,
    )
    from teamarr.database.exception_keywords import (
        get_keyword as db_get_keyword,
    )

    with get_db() as conn:
        keyword = db_get_keyword(conn, keyword_id)
        if not keyword:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Keyword {keyword_id} not found",
            )

        db_delete_keyword(conn, keyword_id)
