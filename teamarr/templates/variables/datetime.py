"""DateTime variables: game dates, times, relative time.

These variables format game start times for display in EPG.
All times are converted to the user's configured timezone.
"""

from datetime import datetime
import platform

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
    fmt = "%A, %B %#d, %Y" if platform.system() == "Windows" else "%A, %B %-d, %Y"
    return dt.strftime(fmt)


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
    directive = "%#d" if platform.system() == "Windows" else "%-d"
    return dt.strftime(f"%b {directive}")


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
    name="relative_day",
    category=Category.DATETIME,
    suffix_rules=SuffixRules.BASE_NEXT_ONLY,
    description="Relative day: 'today', 'tonight', 'tomorrow', day of week (2-6 days), or date (7+ days)",
)
def extract_relative_day(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    """Return relative day for natural language EPG descriptions.

    Logic:
        - 0 days: 'today' (before 5pm) or 'tonight' (5pm+)
        - 1 day: 'tomorrow'
        - 2-6 days: day of week (e.g., 'wednesday')
        - 7+ days: date (e.g., 'jan 25')
    """
    dt = _get_local_time(game_ctx)
    if not dt:
        return ""
    now = now_user()
    delta = (dt.date() - now.date()).days

    if delta <= 0:
        return "tonight" if dt.hour >= 17 else "today"
    elif delta == 1:
        return "tomorrow"
    elif delta <= 6:
        return dt.strftime("%A").lower()
    else:
        flag = "#" if platform.system() == "Windows" else "-"
        return dt.strftime(f"%b %{flag}d")

@register_variable(
    name="relative_day_title",
    category=Category.DATETIME,
    suffix_rules=SuffixRules.BASE_NEXT_ONLY,
    description="Relative day (title case): 'Today', 'Tonight', 'Tomorrow', 'Wednesday', 'Jan 25'",
)
def extract_relative_day_title(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    """Return relative day in title case for EPG descriptions."""
    dt = _get_local_time(game_ctx)
    if not dt:
        return ""
    now = now_user()
    delta = (dt.date() - now.date()).days

    if delta <= 0:
        return "Tonight" if dt.hour >= 17 else "Today"
    elif delta == 1:
        return "Tomorrow"
    elif delta <= 6:
        return dt.strftime("%A")
    else:
        flag = "#" if platform.system() == "Windows" else "-"
        return dt.strftime(f"%b %{flag}d")


