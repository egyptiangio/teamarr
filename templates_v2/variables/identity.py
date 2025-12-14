"""Identity variables: team names, league, sport.

These variables identify teams and the competition context.
Most are BASE_ONLY since they don't change between games.
"""

from templates_v2.context import GameContext, TemplateContext
from templates_v2.variables.registry import (
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
    description="League code (e.g., 'nfl', 'nba')",
)
def extract_league(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    return ctx.team_config.league or ""


@register_variable(
    name="league_name",
    category=Category.IDENTITY,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="League display name (e.g., 'NFL', 'NBA')",
)
def extract_league_name(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    return ctx.team_config.league_name or ctx.team_config.league.upper()


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
    description="League identifier (e.g., 'nfl', 'eng.1')",
)
def extract_league_id(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
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
    name="head_coach",
    category=Category.IDENTITY,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Team's head coach name",
)
def extract_head_coach(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if game_ctx:
        return game_ctx.head_coach
    return ""


@register_variable(
    name="gracenote_category",
    category=Category.IDENTITY,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Gracenote category for EPG (e.g., 'Sports event')",
)
def extract_gracenote_category(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    return "Sports event"
