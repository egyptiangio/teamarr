"""Ranking-related template variables.

Variables for college sports rankings (AP Top 25, etc.).
"""

from teamarr.templates.context import GameContext, TemplateContext
from teamarr.templates.variables.registry import (
    Category,
    SuffixRules,
    register_variable,
)


@register_variable(
    name="team_rank",
    category=Category.RANKINGS,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Team's ranking (e.g., '5' for #5, empty if unranked)",
)
def extract_team_rank(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if ctx.team_stats and ctx.team_stats.rank:
        return str(ctx.team_stats.rank)
    return ""


@register_variable(
    name="team_rank_display",
    category=Category.RANKINGS,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Team's ranking with # prefix (e.g., '#5')",
)
def extract_team_rank_display(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if ctx.team_stats and ctx.team_stats.rank:
        return f"#{ctx.team_stats.rank}"
    return ""


@register_variable(
    name="opponent_rank",
    category=Category.RANKINGS,
    suffix_rules=SuffixRules.ALL,
    description="Opponent's ranking",
)
def extract_opponent_rank(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if game_ctx and game_ctx.opponent_stats and game_ctx.opponent_stats.rank:
        return str(game_ctx.opponent_stats.rank)
    return ""


@register_variable(
    name="opponent_rank_display",
    category=Category.RANKINGS,
    suffix_rules=SuffixRules.ALL,
    description="Opponent's ranking with # prefix",
)
def extract_opponent_rank_display(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if game_ctx and game_ctx.opponent_stats and game_ctx.opponent_stats.rank:
        return f"#{game_ctx.opponent_stats.rank}"
    return ""


@register_variable(
    name="is_ranked",
    category=Category.RANKINGS,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="'true' if team is ranked, empty otherwise",
)
def extract_is_ranked(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if ctx.team_stats and ctx.team_stats.rank:
        return "true"
    return ""


@register_variable(
    name="opponent_is_ranked",
    category=Category.RANKINGS,
    suffix_rules=SuffixRules.ALL,
    description="'true' if opponent is ranked, empty otherwise",
)
def extract_opponent_is_ranked(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if game_ctx and game_ctx.opponent_stats and game_ctx.opponent_stats.rank:
        return "true"
    return ""


@register_variable(
    name="is_ranked_matchup",
    category=Category.RANKINGS,
    suffix_rules=SuffixRules.ALL,
    description="'true' if both teams are ranked",
)
def extract_is_ranked_matchup(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    team_ranked = ctx.team_stats and ctx.team_stats.rank
    opponent_ranked = (
        game_ctx
        and game_ctx.opponent_stats
        and game_ctx.opponent_stats.rank
    )
    if team_ranked and opponent_ranked:
        return "true"
    return ""


@register_variable(
    name="home_team_rank",
    category=Category.RANKINGS,
    suffix_rules=SuffixRules.ALL,
    description="Home team's ranking for this game",
)
def extract_home_team_rank(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    event = game_ctx.event
    is_home = event.home_team.id == ctx.team_config.team_id
    if is_home and ctx.team_stats and ctx.team_stats.rank:
        return str(ctx.team_stats.rank)
    elif not is_home and game_ctx.opponent_stats and game_ctx.opponent_stats.rank:
        return str(game_ctx.opponent_stats.rank)
    return ""


@register_variable(
    name="away_team_rank",
    category=Category.RANKINGS,
    suffix_rules=SuffixRules.ALL,
    description="Away team's ranking for this game",
)
def extract_away_team_rank(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    event = game_ctx.event
    is_home = event.home_team.id == ctx.team_config.team_id
    if not is_home and ctx.team_stats and ctx.team_stats.rank:
        return str(ctx.team_stats.rank)
    elif is_home and game_ctx.opponent_stats and game_ctx.opponent_stats.rank:
        return str(game_ctx.opponent_stats.rank)
    return ""
