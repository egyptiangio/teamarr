"""DateTime variables: game dates, times, relative time.

These variables format game start times for display in EPG.
All times are converted to the user's configured timezone.
"""

from datetime import datetime

from teamarr.templates.context import GameContext, TemplateContext
from teamarr.templates.variables.registry import (
    Category,
    SuffixRules,
    register_variable,
)
from teamarr.utilities.tz import (
    format_time,
    now_user,
    to_user_tz,
)


def _get_local_time(game_ctx: GameContext | None) -> datetime | None:
    """Get game start time in user timezone."""
    if not game_ctx or not game_ctx.event:
        return None
    return to_user_tz(game_ctx.event.start_time)


@register_variable(
    name="game_date",
    category=Category.DATETIME,
    suffix_rules=SuffixRules.ALL,
    description="Full game date (e.g., 'Tuesday, December 10, 2024')",
)
def extract_game_date(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    dt = _get_local_time(game_ctx)
    if not dt:
        return ""
    return dt.strftime("%A, %B %-d, %Y")


@register_variable(
    name="game_date_short",
    category=Category.DATETIME,
    suffix_rules=SuffixRules.ALL,
    description="Short game date (e.g., 'Dec 10')",
)
def extract_game_date_short(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    dt = _get_local_time(game_ctx)
    if not dt:
        return ""
    return dt.strftime("%b %-d")


@register_variable(
    name="game_day",
    category=Category.DATETIME,
    suffix_rules=SuffixRules.ALL,
    description="Day of week (e.g., 'Tuesday')",
)
def extract_game_day(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    dt = _get_local_time(game_ctx)
    if not dt:
        return ""
    return dt.strftime("%A")


@register_variable(
    name="game_day_short",
    category=Category.DATETIME,
    suffix_rules=SuffixRules.ALL,
    description="Short day of week (e.g., 'Tue')",
)
def extract_game_day_short(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    dt = _get_local_time(game_ctx)
    if not dt:
        return ""
    return dt.strftime("%a")


@register_variable(
    name="game_time",
    category=Category.DATETIME,
    suffix_rules=SuffixRules.ALL,
    description="Game time formatted per user settings (e.g., '7:30 PM EST' or '19:30')",
)
def extract_game_time(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    # Uses user's time_format (12h/24h) and show_timezone settings
    return format_time(game_ctx.event.start_time)


@register_variable(
    name="today_tonight",
    category=Category.DATETIME,
    suffix_rules=SuffixRules.ALL,
    description="'today' or 'tonight' based on 5pm cutoff",
)
def extract_today_tonight(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    dt = _get_local_time(game_ctx)
    if not dt:
        return ""
    return "tonight" if dt.hour >= 17 else "today"


@register_variable(
    name="today_tonight_title",
    category=Category.DATETIME,
    suffix_rules=SuffixRules.ALL,
    description="'Today' or 'Tonight' (title case)",
)
def extract_today_tonight_title(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    dt = _get_local_time(game_ctx)
    if not dt:
        return ""
    return "Tonight" if dt.hour >= 17 else "Today"


@register_variable(
    name="days_until",
    category=Category.DATETIME,
    suffix_rules=SuffixRules.ALL,
    description="Days until game (e.g., '3')",
)
def extract_days_until(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    dt = _get_local_time(game_ctx)
    if not dt:
        return ""
    now = now_user()
    delta = dt.date() - now.date()
    return str(max(0, delta.days))


@register_variable(
    name="hours_until",
    category=Category.DATETIME,
    suffix_rules=SuffixRules.ALL,
    description="Hours until game (e.g., '24')",
)
def extract_hours_until(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    dt = _get_local_time(game_ctx)
    if not dt:
        return ""
    now = now_user()
    delta = dt - now
    hours = int(delta.total_seconds() / 3600)
    return str(max(0, hours))
