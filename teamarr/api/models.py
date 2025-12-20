"""Pydantic models for API requests and responses."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

# =============================================================================
# Teams
# =============================================================================


class TeamCreate(BaseModel):
    """Request body for creating a team."""

    provider: str = "espn"
    provider_team_id: str
    league: str
    sport: str
    team_name: str
    team_abbrev: str | None = None
    team_logo_url: str | None = None
    team_color: str | None = None
    channel_id: str
    channel_logo_url: str | None = None
    template_id: int | None = None
    active: bool = True


class TeamUpdate(BaseModel):
    """Request body for updating a team."""

    team_name: str | None = None
    team_abbrev: str | None = None
    team_logo_url: str | None = None
    team_color: str | None = None
    channel_id: str | None = None
    channel_logo_url: str | None = None
    template_id: int | None = None
    active: bool | None = None


class TeamResponse(BaseModel):
    """Response body for a team."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    provider: str
    provider_team_id: str
    league: str
    sport: str
    team_name: str
    team_abbrev: str | None
    team_logo_url: str | None
    team_color: str | None
    channel_id: str
    channel_logo_url: str | None
    template_id: int | None
    active: bool
    created_at: datetime
    updated_at: datetime


# =============================================================================
# Templates
# =============================================================================


class PregamePeriod(BaseModel):
    """A pregame filler period."""

    start_hours_before: float
    end_hours_before: float
    title: str
    description: str


class PostgamePeriod(BaseModel):
    """A postgame filler period."""

    start_hours_after: float
    end_hours_after: float
    title: str
    description: str


class FillerFallback(BaseModel):
    """Fallback content for filler."""

    title: str
    subtitle: str | None = None
    description: str
    art_url: str | None = None


class ConditionalContent(BaseModel):
    """Conditional description settings."""

    enabled: bool = False
    description_final: str | None = None
    description_not_final: str | None = None


class IdleOffseasonContent(BaseModel):
    """Offseason content settings (no game in 30-day lookahead).

    Each field (title, subtitle, description) can be independently enabled
    to override the default idle content when there's no upcoming game.
    """

    title_enabled: bool = False
    title: str | None = None
    subtitle_enabled: bool = False
    subtitle: str | None = None
    description_enabled: bool = False
    description: str | None = "No upcoming {team_name} games scheduled."


class ConditionalDescriptionEntry(BaseModel):
    """A conditional description entry."""

    condition: str
    condition_value: str | None = None
    template: str
    priority: int = 50


class TemplateCreate(BaseModel):
    """Request body for creating a template."""

    name: str
    template_type: str = "team"
    sport: str | None = None
    league: str | None = None

    # Programme formatting
    title_format: str = "{team_name} {sport}"
    subtitle_template: str | None = "{venue_full}"
    description_template: str | None = "{matchup} | {venue_full}"
    program_art_url: str | None = None

    # Game duration
    game_duration_mode: str = "sport"
    game_duration_override: float | None = None

    # XMLTV metadata
    xmltv_flags: dict | None = None
    xmltv_categories: list[str] | None = None
    categories_apply_to: str = "events"

    # Filler: Pregame
    pregame_enabled: bool = True
    pregame_periods: list[PregamePeriod] | None = None
    pregame_fallback: FillerFallback | None = None

    # Filler: Postgame
    postgame_enabled: bool = True
    postgame_periods: list[PostgamePeriod] | None = None
    postgame_fallback: FillerFallback | None = None
    postgame_conditional: ConditionalContent | None = None

    # Filler: Idle
    idle_enabled: bool = True
    idle_content: FillerFallback | None = None
    idle_conditional: ConditionalContent | None = None
    idle_offseason: IdleOffseasonContent | None = None

    # Conditional descriptions
    conditional_descriptions: list[ConditionalDescriptionEntry] | None = None

    # Event template specific
    event_channel_name: str | None = None
    event_channel_logo_url: str | None = None


class TemplateUpdate(BaseModel):
    """Request body for updating a template."""

    name: str | None = None
    sport: str | None = None
    league: str | None = None
    title_format: str | None = None
    subtitle_template: str | None = None
    description_template: str | None = None
    program_art_url: str | None = None
    game_duration_mode: str | None = None
    game_duration_override: float | None = None

    # XMLTV metadata
    xmltv_flags: dict | None = None
    xmltv_categories: list[str] | None = None
    categories_apply_to: str | None = None

    # Filler toggles
    pregame_enabled: bool | None = None
    pregame_fallback: FillerFallback | None = None
    postgame_enabled: bool | None = None
    postgame_fallback: FillerFallback | None = None
    postgame_conditional: ConditionalContent | None = None
    idle_enabled: bool | None = None
    idle_content: FillerFallback | None = None
    idle_conditional: ConditionalContent | None = None
    idle_offseason: IdleOffseasonContent | None = None

    # Conditional descriptions
    conditional_descriptions: list[ConditionalDescriptionEntry] | None = None

    # Event template specific
    event_channel_name: str | None = None
    event_channel_logo_url: str | None = None


class TemplateResponse(BaseModel):
    """Response body for a template."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    template_type: str
    sport: str | None
    league: str | None
    title_format: str | None
    subtitle_template: str | None
    program_art_url: str | None
    game_duration_mode: str | None
    game_duration_override: float | None
    pregame_enabled: bool | None
    postgame_enabled: bool | None
    idle_enabled: bool | None
    created_at: datetime
    updated_at: datetime


class TemplateFullResponse(TemplateResponse):
    """Full template response including JSON fields."""

    xmltv_flags: dict | None = None
    xmltv_categories: list[str] | None = None
    categories_apply_to: str | None = None
    pregame_periods: list[dict] | None = None
    pregame_fallback: dict | None = None
    postgame_periods: list[dict] | None = None
    postgame_fallback: dict | None = None
    postgame_conditional: dict | None = None
    idle_content: dict | None = None
    idle_conditional: dict | None = None
    idle_offseason: dict | None = None
    conditional_descriptions: list[dict] | None = None
    event_channel_name: str | None = None
    event_channel_logo_url: str | None = None


# =============================================================================
# EPG
# =============================================================================


class EPGGenerateRequest(BaseModel):
    """Request body for team-based EPG generation."""

    team_ids: list[int] | None = None  # None = all active teams
    days_ahead: int | None = None  # None = use settings default


class EPGGenerateResponse(BaseModel):
    """Response body for EPG generation."""

    programmes_count: int
    teams_processed: int
    events_processed: int = 0
    duration_seconds: float


class EventEPGRequest(BaseModel):
    """Request body for event-based EPG generation."""

    leagues: list[str]
    target_date: str | None = None
    channel_prefix: str = "event"
    pregame_minutes: int = 30
    duration_hours: float = 3.0


class EventMatchRequest(BaseModel):
    """Request body for event matching."""

    league: str
    target_date: str | None = None  # YYYY-MM-DD
    team1_id: str | None = None
    team2_id: str | None = None
    team1_name: str | None = None
    team2_name: str | None = None


class EventMatchResponse(BaseModel):
    """Response body for event matching."""

    found: bool
    event_id: str | None = None
    event_name: str | None = None
    home_team: str | None = None
    away_team: str | None = None
    start_time: str | None = None
    venue: str | None = None


# =============================================================================
# Stream Matching (with fingerprint cache)
# =============================================================================


class StreamInput(BaseModel):
    """A stream to match."""

    id: int
    name: str


class StreamBatchMatchRequest(BaseModel):
    """Request for batch stream matching with cache."""

    group_id: int
    streams: list[StreamInput]
    search_leagues: list[str]
    include_leagues: list[str] | None = None
    target_date: str | None = None  # YYYY-MM-DD, defaults to today


class StreamMatchResultModel(BaseModel):
    """Result of matching a single stream."""

    stream_name: str
    matched: bool
    event_id: str | None = None
    event_name: str | None = None
    league: str | None = None
    home_team: str | None = None
    away_team: str | None = None
    start_time: str | None = None
    included: bool = False
    exclusion_reason: str | None = None
    from_cache: bool = False


class StreamBatchMatchResponse(BaseModel):
    """Response for batch stream matching."""

    total: int
    matched: int
    included: int
    unmatched: int
    match_rate: float
    cache_hits: int
    cache_misses: int
    cache_hit_rate: float
    results: list[StreamMatchResultModel]
