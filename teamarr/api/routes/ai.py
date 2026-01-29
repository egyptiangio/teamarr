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
    timeout: int
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
    timeout: int | None = Field(None, ge=30, le=600)  # 30s to 10min
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
    """Request to learn patterns for a group or multiple groups."""

    group_id: int | None = None  # Single group (legacy)
    group_ids: list[int] | None = None  # Multiple groups


class LearnPatternsGroupResult(BaseModel):
    """Result for a single group's pattern learning."""

    group_id: int
    group_name: str
    success: bool
    patterns_learned: int
    patterns: list[PatternResponse]
    coverage_percent: float
    error: str | None = None


class LearnPatternsResponse(BaseModel):
    """Response from pattern learning."""

    success: bool
    group_id: int  # For backwards compatibility (first group)
    group_name: str  # For backwards compatibility (first group)
    patterns_learned: int  # Total across all groups
    patterns: list[PatternResponse]  # All patterns
    coverage_percent: float  # Average coverage
    error: str | None = None
    # New: per-group results
    group_results: list[LearnPatternsGroupResult] | None = None


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


class PatternUpdate(BaseModel):
    """Update pattern request."""

    regex: str | None = None
    description: str | None = None


@router.put("/patterns/{pattern_id}", response_model=PatternResponse)
def update_pattern(pattern_id: str, request: PatternUpdate):
    """Update a learned pattern (regex or description)."""
    import re
    from teamarr.database.ai_patterns import get_all_patterns

    # Validate regex if provided
    if request.regex:
        try:
            re.compile(request.regex)
        except re.error as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid regex: {e}",
            )

    with get_db() as conn:
        # Find the pattern
        patterns = get_all_patterns(conn)
        pattern = next((p for p in patterns if p["pattern_id"] == pattern_id), None)

        if not pattern:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Pattern {pattern_id} not found",
            )

        # Build update query
        updates = []
        values = []
        if request.regex is not None:
            updates.append("regex = ?")
            values.append(request.regex)
        if request.description is not None:
            updates.append("description = ?")
            values.append(request.description)

        if updates:
            from datetime import datetime, timezone
            updates.append("updated_at = ?")
            values.append(datetime.now(timezone.utc).isoformat())
            values.append(pattern_id)

            conn.execute(
                f"UPDATE ai_patterns SET {', '.join(updates)} WHERE pattern_id = ?",
                values,
            )
            conn.commit()

        # Fetch updated pattern
        patterns = get_all_patterns(conn)
        updated = next((p for p in patterns if p["pattern_id"] == pattern_id), pattern)

    logger.info("[AI] Updated pattern: %s", pattern_id)

    return PatternResponse(
        pattern_id=updated["pattern_id"],
        regex=updated["regex"],
        description=updated.get("description", ""),
        example_streams=updated.get("example_streams", []),
        field_map=updated.get("field_map", {}),
        confidence=updated.get("confidence", 0.0),
        match_count=updated.get("match_count", 0),
        fail_count=updated.get("fail_count", 0),
        group_id=updated.get("group_id"),
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


def _learn_patterns_for_single_group(
    group_id: int,
    settings,
    dispatcharr_conn,
) -> LearnPatternsGroupResult:
    """Learn patterns for a single group. Returns result dict."""
    from teamarr.database.ai_patterns import get_patterns_for_group
    from teamarr.database.groups import get_group
    from teamarr.services.ai import AIClassifier, AIClassifierConfig

    with get_db() as conn:
        group = get_group(conn, group_id)
        if not group:
            return LearnPatternsGroupResult(
                group_id=group_id,
                group_name=f"Unknown ({group_id})",
                success=False,
                patterns_learned=0,
                patterns=[],
                coverage_percent=0.0,
                error=f"Group {group_id} not found",
            )

    # Fetch streams
    streams = dispatcharr_conn.m3u.list_streams(
        group_id=group.m3u_group_id,
        account_id=group.m3u_account_id,
    )

    if not streams:
        return LearnPatternsGroupResult(
            group_id=group_id,
            group_name=group.name,
            success=False,
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
            group_id=group_id,
        )
        classifier = AIClassifier(config)

        try:
            learned = classifier.learn_patterns_for_group(stream_names, group_id)

            # Calculate coverage
            covered = 0
            for name in stream_names:
                for pattern in learned:
                    if pattern.matches(name):
                        covered += 1
                        break

            coverage = (covered / len(stream_names)) * 100 if stream_names else 0

            # Get saved patterns from DB
            saved_patterns = get_patterns_for_group(db_conn, group_id)

            logger.info(
                "[AI] Learned %d patterns for group %d (%s) - %.1f%% coverage",
                len(learned),
                group_id,
                group.name,
                coverage,
            )

            return LearnPatternsGroupResult(
                group_id=group_id,
                group_name=group.name,
                success=True,
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
            logger.exception("[AI] Pattern learning failed for group %d: %s", group_id, e)
            return LearnPatternsGroupResult(
                group_id=group_id,
                group_name=group.name,
                success=False,
                patterns_learned=0,
                patterns=[],
                coverage_percent=0.0,
                error=str(e),
            )
        finally:
            classifier.close()


@router.post("/learn", response_model=LearnPatternsResponse)
def learn_patterns(request: LearnPatternsRequest):
    """Learn regex patterns from group streams.

    Supports both single group (group_id) and multiple groups (group_ids).
    Fetches streams from Dispatcharr for specified groups,
    analyzes their format patterns using AI, and saves learned
    regex patterns to the database for fast future matching.
    """
    from teamarr.database.settings import get_ai_settings
    from teamarr.dispatcharr import get_factory

    # Determine which groups to process
    group_ids = []
    if request.group_ids:
        group_ids = request.group_ids
    elif request.group_id:
        group_ids = [request.group_id]
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either group_id or group_ids must be provided",
        )

    with get_db() as conn:
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

    # Get Dispatcharr connection
    factory = get_factory(get_db)
    if not factory:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dispatcharr not configured",
        )

    dispatcharr_conn = factory.get_connection()
    if not dispatcharr_conn or not dispatcharr_conn.m3u:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dispatcharr connection failed",
        )

    # Process each group
    group_results: list[LearnPatternsGroupResult] = []
    for gid in group_ids:
        result = _learn_patterns_for_single_group(gid, settings, dispatcharr_conn)
        group_results.append(result)

    # Aggregate results
    total_patterns = sum(r.patterns_learned for r in group_results)
    all_patterns = [p for r in group_results for p in r.patterns]
    successful = [r for r in group_results if r.success]
    avg_coverage = sum(r.coverage_percent for r in successful) / len(successful) if successful else 0.0
    overall_success = len(successful) > 0

    # Use first group for backwards compatibility
    first_result = group_results[0] if group_results else None

    return LearnPatternsResponse(
        success=overall_success,
        group_id=first_result.group_id if first_result else 0,
        group_name=first_result.group_name if first_result else "",
        patterns_learned=total_patterns,
        patterns=all_patterns,
        coverage_percent=avg_coverage,
        error=None if overall_success else "Some groups failed",
        group_results=group_results if len(group_ids) > 1 else None,
    )


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
        timeout=float(settings.timeout),
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


# =============================================================================
# TEST REGEX PATTERN
# =============================================================================


class TestRegexRequest(BaseModel):
    """Request to test a regex pattern against streams."""

    regex: str
    streams: list[str] = Field(..., min_length=1, max_length=100)


class RegexMatchResult(BaseModel):
    """Result of testing regex against a stream."""

    stream: str
    matched: bool
    groups: dict[str, str] = {}


class TestRegexResponse(BaseModel):
    """Response from regex testing."""

    success: bool
    valid_regex: bool
    matches: int
    total: int
    results: list[RegexMatchResult]
    error: str | None = None


@router.post("/test-regex", response_model=TestRegexResponse)
def test_regex(request: TestRegexRequest):
    """Test a regex pattern against provided streams.

    Useful for verifying/editing patterns before saving.
    """
    import re

    # Validate regex
    try:
        pattern = re.compile(request.regex, re.IGNORECASE)
    except re.error as e:
        return TestRegexResponse(
            success=False,
            valid_regex=False,
            matches=0,
            total=len(request.streams),
            results=[],
            error=f"Invalid regex: {e}",
        )

    results = []
    matches = 0

    for stream in request.streams:
        match = pattern.search(stream)
        if match:
            matches += 1
            results.append(
                RegexMatchResult(
                    stream=stream,
                    matched=True,
                    groups=match.groupdict(),
                )
            )
        else:
            results.append(
                RegexMatchResult(
                    stream=stream,
                    matched=False,
                    groups={},
                )
            )

    return TestRegexResponse(
        success=True,
        valid_regex=True,
        matches=matches,
        total=len(request.streams),
        results=results,
    )
