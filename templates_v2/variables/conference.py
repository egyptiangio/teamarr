"""Conference and division template variables.

Variables for college conferences and pro league divisions.
"""

from templates_v2.context import GameContext, TemplateContext
from templates_v2.variables.registry import (
    Category,
    SuffixRules,
    register_variable,
)

# College conference variables (team's perspective)


@register_variable(
    name="college_conference",
    category=Category.CONFERENCE,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Team's college conference name",
)
def extract_college_conference(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if ctx.team_stats and ctx.team_stats.conference:
        return ctx.team_stats.conference
    return ""


@register_variable(
    name="college_conference_abbrev",
    category=Category.CONFERENCE,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Team's college conference abbreviation",
)
def extract_college_conference_abbrev(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if ctx.team_stats and ctx.team_stats.conference_abbrev:
        return ctx.team_stats.conference_abbrev
    return ""


# Pro conference/division variables (team's perspective)


@register_variable(
    name="pro_conference",
    category=Category.CONFERENCE,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Team's pro conference (e.g., 'NFC', 'Eastern')",
)
def extract_pro_conference(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if ctx.team_stats and ctx.team_stats.conference:
        return ctx.team_stats.conference
    return ""


@register_variable(
    name="pro_conference_abbrev",
    category=Category.CONFERENCE,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Team's pro conference abbreviation",
)
def extract_pro_conference_abbrev(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if ctx.team_stats and ctx.team_stats.conference_abbrev:
        return ctx.team_stats.conference_abbrev
    return ""


@register_variable(
    name="pro_division",
    category=Category.CONFERENCE,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Team's pro division (e.g., 'NFC North')",
)
def extract_pro_division(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if ctx.team_stats and ctx.team_stats.division:
        return ctx.team_stats.division
    return ""


# Opponent conference variables


@register_variable(
    name="opponent_college_conference",
    category=Category.CONFERENCE,
    suffix_rules=SuffixRules.ALL,
    description="Opponent's college conference",
)
def extract_opponent_college_conference(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if game_ctx and game_ctx.opponent_stats and game_ctx.opponent_stats.conference:
        return game_ctx.opponent_stats.conference
    return ""


@register_variable(
    name="opponent_college_conference_abbrev",
    category=Category.CONFERENCE,
    suffix_rules=SuffixRules.ALL,
    description="Opponent's college conference abbreviation",
)
def extract_opponent_college_conference_abbrev(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if game_ctx and game_ctx.opponent_stats:
        return game_ctx.opponent_stats.conference_abbrev or ""
    return ""


@register_variable(
    name="opponent_pro_conference",
    category=Category.CONFERENCE,
    suffix_rules=SuffixRules.ALL,
    description="Opponent's pro conference",
)
def extract_opponent_pro_conference(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if game_ctx and game_ctx.opponent_stats and game_ctx.opponent_stats.conference:
        return game_ctx.opponent_stats.conference
    return ""


@register_variable(
    name="opponent_pro_conference_abbrev",
    category=Category.CONFERENCE,
    suffix_rules=SuffixRules.ALL,
    description="Opponent's pro conference abbreviation",
)
def extract_opponent_pro_conference_abbrev(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if game_ctx and game_ctx.opponent_stats:
        return game_ctx.opponent_stats.conference_abbrev or ""
    return ""


@register_variable(
    name="opponent_pro_division",
    category=Category.CONFERENCE,
    suffix_rules=SuffixRules.ALL,
    description="Opponent's pro division",
)
def extract_opponent_pro_division(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if game_ctx and game_ctx.opponent_stats and game_ctx.opponent_stats.division:
        return game_ctx.opponent_stats.division
    return ""


# Home team conference variables


@register_variable(
    name="home_team_college_conference",
    category=Category.CONFERENCE,
    suffix_rules=SuffixRules.ALL,
    description="Home team's college conference",
)
def extract_home_team_college_conference(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    is_home = game_ctx.event.home_team.id == ctx.team_config.team_id
    if is_home and ctx.team_stats:
        return ctx.team_stats.conference or ""
    elif not is_home and game_ctx.opponent_stats:
        return game_ctx.opponent_stats.conference or ""
    return ""


@register_variable(
    name="home_team_college_conference_abbrev",
    category=Category.CONFERENCE,
    suffix_rules=SuffixRules.ALL,
    description="Home team's college conference abbreviation",
)
def extract_home_team_college_conference_abbrev(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    is_home = game_ctx.event.home_team.id == ctx.team_config.team_id
    if is_home and ctx.team_stats:
        return ctx.team_stats.conference_abbrev or ""
    elif not is_home and game_ctx.opponent_stats:
        return game_ctx.opponent_stats.conference_abbrev or ""
    return ""


@register_variable(
    name="home_team_pro_conference",
    category=Category.CONFERENCE,
    suffix_rules=SuffixRules.ALL,
    description="Home team's pro conference",
)
def extract_home_team_pro_conference(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    is_home = game_ctx.event.home_team.id == ctx.team_config.team_id
    if is_home and ctx.team_stats:
        return ctx.team_stats.conference or ""
    elif not is_home and game_ctx.opponent_stats:
        return game_ctx.opponent_stats.conference or ""
    return ""


@register_variable(
    name="home_team_pro_conference_abbrev",
    category=Category.CONFERENCE,
    suffix_rules=SuffixRules.ALL,
    description="Home team's pro conference abbreviation",
)
def extract_home_team_pro_conference_abbrev(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    is_home = game_ctx.event.home_team.id == ctx.team_config.team_id
    if is_home and ctx.team_stats:
        return ctx.team_stats.conference_abbrev or ""
    elif not is_home and game_ctx.opponent_stats:
        return game_ctx.opponent_stats.conference_abbrev or ""
    return ""


@register_variable(
    name="home_team_pro_division",
    category=Category.CONFERENCE,
    suffix_rules=SuffixRules.ALL,
    description="Home team's pro division",
)
def extract_home_team_pro_division(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    is_home = game_ctx.event.home_team.id == ctx.team_config.team_id
    if is_home and ctx.team_stats:
        return ctx.team_stats.division or ""
    elif not is_home and game_ctx.opponent_stats:
        return game_ctx.opponent_stats.division or ""
    return ""


# Away team conference variables


@register_variable(
    name="away_team_college_conference",
    category=Category.CONFERENCE,
    suffix_rules=SuffixRules.ALL,
    description="Away team's college conference",
)
def extract_away_team_college_conference(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    is_home = game_ctx.event.home_team.id == ctx.team_config.team_id
    if not is_home and ctx.team_stats:
        return ctx.team_stats.conference or ""
    elif is_home and game_ctx.opponent_stats:
        return game_ctx.opponent_stats.conference or ""
    return ""


@register_variable(
    name="away_team_college_conference_abbrev",
    category=Category.CONFERENCE,
    suffix_rules=SuffixRules.ALL,
    description="Away team's college conference abbreviation",
)
def extract_away_team_college_conference_abbrev(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    is_home = game_ctx.event.home_team.id == ctx.team_config.team_id
    if not is_home and ctx.team_stats:
        return ctx.team_stats.conference_abbrev or ""
    elif is_home and game_ctx.opponent_stats:
        return game_ctx.opponent_stats.conference_abbrev or ""
    return ""


@register_variable(
    name="away_team_pro_conference",
    category=Category.CONFERENCE,
    suffix_rules=SuffixRules.ALL,
    description="Away team's pro conference",
)
def extract_away_team_pro_conference(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    is_home = game_ctx.event.home_team.id == ctx.team_config.team_id
    if not is_home and ctx.team_stats:
        return ctx.team_stats.conference or ""
    elif is_home and game_ctx.opponent_stats:
        return game_ctx.opponent_stats.conference or ""
    return ""


@register_variable(
    name="away_team_pro_conference_abbrev",
    category=Category.CONFERENCE,
    suffix_rules=SuffixRules.ALL,
    description="Away team's pro conference abbreviation",
)
def extract_away_team_pro_conference_abbrev(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    is_home = game_ctx.event.home_team.id == ctx.team_config.team_id
    if not is_home and ctx.team_stats:
        return ctx.team_stats.conference_abbrev or ""
    elif is_home and game_ctx.opponent_stats:
        return game_ctx.opponent_stats.conference_abbrev or ""
    return ""


@register_variable(
    name="away_team_pro_division",
    category=Category.CONFERENCE,
    suffix_rules=SuffixRules.ALL,
    description="Away team's pro division",
)
def extract_away_team_pro_division(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    is_home = game_ctx.event.home_team.id == ctx.team_config.team_id
    if not is_home and ctx.team_stats:
        return ctx.team_stats.division or ""
    elif is_home and game_ctx.opponent_stats:
        return game_ctx.opponent_stats.division or ""
    return ""
