"""Score-related template variables.

Variables for game scores. These only apply to completed games (LAST_ONLY).
"""

from templates_v2.context import GameContext, TemplateContext
from templates_v2.variables.registry import (
    Category,
    SuffixRules,
    register_variable,
)


def _is_team_home(ctx: TemplateContext, game_ctx: GameContext | None) -> bool | None:
    """Check if team is home. Returns None if context unavailable."""
    if not game_ctx or not game_ctx.event:
        return None
    return game_ctx.event.home_team.id == ctx.team_config.team_id


@register_variable(
    name="team_score",
    category=Category.SCORES,
    suffix_rules=SuffixRules.LAST_ONLY,
    description="Team's final score",
)
def extract_team_score(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    is_home = _is_team_home(ctx, game_ctx)
    if is_home is None or not game_ctx or not game_ctx.event:
        return ""
    event = game_ctx.event
    score = event.home_score if is_home else event.away_score
    return str(score) if score is not None else ""


@register_variable(
    name="opponent_score",
    category=Category.SCORES,
    suffix_rules=SuffixRules.LAST_ONLY,
    description="Opponent's final score",
)
def extract_opponent_score(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    is_home = _is_team_home(ctx, game_ctx)
    if is_home is None or not game_ctx or not game_ctx.event:
        return ""
    event = game_ctx.event
    score = event.away_score if is_home else event.home_score
    return str(score) if score is not None else ""


@register_variable(
    name="score",
    category=Category.SCORES,
    suffix_rules=SuffixRules.LAST_ONLY,
    description="Final score (e.g., '24-17')",
)
def extract_score(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    event = game_ctx.event
    if event.home_score is None or event.away_score is None:
        return ""
    return f"{event.home_score}-{event.away_score}"


@register_variable(
    name="final_score",
    category=Category.SCORES,
    suffix_rules=SuffixRules.LAST_ONLY,
    description="Final score with team perspective (e.g., 'W 24-17' or 'L 17-24')",
)
def extract_final_score(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    is_home = _is_team_home(ctx, game_ctx)
    if is_home is None or not game_ctx or not game_ctx.event:
        return ""
    event = game_ctx.event
    if event.home_score is None or event.away_score is None:
        return ""

    team_score = event.home_score if is_home else event.away_score
    opp_score = event.away_score if is_home else event.home_score

    if team_score > opp_score:
        return f"W {team_score}-{opp_score}"
    elif team_score < opp_score:
        return f"L {opp_score}-{team_score}"
    else:
        return f"T {team_score}-{opp_score}"


@register_variable(
    name="score_diff",
    category=Category.SCORES,
    suffix_rules=SuffixRules.LAST_ONLY,
    description="Score differential (positive=won by, negative=lost by)",
)
def extract_score_diff(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    is_home = _is_team_home(ctx, game_ctx)
    if is_home is None or not game_ctx or not game_ctx.event:
        return ""
    event = game_ctx.event
    if event.home_score is None or event.away_score is None:
        return ""

    team_score = event.home_score if is_home else event.away_score
    opp_score = event.away_score if is_home else event.home_score
    diff = team_score - opp_score
    if diff > 0:
        return f"+{diff}"
    return str(diff)


@register_variable(
    name="score_differential",
    category=Category.SCORES,
    suffix_rules=SuffixRules.LAST_ONLY,
    description="Score differential as absolute value (e.g., '7')",
)
def extract_score_differential(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    is_home = _is_team_home(ctx, game_ctx)
    if is_home is None or not game_ctx or not game_ctx.event:
        return "0"
    event = game_ctx.event
    if event.home_score is None or event.away_score is None:
        return "0"

    team_score = event.home_score if is_home else event.away_score
    opp_score = event.away_score if is_home else event.home_score
    return str(abs(team_score - opp_score))


@register_variable(
    name="score_differential_text",
    category=Category.SCORES,
    suffix_rules=SuffixRules.LAST_ONLY,
    description="Score differential as text (e.g., 'by 7' or 'by 3')",
)
def extract_score_diff_text(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    is_home = _is_team_home(ctx, game_ctx)
    if is_home is None or not game_ctx or not game_ctx.event:
        return ""
    event = game_ctx.event
    if event.home_score is None or event.away_score is None:
        return ""

    team_score = event.home_score if is_home else event.away_score
    opp_score = event.away_score if is_home else event.home_score
    diff = abs(team_score - opp_score)
    return f"by {diff}" if diff > 0 else "tie"


@register_variable(
    name="home_team_score",
    category=Category.SCORES,
    suffix_rules=SuffixRules.LAST_ONLY,
    description="Home team's final score",
)
def extract_home_team_score(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    score = game_ctx.event.home_score
    return str(score) if score is not None else ""


@register_variable(
    name="away_team_score",
    category=Category.SCORES,
    suffix_rules=SuffixRules.LAST_ONLY,
    description="Away team's final score",
)
def extract_away_team_score(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    score = game_ctx.event.away_score
    return str(score) if score is not None else ""
