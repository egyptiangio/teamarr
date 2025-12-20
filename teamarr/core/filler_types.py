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

    Populated from database templates table.
    """

    # Pregame settings
    pregame_enabled: bool = True
    pregame_template: FillerTemplate = field(
        default_factory=lambda: FillerTemplate(
            title="Pregame Coverage",
            description="{team_name} vs {opponent.next} starts at {game_time.next}",
        )
    )

    # Postgame settings
    postgame_enabled: bool = True
    postgame_template: FillerTemplate = field(
        default_factory=lambda: FillerTemplate(
            title="Postgame Recap",
            description="{team_name} {result_text.last} {final_score.last}",
        )
    )
    postgame_conditional: ConditionalFillerTemplate = field(
        default_factory=ConditionalFillerTemplate
    )

    # Idle settings
    idle_enabled: bool = True
    idle_template: FillerTemplate = field(
        default_factory=lambda: FillerTemplate(
            title="{team_name} Programming",
            description="Next game: {game_date.next} vs {opponent.next}",
        )
    )
    idle_conditional: ConditionalFillerTemplate = field(default_factory=ConditionalFillerTemplate)
    idle_offseason: OffseasonFillerTemplate = field(default_factory=OffseasonFillerTemplate)

    # Category for filler content
    category: str = "Sports"


@dataclass
class FillerOptions:
    """Options for filler generation."""

    # EPG window
    output_days_ahead: int = 14

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

    # Pregame buffer (minutes) - must match the value used in TeamEPGOptions
    # The actual game programme starts this many minutes before event.start_time
    # Filler should end when the game PROGRAMME starts, not the event
    pregame_buffer_minutes: int = 30
