"""Playoff and season type template variables.

Variables for identifying game type (playoff, preseason, regular season).
"""

from templates_v2.context import GameContext, TemplateContext
from templates_v2.variables.registry import (
    Category,
    SuffixRules,
    register_variable,
)


def _get_season_type(game_ctx: GameContext | None) -> str:
    """Get season type from event."""
    if not game_ctx or not game_ctx.event:
        return ""
    return game_ctx.event.season_type or ""


@register_variable(
    name="season_type",
    category=Category.PLAYOFFS,
    suffix_rules=SuffixRules.ALL,
    description="Season type (e.g., 'Regular Season', 'Playoffs', 'Preseason')",
)
def extract_season_type(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    return _get_season_type(game_ctx)


@register_variable(
    name="is_playoff",
    category=Category.PLAYOFFS,
    suffix_rules=SuffixRules.ALL,
    description="'true' if playoff/postseason game",
)
def extract_is_playoff(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    season_type = _get_season_type(game_ctx).lower()
    if "post" in season_type or "playoff" in season_type:
        return "true"
    return ""


@register_variable(
    name="is_preseason",
    category=Category.PLAYOFFS,
    suffix_rules=SuffixRules.ALL,
    description="'true' if preseason/exhibition game",
)
def extract_is_preseason(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    season_type = _get_season_type(game_ctx).lower()
    if "pre" in season_type or "exhibition" in season_type:
        return "true"
    return ""


@register_variable(
    name="is_regular_season",
    category=Category.PLAYOFFS,
    suffix_rules=SuffixRules.ALL,
    description="'true' if regular season game",
)
def extract_is_regular_season(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    season_type = _get_season_type(game_ctx).lower()
    if not season_type:
        return ""
    # Regular season if not playoff and not preseason
    if "post" not in season_type and "playoff" not in season_type:
        if "pre" not in season_type and "exhibition" not in season_type:
            return "true"
    return ""
