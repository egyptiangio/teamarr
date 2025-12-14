"""Statistics-related template variables.

Variables for scoring averages (PPG, PAPG), etc.
"""

from templates_v2.context import GameContext, TemplateContext
from templates_v2.variables.registry import (
    Category,
    SuffixRules,
    register_variable,
)


def _format_ppg(value: float | None) -> str:
    """Format points per game to one decimal place."""
    if value is None or value == 0:
        return ""
    return f"{value:.1f}"


@register_variable(
    name="team_ppg",
    category=Category.STATISTICS,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Team's points per game average",
)
def extract_team_ppg(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if ctx.team_stats:
        return _format_ppg(ctx.team_stats.ppg)
    return ""


@register_variable(
    name="team_papg",
    category=Category.STATISTICS,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Team's points allowed per game average",
)
def extract_team_papg(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if ctx.team_stats:
        return _format_ppg(ctx.team_stats.papg)
    return ""


@register_variable(
    name="opponent_ppg",
    category=Category.STATISTICS,
    suffix_rules=SuffixRules.ALL,
    description="Opponent's points per game average",
)
def extract_opponent_ppg(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if game_ctx and game_ctx.opponent_stats:
        return _format_ppg(game_ctx.opponent_stats.ppg)
    return ""


@register_variable(
    name="opponent_papg",
    category=Category.STATISTICS,
    suffix_rules=SuffixRules.ALL,
    description="Opponent's points allowed per game average",
)
def extract_opponent_papg(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if game_ctx and game_ctx.opponent_stats:
        return _format_ppg(game_ctx.opponent_stats.papg)
    return ""


@register_variable(
    name="home_team_ppg",
    category=Category.STATISTICS,
    suffix_rules=SuffixRules.ALL,
    description="Home team's PPG for this game",
)
def extract_home_team_ppg(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    event = game_ctx.event
    is_home = event.home_team.id == ctx.team_config.team_id
    if is_home and ctx.team_stats:
        return _format_ppg(ctx.team_stats.ppg)
    elif not is_home and game_ctx.opponent_stats:
        return _format_ppg(game_ctx.opponent_stats.ppg)
    return ""


@register_variable(
    name="away_team_ppg",
    category=Category.STATISTICS,
    suffix_rules=SuffixRules.ALL,
    description="Away team's PPG for this game",
)
def extract_away_team_ppg(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    event = game_ctx.event
    is_home = event.home_team.id == ctx.team_config.team_id
    if not is_home and ctx.team_stats:
        return _format_ppg(ctx.team_stats.ppg)
    elif is_home and game_ctx.opponent_stats:
        return _format_ppg(game_ctx.opponent_stats.ppg)
    return ""
