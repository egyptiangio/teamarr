"""Identity variables: team names, league, sport.

These variables identify teams and the competition context.
Most are BASE_ONLY since they don't change between games.
"""

from teamarr.templates.context import GameContext, TemplateContext
from teamarr.templates.variables.registry import (
    Category,
    SuffixRules,
    register_variable,
)


def _to_pascal_case(name: str) -> str:
    """Convert team name to PascalCase for channel IDs.

    Strips non-alphanumeric characters and normalizes accents.
    Examples:
        "Detroit Lions" → "DetroitLions"
        "D.C. United" → "DcUnited"
        "Atlético Madrid" → "AtleticoMadrid"
    """
    import re
    import unicodedata

    # Normalize unicode (é → e)
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    # Keep only alphanumeric, split on non-alpha
    words = re.split(r"[^a-zA-Z0-9]+", ascii_name)
    return "".join(word.capitalize() for word in words if word)


def _get_opponent(ctx: TemplateContext, game_ctx: GameContext | None):
    """Helper to get opponent team from game context."""
    if not game_ctx or not game_ctx.event:
        return None
    event = game_ctx.event
    is_home = event.home_team.id == ctx.team_config.team_id
    return event.away_team if is_home else event.home_team


@register_variable(
    name="team_name",
    category=Category.IDENTITY,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Team display name (e.g., 'Detroit Lions')",
)
def extract_team_name(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    return ctx.team_config.team_name or ""


@register_variable(
    name="team_abbrev",
    category=Category.IDENTITY,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Team abbreviation uppercase (e.g., 'DET')",
)
def extract_team_abbrev(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    abbrev = ctx.team_config.team_abbrev or ""
    return abbrev.upper()


@register_variable(
    name="team_abbrev_lower",
    category=Category.IDENTITY,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Team abbreviation lowercase (e.g., 'det')",
)
def extract_team_abbrev_lower(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    abbrev = ctx.team_config.team_abbrev or ""
    return abbrev.lower()


@register_variable(
    name="team_name_pascal",
    category=Category.IDENTITY,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Team name in PascalCase for channel IDs (e.g., 'DetroitLions')",
)
def extract_team_name_pascal(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    return _to_pascal_case(ctx.team_config.team_name or "")


@register_variable(
    name="opponent",
    category=Category.IDENTITY,
    suffix_rules=SuffixRules.ALL,
    description="Opponent team name",
)
def extract_opponent(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    opponent = _get_opponent(ctx, game_ctx)
    return opponent.name if opponent else ""


@register_variable(
    name="opponent_abbrev",
    category=Category.IDENTITY,
    suffix_rules=SuffixRules.ALL,
    description="Opponent team abbreviation uppercase",
)
def extract_opponent_abbrev(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    opponent = _get_opponent(ctx, game_ctx)
    return opponent.abbreviation.upper() if opponent else ""


@register_variable(
    name="opponent_abbrev_lower",
    category=Category.IDENTITY,
    suffix_rules=SuffixRules.ALL,
    description="Opponent abbreviation lowercase",
)
def extract_opponent_abbrev_lower(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    opponent = _get_opponent(ctx, game_ctx)
    return opponent.abbreviation.lower() if opponent else ""


@register_variable(
    name="matchup",
    category=Category.IDENTITY,
    suffix_rules=SuffixRules.ALL,
    description="Full matchup string (e.g., 'Tampa Bay @ Detroit')",
)
def extract_matchup(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    event = game_ctx.event
    return f"{event.away_team.name} @ {event.home_team.name}"


@register_variable(
    name="matchup_abbrev",
    category=Category.IDENTITY,
    suffix_rules=SuffixRules.ALL,
    description="Abbreviated matchup uppercase (e.g., 'TB @ DET')",
)
def extract_matchup_abbrev(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    event = game_ctx.event
    return f"{event.away_team.abbreviation.upper()} @ {event.home_team.abbreviation.upper()}"


@register_variable(
    name="league",
    category=Category.IDENTITY,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="League short alias (e.g., 'NFL', 'EPL', 'UCL', 'La Liga')",
)
def extract_league(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    """Return league short alias for display.

    Fallback chain:
        1. league_alias from leagues table (e.g., 'EPL', 'UCL')
        2. display_name from leagues table (e.g., 'NFL', 'La Liga')
        3. league_code uppercase

    Examples:
        eng.1 → EPL (has league_alias)
        uefa.champions → UCL (has league_alias)
        nfl → NFL (display_name already short)
        ger.1 → Bundesliga (display_name already short)

    THREAD-SAFE: Uses in-memory cache, no DB access.
    """
    from teamarr.services.league_mappings import get_league_mapping_service

    service = get_league_mapping_service()
    return service.get_league_alias(ctx.team_config.league)


@register_variable(
    name="league_name",
    category=Category.IDENTITY,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="League full display name (e.g., 'NFL', 'NCAA Men's Basketball')",
)
def extract_league_name(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    """Return league full display name.

    Fallback chain:
        1. Our display_name from leagues table
        2. API's league_name from league_cache table
        3. Raw league code (uppercase)

    Examples:
        nfl → NFL
        mens-college-basketball → NCAA Men's Basketball
        eng.1 → English Premier League

    THREAD-SAFE: Uses in-memory cache, no DB access.
    """
    from teamarr.services.league_mappings import get_league_mapping_service

    service = get_league_mapping_service()
    return service.get_league_display_name(ctx.team_config.league)


@register_variable(
    name="sport",
    category=Category.IDENTITY,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Sport display name (e.g., 'Football', 'MMA')",
)
def extract_sport(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    """Return sport display name with proper casing.

    Uses sports table for display names (handles special cases like 'MMA').
    Falls back to title case if sport not in table.

    THREAD-SAFE: Uses in-memory cache, no DB access.
    """
    sport_code = ctx.team_config.sport
    if not sport_code:
        return ""

    from teamarr.services.league_mappings import get_league_mapping_service

    service = get_league_mapping_service()
    return service.get_sport_display_name(sport_code)


@register_variable(
    name="sport_lower",
    category=Category.IDENTITY,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Sport in lowercase (e.g., 'football')",
)
def extract_sport_lower(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    sport = ctx.team_config.sport or ""
    return sport.lower()


@register_variable(
    name="league_id",
    category=Category.IDENTITY,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="League identifier for URLs (e.g., 'nfl', 'epl', 'ncaabb')",
)
def extract_league_id(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    """Return league_id for URL construction.

    Always lowercase - stored that way in DB.

    Examples:
        nfl → nfl
        college-baseball → ncaabb
        college-softball → ncaasbw
        eng.1 → epl
        ger.1 → bundesliga

    THREAD-SAFE: Uses in-memory cache, no DB access.
    """
    from teamarr.services.league_mappings import get_league_mapping_service

    service = get_league_mapping_service()
    return service.get_league_id(ctx.team_config.league)


@register_variable(
    name="league_code",
    category=Category.IDENTITY,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Raw league code (e.g., 'nfl', 'mens-college-basketball', 'eng.1')",
)
def extract_league_code(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    """Return raw league_code, ignoring any alias."""
    return ctx.team_config.league


@register_variable(
    name="gracenote_category",
    category=Category.IDENTITY,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Gracenote category for EPG (e.g., 'NFL Football', 'College Basketball')",
)
def extract_gracenote_category(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    """Return Gracenote-compatible category.

    Fallback chain:
        1. gracenote_category from leagues table (curated value)
        2. Auto-generated: "{display_name} {Sport}" (e.g., 'NFL Football')

    Examples:
        nfl → NFL Football
        mens-college-basketball → College Basketball (if curated)
        eng.1 → English Premier League Soccer

    THREAD-SAFE: Uses in-memory cache, no DB access.
    """
    from teamarr.services.league_mappings import get_league_mapping_service

    service = get_league_mapping_service()
    return service.get_gracenote_category(ctx.team_config.league)


@register_variable(
    name="exception_keyword",
    category=Category.IDENTITY,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Exception keyword label (e.g., 'Spanish', '4K', 'Manningcast') - set at channel creation",  # noqa: E501
)
def extract_exception_keyword(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    """Return exception keyword label for channel naming.

    This variable is special - it's populated via extra_vars at channel creation
    time, not extracted from event data. The extractor returns empty string
    as a placeholder; actual values are injected by the lifecycle service.

    Used in channel name templates like:
        "{away_team} @ {home_team} ({exception_keyword})"
        "{exception_keyword}: {matchup}"

    Examples:
        Spanish, French, 4K, Manningcast
    """
    # Value is injected via extra_vars in lifecycle service
    # This extractor exists for validation and UI display only
    return ""
