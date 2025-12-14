"""Venue variables: stadium name, city, state.

These variables provide game location information.
"""

from templates_v2.context import GameContext, TemplateContext
from templates_v2.variables.registry import (
    Category,
    SuffixRules,
    register_variable,
)


@register_variable(
    name="venue",
    category=Category.VENUE,
    suffix_rules=SuffixRules.ALL,
    description="Stadium/arena name (e.g., 'Ford Field')",
)
def extract_venue(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.event or not game_ctx.event.venue:
        return ""
    return game_ctx.event.venue.name or ""


@register_variable(
    name="venue_city",
    category=Category.VENUE,
    suffix_rules=SuffixRules.ALL,
    description="Venue city (e.g., 'Detroit')",
)
def extract_venue_city(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.event or not game_ctx.event.venue:
        return ""
    return game_ctx.event.venue.city or ""


@register_variable(
    name="venue_state",
    category=Category.VENUE,
    suffix_rules=SuffixRules.ALL,
    description="Venue state (e.g., 'Michigan')",
)
def extract_venue_state(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.event or not game_ctx.event.venue:
        return ""
    return game_ctx.event.venue.state or ""


@register_variable(
    name="venue_full",
    category=Category.VENUE,
    suffix_rules=SuffixRules.ALL,
    description="Full venue location (e.g., 'Ford Field, Detroit, Michigan')",
)
def extract_venue_full(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    if not game_ctx or not game_ctx.event or not game_ctx.event.venue:
        return ""
    venue = game_ctx.event.venue
    parts = [p for p in [venue.name, venue.city, venue.state] if p]
    return ", ".join(parts)


@register_variable(
    name="attendance",
    category=Category.VENUE,
    suffix_rules=SuffixRules.LAST_ONLY,
    description="Game attendance (formatted with commas)",
)
def extract_attendance(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    # Attendance would need to be added to Event dataclass
    # Available in ESPN competition data as competition.attendance
    # For now, return empty - can be populated when parsing events
    return ""
