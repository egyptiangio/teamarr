"""AI/Ollama integration endpoints.

Provides REST API for:
- AI service status and health checks
- Pattern learning for event groups
- AI settings management
- Pattern management (list, delete)
"""

import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from teamarr.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# PYDANTIC MODELS
# =============================================================================


class AIStatusResponse(BaseModel):
    """AI service status."""

    enabled: bool
    available: bool
    ollama_url: str
    model: str
    error: str | None = None


class AISettingsResponse(BaseModel):
    """AI settings."""

    enabled: bool
    ollama_url: str
    model: str
    use_for_parsing: bool
    use_for_matching: bool
    batch_size: int
    learn_patterns: bool
    fallback_to_regex: bool


class AISettingsUpdate(BaseModel):
    """Update AI settings request."""

    enabled: bool | None = None
    ollama_url: str | None = None
    model: str | None = None
    use_for_parsing: bool | None = None
    use_for_matching: bool | None = None
    batch_size: int | None = Field(None, ge=1, le=50)
    learn_patterns: bool | None = None
    fallback_to_regex: bool | None = None


class PatternResponse(BaseModel):
    """Learned pattern details."""

    pattern_id: str
    regex: str
    description: str
    example_streams: list[str]
    field_map: dict[str, str]
    confidence: float
    match_count: int
    fail_count: int
    group_id: int | None = None


class PatternListResponse(BaseModel):
    """List of learned patterns."""

    patterns: list[PatternResponse]
    total: int


class LearnPatternsRequest(BaseModel):
    """Request to learn patterns for a group."""

    group_id: int


class LearnPatternsResponse(BaseModel):
    """Response from pattern learning."""

    success: bool
    group_id: int
    group_name: str
    patterns_learned: int
    patterns: list[PatternResponse]
    coverage_percent: float
    error: str | None = None


class TestParseRequest(BaseModel):
    """Request to test AI parsing on streams."""

    streams: list[str] = Field(..., min_length=1, max_length=20)


class ParsedStreamResponse(BaseModel):
    """AI-parsed stream result."""

    stream: str
    team1: str | None = None
    team2: str | None = None
    league: str | None = None
    sport: str | None = None
    date: str | None = None
    time: str | None = None
    confidence: float = 0.0


class TestParseResponse(BaseModel):
    """Response from test parsing."""

    success: bool
    results: list[ParsedStreamResponse]
    error: str | None = None


# =============================================================================
# AI STATUS AND SETTINGS
# =============================================================================


@router.get("/status", response_model=AIStatusResponse)
def get_ai_status():
    """Get AI service status and availability."""
    from teamarr.database.settings import get_ai_settings
    from teamarr.services.ai import AIClassifier, AIClassifierConfig

    with get_db() as conn:
        settings = get_ai_settings(conn)

    if not settings.enabled:
        return AIStatusResponse(
            enabled=False,
            available=False,
            ollama_url=settings.ollama_url,
            model=settings.model,
            error="AI is disabled in settings",
        )

    # Check if Ollama is available
    config = AIClassifierConfig(settings=settings)
    classifier = AIClassifier(config)

    try:
        available = classifier.is_available()
        return AIStatusResponse(
            enabled=True,
            available=available,
            ollama_url=settings.ollama_url,
            model=settings.model,
            error=None if available else "Ollama not responding",
        )
    except Exception as e:
        return AIStatusResponse(
            enabled=True,
            available=False,
            ollama_url=settings.ollama_url,
            model=settings.model,
            error=str(e),
        )
    finally:
        classifier.close()


@router.get("/settings", response_model=AISettingsResponse)
def get_settings():
    """Get current AI settings."""
    from teamarr.database.settings import get_ai_settings

    with get_db() as conn:
        settings = get_ai_settings(conn)

    return AISettingsResponse(
        enabled=settings.enabled,
        ollama_url=settings.ollama_url,
        model=settings.model,
        use_for_parsing=settings.use_for_parsing,
        use_for_matching=settings.use_for_matching,
        batch_size=settings.batch_size,
        learn_patterns=settings.learn_patterns,
        fallback_to_regex=settings.fallback_to_regex,
    )


@router.put("/settings", response_model=AISettingsResponse)
def update_settings(request: AISettingsUpdate):
    """Update AI settings."""
    from teamarr.database.settings import get_ai_settings, update_ai_settings

    with get_db() as conn:
        # Only update provided fields
        updates = {k: v for k, v in request.model_dump().items() if v is not None}

        if updates:
            update_ai_settings(conn, **updates)

        settings = get_ai_settings(conn)

    logger.info("[AI] Settings updated: %s", list(updates.keys()))

    return AISettingsResponse(
        enabled=settings.enabled,
        ollama_url=settings.ollama_url,
        model=settings.model,
        use_for_parsing=settings.use_for_parsing,
        use_for_matching=settings.use_for_matching,
        batch_size=settings.batch_size,
        learn_patterns=settings.learn_patterns,
        fallback_to_regex=settings.fallback_to_regex,
    )


# =============================================================================
# PATTERN MANAGEMENT
# =============================================================================


@router.get("/patterns", response_model=PatternListResponse)
def list_patterns(group_id: int | None = None):
    """List all learned patterns, optionally filtered by group."""
    from teamarr.database.ai_patterns import get_all_patterns, get_patterns_for_group

    with get_db() as conn:
        if group_id is not None:
            patterns = get_patterns_for_group(conn, group_id)
        else:
            patterns = get_all_patterns(conn)

    return PatternListResponse(
        patterns=[
            PatternResponse(
                pattern_id=p["pattern_id"],
                regex=p["regex"],
                description=p.get("description", ""),
                example_streams=p.get("example_streams", []),
                field_map=p.get("field_map", {}),
                confidence=p.get("confidence", 0.0),
                match_count=p.get("match_count", 0),
                fail_count=p.get("fail_count", 0),
                group_id=p.get("group_id"),
            )
            for p in patterns
        ],
        total=len(patterns),
    )


@router.delete("/patterns/{pattern_id}")
def delete_pattern(pattern_id: str) -> dict:
    """Delete a learned pattern."""
    from teamarr.database.ai_patterns import delete_pattern as db_delete_pattern

    with get_db() as conn:
        db_delete_pattern(conn, pattern_id)

    logger.info("[AI] Deleted pattern: %s", pattern_id)

    return {"success": True, "message": f"Deleted pattern {pattern_id}"}


@router.delete("/patterns/group/{group_id}")
def delete_group_patterns(group_id: int) -> dict:
    """Delete all learned patterns for a group."""
    from teamarr.database.ai_patterns import delete_patterns_for_group
    from teamarr.database.groups import get_group

    with get_db() as conn:
        group = get_group(conn, group_id)
        if not group:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Group {group_id} not found",
            )

        count = delete_patterns_for_group(conn, group_id)

    logger.info("[AI] Deleted %d patterns for group %d (%s)", count, group_id, group.name)

    return {
        "success": True,
        "group_id": group_id,
        "group_name": group.name,
        "patterns_deleted": count,
    }


# =============================================================================
# PATTERN LEARNING
# =============================================================================


@router.post("/learn", response_model=LearnPatternsResponse)
def learn_patterns(request: LearnPatternsRequest):
    """Learn regex patterns from a group's streams.

    Fetches streams from Dispatcharr for the specified group,
    analyzes their format patterns using AI, and saves learned
    regex patterns to the database for fast future matching.
    """
    from teamarr.database.ai_patterns import get_patterns_for_group
    from teamarr.database.groups import get_group
    from teamarr.database.settings import get_ai_settings
    from teamarr.dispatcharr import get_factory
    from teamarr.services.ai import AIClassifier, AIClassifierConfig

    with get_db() as conn:
        group = get_group(conn, request.group_id)
        if not group:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Group {request.group_id} not found",
            )

        settings = get_ai_settings(conn)

    if not settings.enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="AI is disabled. Enable it in settings first.",
        )

    if not settings.learn_patterns:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pattern learning is disabled. Enable learn_patterns in settings.",
        )

    # Fetch streams from Dispatcharr
    factory = get_factory(get_db)
    if not factory:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dispatcharr not configured",
        )

    conn = factory.get_connection()
    if not conn or not conn.m3u:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dispatcharr connection failed",
        )

    streams = conn.m3u.list_streams(
        group_id=group.m3u_group_id,
        account_id=group.m3u_account_id,
    )

    if not streams:
        return LearnPatternsResponse(
            success=False,
            group_id=request.group_id,
            group_name=group.name,
            patterns_learned=0,
            patterns=[],
            coverage_percent=0.0,
            error="No streams found for this group",
        )

    stream_names = [s.name for s in streams]

    # Learn patterns
    with get_db() as db_conn:
        config = AIClassifierConfig(
            settings=settings,
            db_conn=db_conn,
            group_id=request.group_id,
        )
        classifier = AIClassifier(config)

        try:
            learned = classifier.learn_patterns_for_group(stream_names, request.group_id)

            # Calculate coverage
            covered = 0
            for name in stream_names:
                for pattern in learned:
                    if pattern.matches(name):
                        covered += 1
                        break

            coverage = (covered / len(stream_names)) * 100 if stream_names else 0

            # Get saved patterns from DB
            saved_patterns = get_patterns_for_group(db_conn, request.group_id)

            logger.info(
                "[AI] Learned %d patterns for group %d (%s) - %.1f%% coverage",
                len(learned),
                request.group_id,
                group.name,
                coverage,
            )

            return LearnPatternsResponse(
                success=True,
                group_id=request.group_id,
                group_name=group.name,
                patterns_learned=len(learned),
                patterns=[
                    PatternResponse(
                        pattern_id=p["pattern_id"],
                        regex=p["regex"],
                        description=p.get("description", ""),
                        example_streams=p.get("example_streams", []),
                        field_map=p.get("field_map", {}),
                        confidence=p.get("confidence", 0.0),
                        match_count=p.get("match_count", 0),
                        fail_count=p.get("fail_count", 0),
                        group_id=p.get("group_id"),
                    )
                    for p in saved_patterns
                ],
                coverage_percent=coverage,
            )

        except Exception as e:
            logger.exception("[AI] Pattern learning failed for group %d: %s", request.group_id, e)
            return LearnPatternsResponse(
                success=False,
                group_id=request.group_id,
                group_name=group.name,
                patterns_learned=0,
                patterns=[],
                coverage_percent=0.0,
                error=str(e),
            )
        finally:
            classifier.close()


# =============================================================================
# TEST PARSING
# =============================================================================


@router.post("/test-parse", response_model=TestParseResponse)
def test_parse(request: TestParseRequest):
    """Test AI parsing on provided streams.

    Useful for testing AI capabilities before enabling for a group.
    Does not save patterns or modify any settings.
    """
    from teamarr.database.settings import get_ai_settings
    from teamarr.services.ai.client import OllamaConfig
    from teamarr.services.ai.parser import AIStreamParser

    with get_db() as conn:
        settings = get_ai_settings(conn)

    if not settings.enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="AI is disabled. Enable it in settings first.",
        )

    ollama_config = OllamaConfig(
        base_url=settings.ollama_url,
        model=settings.model,
    )
    parser = AIStreamParser(ollama_config)

    try:
        if not parser.is_available():
            return TestParseResponse(
                success=False,
                results=[],
                error="Ollama is not available",
            )

        results = []
        for stream in request.streams:
            parsed = parser.parse_single(stream)
            if parsed:
                results.append(
                    ParsedStreamResponse(
                        stream=stream,
                        team1=parsed.team1,
                        team2=parsed.team2,
                        league=parsed.league,
                        sport=parsed.sport,
                        date=parsed.date,
                        time=parsed.time,
                        confidence=parsed.confidence,
                    )
                )
            else:
                results.append(
                    ParsedStreamResponse(
                        stream=stream,
                        confidence=0.0,
                    )
                )

        return TestParseResponse(
            success=True,
            results=results,
        )

    except Exception as e:
        logger.exception("[AI] Test parsing failed: %s", e)
        return TestParseResponse(
            success=False,
            results=[],
            error=str(e),
        )
    finally:
        parser.close()
