"""Game outcome template variables.

Variables for game results. These only apply to completed games (LAST_ONLY).
"""

from teamarr.templates.context import GameContext, TemplateContext
from teamarr.templates.variables.registry import (
    Category,
    SuffixRules,
    register_variable,
)


def _get_result(ctx: TemplateContext, game_ctx: GameContext | None) -> str | None:
    """Get game result for team. Returns 'win', 'loss', 'tie', or None."""
    if not game_ctx or not game_ctx.event:
        return None
    event = game_ctx.event
    if event.home_score is None or event.away_score is None:
        return None

    is_home = event.home_team.id == ctx.team_config.team_id
    team_score = event.home_score if is_home else event.away_score
    opp_score = event.away_score if is_home else event.home_score

    if team_score > opp_score:
        return "win"
    elif team_score < opp_score:
        return "loss"
    return "tie"


@register_variable(
    name="result",
    category=Category.OUTCOME,
    suffix_rules=SuffixRules.ALL,
    description="Game result ('W', 'L', or 'T')",
)
def extract_result(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    result = _get_result(ctx, game_ctx)
    if result == "win":
        return "W"
    elif result == "loss":
        return "L"
    elif result == "tie":
        return "T"
    return ""


@register_variable(
    name="result_lower",
    category=Category.OUTCOME,
    suffix_rules=SuffixRules.ALL,
    description="Game result lowercase ('w', 'l', or 't')",
)
def extract_result_lower(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    result = _get_result(ctx, game_ctx)
    if result == "win":
        return "w"
    elif result == "loss":
        return "l"
    elif result == "tie":
        return "t"
    return ""


@register_variable(
    name="result_text",
    category=Category.OUTCOME,
    suffix_rules=SuffixRules.ALL,
    description="Game result as text ('defeated', 'lost to', 'tied')",
)
def extract_result_text(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    result = _get_result(ctx, game_ctx)
    if result == "win":
        return "defeated"
    elif result == "loss":
        return "lost to"
    elif result == "tie":
        return "tied"
    return ""


@register_variable(
    name="overtime_text",
    category=Category.OUTCOME,
    suffix_rules=SuffixRules.ALL,  # Works for event channels without suffix
    description="'in overtime' if game went to overtime, empty otherwise",
)
def extract_overtime_text(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    # Check status detail for overtime indicators
    status = game_ctx.event.status
    if status.detail:
        detail_lower = status.detail.lower()
        if "ot" in detail_lower or "overtime" in detail_lower:
            return "in overtime"
    return ""


@register_variable(
    name="overtime_short",
    category=Category.OUTCOME,
    suffix_rules=SuffixRules.ALL,  # Works for event channels without suffix
    description="'OT' if game went to overtime, empty otherwise",
)
def extract_overtime_short(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    status = game_ctx.event.status
    if status.detail:
        detail_lower = status.detail.lower()
        if "ot" in detail_lower or "overtime" in detail_lower:
            return "OT"
    return ""
