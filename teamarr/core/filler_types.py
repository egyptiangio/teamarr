"""Filler generation types and configuration.

These types are used by both the database layer (for template conversion)
and the consumers layer (for filler generation). Placed in core to maintain
proper layer isolation.
"""

from dataclasses import dataclass, field
from enum import Enum


class FillerType(str, Enum):
    """Types of filler content."""

    PREGAME = "pregame"
    POSTGAME = "postgame"
    IDLE = "idle"


@dataclass
class FillerTemplate:
    """Template for a specific filler type."""

    title: str
    subtitle: str | None = None
    description: str | None = None
    art_url: str | None = None


@dataclass
class ConditionalFillerTemplate:
    """Conditional templates based on game status."""

    enabled: bool = False
    description_final: str | None = None  # When last game is final
    description_not_final: str | None = None  # When last game in progress


@dataclass
class OffseasonFillerTemplate:
    """Offseason templates when no games scheduled."""

    enabled: bool = False
    title: str | None = None
    subtitle: str | None = None
    description: str | None = None


@dataclass
class FillerConfig:
    """Configuration for filler generation.

    Populated from database templates table. No hardcoded defaults -
    schema.sql provides all default values.
    """

    # Pregame settings
    pregame_enabled: bool = True
    pregame_template: FillerTemplate = field(
        default_factory=lambda: FillerTemplate(title="", description="")
    )

    # Postgame settings
    postgame_enabled: bool = True
    postgame_template: FillerTemplate = field(
        default_factory=lambda: FillerTemplate(title="", description="")
    )
    postgame_conditional: ConditionalFillerTemplate = field(
        default_factory=ConditionalFillerTemplate
    )

    # Idle settings
    idle_enabled: bool = True
    idle_template: FillerTemplate = field(
        default_factory=lambda: FillerTemplate(title="", description="")
    )
    idle_conditional: ConditionalFillerTemplate = field(default_factory=ConditionalFillerTemplate)
    idle_offseason: OffseasonFillerTemplate = field(default_factory=OffseasonFillerTemplate)

    # XMLTV categories (list for multiple categories)
    xmltv_categories: list[str] = field(default_factory=list)
    # Whether categories apply to filler ('all') or just events ('events')
    categories_apply_to: str = "events"


@dataclass
class FillerOptions:
    """Options for filler generation."""

    # EPG window
    output_days_ahead: int = 14
    lookback_hours: int = 6  # How far back to start EPG (for recently finished games)

    # Timezone for EPG
    epg_timezone: str = "America/New_York"

    # Midnight crossover mode: 'postgame' or 'idle'
    # Controls what fills the gap when a game crosses midnight
    # and there's no game the next day
    midnight_crossover_mode: str = "postgame"

    # Sport durations (hours) - loaded from settings
    # Keys are sport names (lowercase): basketball, football, hockey, baseball, soccer
    # If not provided, uses hardcoded defaults
    sport_durations: dict[str, float] = field(default_factory=dict)

    # Default duration if sport not found
    default_duration: float = 3.0

    # Pregame buffer (minutes) - gap between pregame filler end and game start
    # Set to 0 so pregame filler ends exactly when the game programme starts
    pregame_buffer_minutes: int = 0

    # Postponed event label
    # When True, prepends "Postponed: " to filler title, subtitle, and description
    # if the relevant event (next for pregame, last for postgame) is postponed
    prepend_postponed_label: bool = True
