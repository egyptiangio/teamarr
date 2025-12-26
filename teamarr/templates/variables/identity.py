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
    """Convert team name to PascalCase for channel IDs."""
    return "".join(word.capitalize() for word in name.split())


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
    description="Team abbreviation (e.g., 'DET')",
)
def extract_team_abbrev(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    return ctx.team_config.team_abbrev or ""


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
    description="Opponent team abbreviation",
)
def extract_opponent_abbrev(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    opponent = _get_opponent(ctx, game_ctx)
    return opponent.abbreviation if opponent else ""


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
    description="Abbreviated matchup (e.g., 'TB @ DET')",
)
def extract_matchup_abbrev(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    event = game_ctx.event
    return f"{event.away_team.abbreviation} @ {event.home_team.abbreviation}"


@register_variable(
    name="league",
    category=Category.IDENTITY,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="League short code uppercase (e.g., 'NFL', 'NCAAM')",
)
def extract_league(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    """Return league short code uppercase.

    Fallback chain:
        1. Our alias from leagues.league_id_alias (uppercase)
        2. Raw league code (uppercase)

    Examples:
        mens-college-basketball → NCAAM (alias)
        eng.1 → EPL (alias)
        nfl → NFL (code, no alias needed)
    """
    from teamarr.database import get_db
    from teamarr.database.leagues import get_league_id

    with get_db() as conn:
        league_id = get_league_id(conn, ctx.team_config.league)
    return league_id.upper()


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
    """
    from teamarr.database import get_db

    league_code = ctx.team_config.league

    with get_db() as conn:
        # 1. Check our curated display_name in leagues table
        cursor = conn.execute(
            "SELECT display_name FROM leagues WHERE league_code = ?",
            (league_code,),
        )
        row = cursor.fetchone()
        if row and row["display_name"]:
            return row["display_name"]

        # 2. Fallback to API's league_name from league_cache
        cursor = conn.execute(
            "SELECT league_name FROM league_cache WHERE league_slug = ?",
            (league_code,),
        )
        row = cursor.fetchone()
        if row and row["league_name"]:
            return row["league_name"]

    # 3. Final fallback to league code uppercase
    return league_code.upper()


@register_variable(
    name="sport",
    category=Category.IDENTITY,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Sport name (e.g., 'football', 'basketball')",
)
def extract_sport(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    return ctx.team_config.sport or ""


@register_variable(
    name="sport_title",
    category=Category.IDENTITY,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Sport name title case (e.g., 'Football', 'Basketball')",
)
def extract_sport_title(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    sport = ctx.team_config.sport or ""
    return sport.title()


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
    description="League identifier - uses alias if configured (e.g., 'nfl', 'epl', 'ncaam')",
)
def extract_league_id(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    """Return league_id_alias if configured, otherwise league_code."""
    from teamarr.database import get_db
    from teamarr.database.leagues import get_league_id

    with get_db() as conn:
        return get_league_id(conn, ctx.team_config.league)


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
    name="league_slug",
    category=Category.IDENTITY,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="League as URL slug (e.g., 'nfl', 'eng-1')",
)
def extract_league_slug(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    return ctx.team_config.league.replace(".", "-").lower()


@register_variable(
    name="gracenote_category",
    category=Category.IDENTITY,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Gracenote category for EPG (e.g., 'Sports event')",
)
def extract_gracenote_category(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    return "Sports event"
