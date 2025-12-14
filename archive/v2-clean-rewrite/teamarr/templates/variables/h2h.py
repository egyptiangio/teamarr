"""Head-to-head template variables.

Variables for season series and recent matchup history.
"""

from teamarr.templates.context import GameContext, TemplateContext
from teamarr.templates.variables.registry import (
    Category,
    SuffixRules,
    register_variable,
)


@register_variable(
    name="season_series",
    category=Category.H2H,
    suffix_rules=SuffixRules.ALL,
    description="Season series record (e.g., '2-1')",
)
def extract_season_series(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.h2h:
        return ""
    h2h = game_ctx.h2h
    return f"{h2h.team_wins}-{h2h.opponent_wins}"


@register_variable(
    name="season_series_team_wins",
    category=Category.H2H,
    suffix_rules=SuffixRules.ALL,
    description="Team's wins in season series",
)
def extract_season_series_team_wins(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if not game_ctx or not game_ctx.h2h:
        return "0"
    return str(game_ctx.h2h.team_wins)


@register_variable(
    name="season_series_opponent_wins",
    category=Category.H2H,
    suffix_rules=SuffixRules.ALL,
    description="Opponent's wins in season series",
)
def extract_season_series_opponent_wins(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if not game_ctx or not game_ctx.h2h:
        return "0"
    return str(game_ctx.h2h.opponent_wins)


@register_variable(
    name="season_series_leader",
    category=Category.H2H,
    suffix_rules=SuffixRules.ALL,
    description="'Leads', 'Trails', or 'Tied' in season series",
)
def extract_season_series_leader(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if not game_ctx or not game_ctx.h2h:
        return ""
    h2h = game_ctx.h2h
    if h2h.team_wins > h2h.opponent_wins:
        return "Leads"
    elif h2h.team_wins < h2h.opponent_wins:
        return "Trails"
    elif h2h.team_wins > 0:
        return "Tied"
    return ""


@register_variable(
    name="is_rematch",
    category=Category.H2H,
    suffix_rules=SuffixRules.ALL,
    description="'true' if teams have played this season",
)
def extract_is_rematch(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if game_ctx and game_ctx.h2h:
        h2h = game_ctx.h2h
        if h2h.team_wins > 0 or h2h.opponent_wins > 0:
            return "true"
    return ""


@register_variable(
    name="rematch_result",
    category=Category.H2H,
    suffix_rules=SuffixRules.ALL,
    description="Result of last meeting ('W', 'L', or 'T')",
)
def extract_rematch_result(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.h2h or not game_ctx.h2h.previous_result:
        return ""
    return game_ctx.h2h.previous_result


@register_variable(
    name="rematch_score",
    category=Category.H2H,
    suffix_rules=SuffixRules.ALL,
    description="Score of last meeting (e.g., '24-17')",
)
def extract_rematch_score(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.h2h or not game_ctx.h2h.previous_score:
        return ""
    return game_ctx.h2h.previous_score


@register_variable(
    name="rematch_days_since",
    category=Category.H2H,
    suffix_rules=SuffixRules.ALL,
    description="Days since last meeting",
)
def extract_rematch_days_since(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if not game_ctx or not game_ctx.h2h:
        return ""
    days = game_ctx.h2h.days_since
    if days > 0:
        return str(days)
    return ""


@register_variable(
    name="rematch_text",
    category=Category.H2H,
    suffix_rules=SuffixRules.ALL,
    description="Rematch context (e.g., 'won last meeting 24-17')",
)
def extract_rematch_text(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.h2h:
        return ""
    h2h = game_ctx.h2h
    if not h2h.previous_result or not h2h.previous_score:
        return ""

    if h2h.previous_result == "W":
        verb = "won"
    elif h2h.previous_result == "L":
        verb = "lost"
    else:
        verb = "tied"

    return f"{verb} last meeting {h2h.previous_score}"


@register_variable(
    name="rematch_venue",
    category=Category.H2H,
    suffix_rules=SuffixRules.ALL,
    description="Venue of last meeting",
)
def extract_rematch_venue(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.h2h:
        return ""
    return game_ctx.h2h.previous_venue or ""


@register_variable(
    name="rematch_city",
    category=Category.H2H,
    suffix_rules=SuffixRules.ALL,
    description="City of last meeting",
)
def extract_rematch_city(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.h2h:
        return ""
    return game_ctx.h2h.previous_city or ""


@register_variable(
    name="rematch_date",
    category=Category.H2H,
    suffix_rules=SuffixRules.ALL,
    description="Date of last meeting (formatted)",
)
def extract_rematch_date(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    # Would need to store the previous game date in HeadToHead
    # For now, return empty - can be populated when h2h is built
    return ""


@register_variable(
    name="rematch_score_abbrev",
    category=Category.H2H,
    suffix_rules=SuffixRules.ALL,
    description="Score of last meeting with team abbrevs (e.g., 'DET 24-17 CHI')",
)
def extract_rematch_score_abbrev(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if not game_ctx or not game_ctx.h2h or not game_ctx.h2h.previous_score:
        return ""
    # Would need team abbrevs stored in h2h - for now return the score
    return game_ctx.h2h.previous_score


@register_variable(
    name="rematch_season_series",
    category=Category.H2H,
    suffix_rules=SuffixRules.ALL,
    description="Season series context (e.g., 'leads series 2-1')",
)
def extract_rematch_season_series(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if not game_ctx or not game_ctx.h2h:
        return ""
    h2h = game_ctx.h2h
    if h2h.team_wins == 0 and h2h.opponent_wins == 0:
        return ""
    if h2h.team_wins > h2h.opponent_wins:
        return f"leads series {h2h.team_wins}-{h2h.opponent_wins}"
    elif h2h.team_wins < h2h.opponent_wins:
        return f"trails series {h2h.opponent_wins}-{h2h.team_wins}"
    return f"series tied {h2h.team_wins}-{h2h.opponent_wins}"
