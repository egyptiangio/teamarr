"""Record-related template variables.

Variables for team records (W-L-T), win percentages, etc.
"""

from templates_v2.context import GameContext, TemplateContext
from templates_v2.variables.registry import (
    Category,
    SuffixRules,
    register_variable,
)


def _get_win_pct(wins: int, losses: int, ties: int = 0) -> str:
    """Calculate win percentage as a formatted string."""
    total = wins + losses + ties
    if total == 0:
        return ".000"
    # Ties count as half a win for percentage
    pct = (wins + (ties * 0.5)) / total
    return f".{int(pct * 1000):03d}"


@register_variable(
    name="team_record",
    category=Category.RECORDS,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Team's overall record (e.g., '10-2' or '8-3-1')",
)
def extract_team_record(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if ctx.team_stats:
        return ctx.team_stats.record
    return ""


@register_variable(
    name="team_wins",
    category=Category.RECORDS,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Team's total wins",
)
def extract_team_wins(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if ctx.team_stats:
        return str(ctx.team_stats.wins)
    return ""


@register_variable(
    name="team_losses",
    category=Category.RECORDS,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Team's total losses",
)
def extract_team_losses(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if ctx.team_stats:
        return str(ctx.team_stats.losses)
    return ""


@register_variable(
    name="team_ties",
    category=Category.RECORDS,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Team's total ties/draws",
)
def extract_team_ties(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if ctx.team_stats:
        return str(ctx.team_stats.ties)
    return ""


@register_variable(
    name="team_win_pct",
    category=Category.RECORDS,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Team's winning percentage (e.g., '.750')",
)
def extract_team_win_pct(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if ctx.team_stats:
        return _get_win_pct(
            ctx.team_stats.wins, ctx.team_stats.losses, ctx.team_stats.ties
        )
    return ""


@register_variable(
    name="opponent_record",
    category=Category.RECORDS,
    suffix_rules=SuffixRules.ALL,
    description="Opponent's overall record",
)
def extract_opponent_record(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if game_ctx and game_ctx.opponent_stats:
        return game_ctx.opponent_stats.record
    return ""


@register_variable(
    name="opponent_wins",
    category=Category.RECORDS,
    suffix_rules=SuffixRules.ALL,
    description="Opponent's total wins",
)
def extract_opponent_wins(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if game_ctx and game_ctx.opponent_stats:
        return str(game_ctx.opponent_stats.wins)
    return ""


@register_variable(
    name="opponent_losses",
    category=Category.RECORDS,
    suffix_rules=SuffixRules.ALL,
    description="Opponent's total losses",
)
def extract_opponent_losses(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if game_ctx and game_ctx.opponent_stats:
        return str(game_ctx.opponent_stats.losses)
    return ""


@register_variable(
    name="opponent_ties",
    category=Category.RECORDS,
    suffix_rules=SuffixRules.ALL,
    description="Opponent's total ties/draws",
)
def extract_opponent_ties(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if game_ctx and game_ctx.opponent_stats:
        return str(game_ctx.opponent_stats.ties)
    return ""


@register_variable(
    name="opponent_win_pct",
    category=Category.RECORDS,
    suffix_rules=SuffixRules.ALL,
    description="Opponent's winning percentage",
)
def extract_opponent_win_pct(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if game_ctx and game_ctx.opponent_stats:
        stats = game_ctx.opponent_stats
        return _get_win_pct(stats.wins, stats.losses, stats.ties)
    return ""


@register_variable(
    name="home_record",
    category=Category.RECORDS,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Team's home record (e.g., '5-1')",
)
def extract_home_record(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if ctx.team_stats and ctx.team_stats.home_record:
        return ctx.team_stats.home_record
    return ""


@register_variable(
    name="away_record",
    category=Category.RECORDS,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Team's away/road record",
)
def extract_away_record(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if ctx.team_stats and ctx.team_stats.away_record:
        return ctx.team_stats.away_record
    return ""


@register_variable(
    name="home_team_record",
    category=Category.RECORDS,
    suffix_rules=SuffixRules.ALL,
    description="Home team's overall record for this game",
)
def extract_home_team_record(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    event = game_ctx.event
    is_home = event.home_team.id == ctx.team_config.team_id
    if is_home and ctx.team_stats:
        return ctx.team_stats.record
    elif not is_home and game_ctx.opponent_stats:
        return game_ctx.opponent_stats.record
    return ""


@register_variable(
    name="away_team_record",
    category=Category.RECORDS,
    suffix_rules=SuffixRules.ALL,
    description="Away team's overall record for this game",
)
def extract_away_team_record(
    ctx: TemplateContext, game_ctx: GameContext | None
) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    event = game_ctx.event
    is_home = event.home_team.id == ctx.team_config.team_id
    if not is_home and ctx.team_stats:
        return ctx.team_stats.record
    elif is_home and game_ctx.opponent_stats:
        return game_ctx.opponent_stats.record
    return ""


def _parse_record_for_pct(record: str | None) -> tuple[int, int, int]:
    """Parse record string like '5-2' or '3-1-1' into (wins, losses, ties)."""
    if not record:
        return 0, 0, 0
    parts = record.split("-")
    try:
        if len(parts) == 2:
            return int(parts[0]), int(parts[1]), 0
        elif len(parts) == 3:
            return int(parts[0]), int(parts[2]), int(parts[1])
        return 0, 0, 0
    except ValueError:
        return 0, 0, 0


@register_variable(
    name="home_win_pct",
    category=Category.RECORDS,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Team's home winning percentage",
)
def extract_home_win_pct(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if ctx.team_stats and ctx.team_stats.home_record:
        wins, losses, ties = _parse_record_for_pct(ctx.team_stats.home_record)
        return _get_win_pct(wins, losses, ties)
    return ""


@register_variable(
    name="away_win_pct",
    category=Category.RECORDS,
    suffix_rules=SuffixRules.BASE_ONLY,
    description="Team's away winning percentage",
)
def extract_away_win_pct(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if ctx.team_stats and ctx.team_stats.away_record:
        wins, losses, ties = _parse_record_for_pct(ctx.team_stats.away_record)
        return _get_win_pct(wins, losses, ties)
    return ""


@register_variable(
    name="home_team_seed",
    category=Category.RECORDS,
    suffix_rules=SuffixRules.ALL,
    description="Home team's playoff seed",
)
def extract_home_team_seed(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    is_home = game_ctx.event.home_team.id == ctx.team_config.team_id
    if is_home and ctx.team_stats and ctx.team_stats.playoff_seed:
        return str(ctx.team_stats.playoff_seed)
    elif not is_home and game_ctx.opponent_stats and game_ctx.opponent_stats.playoff_seed:
        return str(game_ctx.opponent_stats.playoff_seed)
    return ""


@register_variable(
    name="away_team_seed",
    category=Category.RECORDS,
    suffix_rules=SuffixRules.ALL,
    description="Away team's playoff seed",
)
def extract_away_team_seed(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    is_home = game_ctx.event.home_team.id == ctx.team_config.team_id
    if not is_home and ctx.team_stats and ctx.team_stats.playoff_seed:
        return str(ctx.team_stats.playoff_seed)
    elif is_home and game_ctx.opponent_stats and game_ctx.opponent_stats.playoff_seed:
        return str(game_ctx.opponent_stats.playoff_seed)
    return ""
