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

    label: str = Field(
        ...,
        min_length=1,
        description="Label for channel naming and {exception_keyword} template variable (e.g., 'Spanish', 'Manningcast')",  # noqa: E501
    )
    match_terms: str = Field(
        ...,
        min_length=1,
        description="Comma-separated terms/phrases to match in stream names (e.g., 'Spanish, En Español, (ESP)')",  # noqa: E501
    )
    behavior: ExceptionBehavior = Field(
        default="consolidate",
        description="How to handle matched streams",
    )
    enabled: bool = True


class ExceptionKeywordUpdate(BaseModel):
    """Update exception keyword request."""

    label: str | None = Field(None, min_length=1)
    match_terms: str | None = Field(None, min_length=1)
    behavior: ExceptionBehavior | None = None
    enabled: bool | None = None


class ExceptionKeywordResponse(BaseModel):
    """Exception keyword response."""

    id: int
    label: str
    match_terms: str
    match_term_list: list[str]
    behavior: str
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
                label=kw.label,
                match_terms=kw.match_terms,
                match_term_list=kw.match_term_list,
                behavior=kw.behavior,
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
        label=keyword.label,
        match_terms=keyword.match_terms,
        match_term_list=keyword.match_term_list,
        behavior=keyword.behavior,
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
                label=request.label,
                match_terms=request.match_terms,
                behavior=request.behavior,
                enabled=request.enabled,
            )
            keyword = db_get_keyword(conn, keyword_id)
    except sqlite3.IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Label '{request.label}' already exists",
        ) from e

    return ExceptionKeywordResponse(
        id=keyword.id,
        label=keyword.label,
        match_terms=keyword.match_terms,
        match_term_list=keyword.match_term_list,
        behavior=keyword.behavior,
        enabled=keyword.enabled,
        created_at=keyword.created_at.isoformat() if keyword.created_at else None,
    )


@router.put("/{keyword_id}", response_model=ExceptionKeywordResponse)
def update_keyword(keyword_id: int, request: ExceptionKeywordUpdate):
    """Update an exception keyword."""
    import sqlite3

    from teamarr.database.exception_keywords import (
        get_keyword as db_get_keyword,
    )
    from teamarr.database.exception_keywords import (
        update_keyword as db_update_keyword,
    )

    try:
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
                label=request.label,
                match_terms=request.match_terms,
                behavior=request.behavior,
                enabled=request.enabled,
            )
            keyword = db_get_keyword(conn, keyword_id)
    except sqlite3.IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Label '{request.label}' already exists",
        ) from e

    return ExceptionKeywordResponse(
        id=keyword.id,
        label=keyword.label,
        match_terms=keyword.match_terms,
        match_term_list=keyword.match_term_list,
        behavior=keyword.behavior,
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
