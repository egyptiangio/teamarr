"""AI/Ollama integration endpoints.

Provides REST API for:
- AI service status and health checks
- Pattern learning for event groups (with background task support)
- AI settings management
- Pattern management (list, delete)
"""

import logging
import threading
import time

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from pydantic import BaseModel, Field

from teamarr.api import pattern_learning_status as pls
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


# Provider configuration models
class OllamaProviderConfig(BaseModel):
    """Ollama provider configuration."""
    enabled: bool = False
    url: str = "http://localhost:11434"
    model: str = "qwen2.5:7b"
    timeout: int = Field(180, ge=30, le=600)


class OpenAIProviderConfig(BaseModel):
    """OpenAI provider configuration."""
    enabled: bool = False
    api_key: str = ""
    model: str = "gpt-4o-mini"
    timeout: int = Field(60, ge=30, le=300)
    organization: str = ""


class AnthropicProviderConfig(BaseModel):
    """Anthropic provider configuration."""
    enabled: bool = False
    api_key: str = ""
    model: str = "claude-3-5-sonnet-20241022"
    timeout: int = Field(60, ge=30, le=300)


class GrokProviderConfig(BaseModel):
    """Grok provider configuration."""
    enabled: bool = False
    api_key: str = ""
    model: str = "grok-2-latest"
    timeout: int = Field(60, ge=30, le=300)


class GroqProviderConfig(BaseModel):
    """Groq (fast inference) provider configuration - FREE TIER."""
    enabled: bool = False
    api_key: str = ""
    model: str = "llama-3.1-8b-instant"
    timeout: int = Field(60, ge=30, le=300)


class GeminiProviderConfig(BaseModel):
    """Google Gemini provider configuration - FREE TIER."""
    enabled: bool = False
    api_key: str = ""
    model: str = "gemini-1.5-flash"
    timeout: int = Field(60, ge=30, le=300)


class OpenRouterProviderConfig(BaseModel):
    """OpenRouter provider configuration - FREE TIER available."""
    enabled: bool = False
    api_key: str = ""
    model: str = "meta-llama/llama-3.1-8b-instruct:free"
    timeout: int = Field(60, ge=30, le=300)
    site_url: str = ""
    app_name: str = "Teamarr"


class AITaskAssignmentsConfig(BaseModel):
    """Task-to-provider assignments."""
    pattern_learning: str = "ollama"
    stream_parsing: str = "ollama"
    event_cards: str = "ollama"
    team_matching: str = "ollama"
    description_gen: str = "ollama"


class AIProvidersConfig(BaseModel):
    """All provider configurations."""
    ollama: OllamaProviderConfig = Field(default_factory=OllamaProviderConfig)
    openai: OpenAIProviderConfig = Field(default_factory=OpenAIProviderConfig)
    anthropic: AnthropicProviderConfig = Field(default_factory=AnthropicProviderConfig)
    grok: GrokProviderConfig = Field(default_factory=GrokProviderConfig)
    # Free tier providers
    groq: GroqProviderConfig = Field(default_factory=GroqProviderConfig)
    gemini: GeminiProviderConfig = Field(default_factory=GeminiProviderConfig)
    openrouter: OpenRouterProviderConfig = Field(default_factory=OpenRouterProviderConfig)


class AISettingsResponse(BaseModel):
    """AI settings with multi-provider support."""

    # Master toggle
    enabled: bool

    # Provider configurations
    providers: AIProvidersConfig

    # Task assignments
    task_assignments: AITaskAssignmentsConfig

    # General settings
    batch_size: int
    learn_patterns: bool
    fallback_to_regex: bool

    # Legacy fields for backwards compatibility
    ollama_url: str  # Maps to providers.ollama.url
    model: str  # Maps to providers.ollama.model
    timeout: int  # Maps to providers.ollama.timeout
    use_for_parsing: bool  # Derived from enabled + ollama.enabled
    use_for_matching: bool  # Currently unused


class AISettingsUpdate(BaseModel):
    """Update AI settings request - supports both legacy and new format."""

    # Master toggle
    enabled: bool | None = None

    # Provider configurations (new format)
    providers: AIProvidersConfig | None = None

    # Task assignments (new format)
    task_assignments: AITaskAssignmentsConfig | None = None

    # General settings
    batch_size: int | None = Field(None, ge=1, le=50)
    learn_patterns: bool | None = None
    fallback_to_regex: bool | None = None

    # Legacy fields (for backwards compatibility)
    ollama_url: str | None = None
    model: str | None = None
    timeout: int | None = Field(None, ge=30, le=600)
    use_for_parsing: bool | None = None
    use_for_matching: bool | None = None


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


def _settings_to_response(settings) -> AISettingsResponse:
    """Convert AISettings to response model."""
    return AISettingsResponse(
        enabled=settings.enabled,
        providers=AIProvidersConfig(
            ollama=OllamaProviderConfig(
                enabled=settings.ollama.enabled,
                url=settings.ollama.url,
                model=settings.ollama.model,
                timeout=settings.ollama.timeout,
            ),
            openai=OpenAIProviderConfig(
                enabled=settings.openai.enabled,
                api_key=settings.openai.api_key,
                model=settings.openai.model,
                timeout=settings.openai.timeout,
                organization=settings.openai.organization,
            ),
            anthropic=AnthropicProviderConfig(
                enabled=settings.anthropic.enabled,
                api_key=settings.anthropic.api_key,
                model=settings.anthropic.model,
                timeout=settings.anthropic.timeout,
            ),
            grok=GrokProviderConfig(
                enabled=settings.grok.enabled,
                api_key=settings.grok.api_key,
                model=settings.grok.model,
                timeout=settings.grok.timeout,
            ),
            groq=GroqProviderConfig(
                enabled=settings.groq.enabled,
                api_key=settings.groq.api_key,
                model=settings.groq.model,
                timeout=settings.groq.timeout,
            ),
            gemini=GeminiProviderConfig(
                enabled=settings.gemini.enabled,
                api_key=settings.gemini.api_key,
                model=settings.gemini.model,
                timeout=settings.gemini.timeout,
            ),
            openrouter=OpenRouterProviderConfig(
                enabled=settings.openrouter.enabled,
                api_key=settings.openrouter.api_key,
                model=settings.openrouter.model,
                timeout=settings.openrouter.timeout,
                site_url=settings.openrouter.site_url,
                app_name=settings.openrouter.app_name,
            ),
        ),
        task_assignments=AITaskAssignmentsConfig(
            pattern_learning=settings.task_assignments.pattern_learning,
            stream_parsing=settings.task_assignments.stream_parsing,
            event_cards=settings.task_assignments.event_cards,
            team_matching=settings.task_assignments.team_matching,
            description_gen=settings.task_assignments.description_gen,
        ),
        batch_size=settings.batch_size,
        learn_patterns=settings.learn_patterns,
        fallback_to_regex=settings.fallback_to_regex,
        # Legacy fields
        ollama_url=settings.ollama.url,
        model=settings.ollama.model,
        timeout=settings.ollama.timeout,
        use_for_parsing=settings.enabled and settings.ollama.enabled,
        use_for_matching=False,
    )


@router.get("/settings", response_model=AISettingsResponse)
def get_settings():
    """Get current AI settings with multi-provider support."""
    from teamarr.database.settings import get_ai_settings

    with get_db() as conn:
        settings = get_ai_settings(conn)

    return _settings_to_response(settings)


@router.put("/settings", response_model=AISettingsResponse)
def update_settings(request: AISettingsUpdate):
    """Update AI settings - supports both legacy and new provider format."""
    from teamarr.database.settings import get_ai_settings, update_ai_settings

    with get_db() as conn:
        # Prepare update kwargs
        update_kwargs = {}

        # Handle master toggle
        if request.enabled is not None:
            update_kwargs["enabled"] = request.enabled

        # Handle general settings
        if request.batch_size is not None:
            update_kwargs["batch_size"] = request.batch_size
        if request.learn_patterns is not None:
            update_kwargs["learn_patterns"] = request.learn_patterns
        if request.fallback_to_regex is not None:
            update_kwargs["fallback_to_regex"] = request.fallback_to_regex

        # Handle legacy fields (map to ollama)
        if request.ollama_url is not None:
            update_kwargs["ollama_url"] = request.ollama_url
        if request.model is not None:
            update_kwargs["model"] = request.model
        if request.timeout is not None:
            update_kwargs["timeout"] = request.timeout

        # Handle new provider configs
        if request.providers is not None:
            providers_config = {
                "ollama": request.providers.ollama.model_dump(),
                "openai": request.providers.openai.model_dump(),
                "anthropic": request.providers.anthropic.model_dump(),
                "grok": request.providers.grok.model_dump(),
                "groq": request.providers.groq.model_dump(),
                "gemini": request.providers.gemini.model_dump(),
                "openrouter": request.providers.openrouter.model_dump(),
            }
            update_kwargs["providers_config"] = providers_config

            # Also sync legacy fields from ollama config
            update_kwargs["ollama_url"] = request.providers.ollama.url
            update_kwargs["model"] = request.providers.ollama.model
            update_kwargs["timeout"] = request.providers.ollama.timeout

        # Handle task assignments
        if request.task_assignments is not None:
            update_kwargs["task_assignments"] = request.task_assignments.model_dump()

        if update_kwargs:
            update_ai_settings(conn, **update_kwargs)

        settings = get_ai_settings(conn)

    logger.info("[AI] Settings updated: %s", list(update_kwargs.keys()))

    return _settings_to_response(settings)


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

    # Process each group with delay to avoid rate limits
    group_results: list[LearnPatternsGroupResult] = []
    for i, gid in enumerate(group_ids):
        # Add delay between groups to avoid rate limits (skip first)
        if i > 0:
            time.sleep(2.0)
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
# BACKGROUND PATTERN LEARNING
# =============================================================================


class PatternLearningStatusResponse(BaseModel):
    """Status of background pattern learning task."""

    in_progress: bool
    status: str
    message: str
    percent: int
    current_group: int
    total_groups: int
    current_group_name: str
    started_at: str | None
    completed_at: str | None
    error: str | None
    eta_seconds: int | None
    groups_completed: int
    patterns_learned: int
    avg_coverage: float
    group_results: list[dict] = Field(default_factory=list)


def _run_pattern_learning_task(group_ids: list[int], settings, dispatcharr_conn) -> None:
    """Background task to learn patterns for multiple groups."""
    try:
        for i, gid in enumerate(group_ids):
            # Check for abort
            if pls.is_abort_requested():
                logger.info("[AI] Pattern learning aborted by user")
                pls.abort_learning()
                return

            # Add delay between groups to avoid rate limits (skip first)
            if i > 0:
                time.sleep(2.0)

            # Get group name for progress display
            with get_db() as conn:
                from teamarr.database.groups import get_group
                group = get_group(conn, gid)
                group_name = group.name if group else f"Group {gid}"

            pls.update_progress(
                current_group=i + 1,
                group_name=group_name,
                message=f"Learning patterns for {group_name}...",
            )

            # Learn patterns for this group
            result = _learn_patterns_for_single_group(gid, settings, dispatcharr_conn)

            # Add result
            pls.add_group_result({
                "group_id": result.group_id,
                "group_name": result.group_name,
                "success": result.success,
                "patterns_learned": result.patterns_learned,
                "coverage_percent": result.coverage_percent,
                "error": result.error,
            })

        pls.complete_learning()

    except Exception as e:
        logger.exception("[AI] Pattern learning task failed: %s", e)
        pls.fail_learning(str(e))


@router.post("/learn/start")
def start_pattern_learning(request: LearnPatternsRequest, background_tasks: BackgroundTasks):
    """Start pattern learning as a background task.

    Returns immediately with task status. Poll /learn/status for progress.
    """
    from teamarr.database.settings import get_ai_settings
    from teamarr.dispatcharr import get_factory

    # Check if already in progress
    if pls.is_in_progress():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Pattern learning already in progress",
        )

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

    # Start the background task
    if not pls.start_learning(len(group_ids)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Pattern learning already in progress",
        )

    # Run in a thread (BackgroundTasks runs after response, but we want true parallel)
    thread = threading.Thread(
        target=_run_pattern_learning_task,
        args=(group_ids, settings, dispatcharr_conn),
        daemon=True,
    )
    thread.start()

    return {"success": True, "message": f"Started learning patterns for {len(group_ids)} groups"}


@router.get("/learn/status", response_model=PatternLearningStatusResponse)
def get_pattern_learning_status():
    """Get current pattern learning status."""
    return pls.get_status()


@router.post("/learn/abort")
def abort_pattern_learning():
    """Request abort of pattern learning task."""
    if pls.request_abort():
        return {"success": True, "message": "Abort requested"}
    return {"success": False, "message": "No pattern learning in progress"}


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
            # Filter out None values from groupdict (optional groups that didn't match)
            groups = {k: v for k, v in match.groupdict().items() if v is not None}
            results.append(
                RegexMatchResult(
                    stream=stream,
                    matched=True,
                    groups=groups,
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
