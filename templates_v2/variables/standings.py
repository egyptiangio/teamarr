"""Standings-related template variables.

Variables for playoff seeds, games back, division standings, etc.
"""

from templates_v2.context import GameContext, TemplateContext
from templates_v2.variables.registry import (
    Category,
    SuffixRules,
    register_variable,
)


@register_variable(
    name="playoff_seed",
    category=Category.STANDINGS,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Team's playoff seed (e.g., '1' for 1-seed)",
)
def extract_playoff_seed(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if ctx.team_stats and ctx.team_stats.playoff_seed:
        return str(ctx.team_stats.playoff_seed)
    return ""


@register_variable(
    name="games_back",
    category=Category.STANDINGS,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Games behind division/conference leader",
)
def extract_games_back(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if ctx.team_stats and ctx.team_stats.games_back is not None:
        gb = ctx.team_stats.games_back
        if gb == 0:
            return "-"
        elif gb == int(gb):
            return str(int(gb))
        else:
            return str(gb)
    return ""


@register_variable(
    name="opponent_playoff_seed",
    category=Category.STANDINGS,
    suffix_rules=SuffixRules.ALL,
    description="Opponent's playoff seed",
)
def extract_opponent_playoff_seed(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if game_ctx and game_ctx.opponent_stats and game_ctx.opponent_stats.playoff_seed:
        return str(game_ctx.opponent_stats.playoff_seed)
    return ""


@register_variable(
    name="opponent_games_back",
    category=Category.STANDINGS,
    suffix_rules=SuffixRules.ALL,
    description="Opponent's games behind leader",
)
def extract_opponent_games_back(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if game_ctx and game_ctx.opponent_stats:
        gb = game_ctx.opponent_stats.games_back
        if gb is not None:
            if gb == 0:
                return "-"
            elif gb == int(gb):
                return str(int(gb))
            else:
                return str(gb)
    return ""
