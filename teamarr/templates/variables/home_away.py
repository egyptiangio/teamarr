"""Home/Away variables: location context, team positions.

These variables provide home/away context and positional team references.
"""

from teamarr.templates.context import GameContext, TemplateContext
from teamarr.templates.variables.registry import (
    Category,
    SuffixRules,
    register_variable,
)


def _to_pascal_case(name: str) -> str:
    """Convert team name to PascalCase.

    Strips non-alphanumeric characters and normalizes accents.
    Examples:
        "Detroit Lions" → "DetroitLions"
        "D.C. United" → "DcUnited"
        "Atlético Madrid" → "AtleticoMadrid"
    """
    import re
    import unicodedata

    # Normalize unicode (é → e)
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    # Keep only alphanumeric, split on non-alpha
    words = re.split(r"[^a-zA-Z0-9]+", ascii_name)
    return "".join(word.capitalize() for word in words if word)


def _is_home(ctx: TemplateContext, game_ctx: GameContext | None) -> bool | None:
    """Determine if configured team is home. Returns None if no game."""
    if not game_ctx or not game_ctx.event:
        return None
    return game_ctx.event.home_team.id == ctx.team_config.team_id


@register_variable(
    name="is_home",
    category=Category.HOME_AWAY,
    suffix_rules=SuffixRules.ALL,
    description="'true' if team is home, 'false' if away",
)
def extract_is_home(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    is_home = _is_home(ctx, game_ctx)
    if is_home is None:
        return ""
    return "true" if is_home else "false"


@register_variable(
    name="is_away",
    category=Category.HOME_AWAY,
    suffix_rules=SuffixRules.ALL,
    description="'true' if team is away, 'false' if home",
)
def extract_is_away(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    is_home = _is_home(ctx, game_ctx)
    if is_home is None:
        return ""
    return "false" if is_home else "true"


@register_variable(
    name="home_away_text",
    category=Category.HOME_AWAY,
    suffix_rules=SuffixRules.ALL,
    description="'at home' or 'on the road'",
)
def extract_home_away_text(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    is_home = _is_home(ctx, game_ctx)
    if is_home is None:
        return ""
    return "at home" if is_home else "on the road"


@register_variable(
    name="vs_at",
    category=Category.HOME_AWAY,
    suffix_rules=SuffixRules.ALL,
    description="'vs' if home, 'at' if away",
)
def extract_vs_at(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    is_home = _is_home(ctx, game_ctx)
    if is_home is None:
        return ""
    return "vs" if is_home else "at"


@register_variable(
    name="vs_@",
    category=Category.HOME_AWAY,
    suffix_rules=SuffixRules.ALL,
    description="'vs' if home, '@' if away",
)
def extract_vs_symbol(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    is_home = _is_home(ctx, game_ctx)
    if is_home is None:
        return ""
    return "vs" if is_home else "@"


@register_variable(
    name="home_team",
    category=Category.HOME_AWAY,
    suffix_rules=SuffixRules.ALL,
    description="Home team name (positional)",
)
def extract_home_team(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    return game_ctx.event.home_team.name


@register_variable(
    name="away_team",
    category=Category.HOME_AWAY,
    suffix_rules=SuffixRules.ALL,
    description="Away team name (positional)",
)
def extract_away_team(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    return game_ctx.event.away_team.name


@register_variable(
    name="home_team_abbrev",
    category=Category.HOME_AWAY,
    suffix_rules=SuffixRules.ALL,
    description="Home team abbreviation uppercase",
)
def extract_home_team_abbrev(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    return game_ctx.event.home_team.abbreviation.upper()


@register_variable(
    name="away_team_abbrev",
    category=Category.HOME_AWAY,
    suffix_rules=SuffixRules.ALL,
    description="Away team abbreviation uppercase",
)
def extract_away_team_abbrev(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    return game_ctx.event.away_team.abbreviation.upper()


@register_variable(
    name="home_team_abbrev_lower",
    category=Category.HOME_AWAY,
    suffix_rules=SuffixRules.ALL,
    description="Home team abbreviation lowercase",
)
def extract_home_team_abbrev_lower(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    return game_ctx.event.home_team.abbreviation.lower()


@register_variable(
    name="away_team_abbrev_lower",
    category=Category.HOME_AWAY,
    suffix_rules=SuffixRules.ALL,
    description="Away team abbreviation lowercase",
)
def extract_away_team_abbrev_lower(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    return game_ctx.event.away_team.abbreviation.lower()


@register_variable(
    name="home_team_pascal",
    category=Category.HOME_AWAY,
    suffix_rules=SuffixRules.ALL,
    description="Home team name in PascalCase",
)
def extract_home_team_pascal(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    return _to_pascal_case(game_ctx.event.home_team.name)


@register_variable(
    name="away_team_pascal",
    category=Category.HOME_AWAY,
    suffix_rules=SuffixRules.ALL,
    description="Away team name in PascalCase",
)
def extract_away_team_pascal(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    return _to_pascal_case(game_ctx.event.away_team.name)


@register_variable(
    name="home_team_logo",
    category=Category.HOME_AWAY,
    suffix_rules=SuffixRules.ALL,
    description="Home team logo URL",
)
def extract_home_team_logo(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    return game_ctx.event.home_team.logo_url or ""


@register_variable(
    name="away_team_logo",
    category=Category.HOME_AWAY,
    suffix_rules=SuffixRules.ALL,
    description="Away team logo URL",
)
def extract_away_team_logo(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.event:
        return ""
    return game_ctx.event.away_team.logo_url or ""
