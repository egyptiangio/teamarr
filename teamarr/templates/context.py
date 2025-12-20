"""Template context dataclasses.

These typed dataclasses hold all data needed for template variable resolution.
Using dataclasses instead of dicts provides type safety and IDE support.
"""

from dataclasses import dataclass

from teamarr.core import Event, Team, TeamStats


@dataclass
class Odds:
    """Betting odds for a game (available ~1 week out from summary endpoint)."""

    provider: str = ""  # "ESPN BET", "DraftKings", etc.
    spread: float = 0.0  # Point spread (absolute value)
    over_under: float = 0.0  # Total points line
    details: str = ""  # Full odds description
    team_moneyline: int = 0  # Our team's moneyline
    opponent_moneyline: int = 0  # Opponent's moneyline


@dataclass
class GameContext:
    """Context for a single game (current, next, or last).

    This is used three times per template resolution:
    - Current game context (base variables)
    - Next game context (.next suffix)
    - Last game context (.last suffix)
    """

    event: Event | None = None

    # Home/away context (computed from event + team_id)
    is_home: bool = True
    team: Team | None = None  # Our team
    opponent: Team | None = None  # Opponent team

    # Additional context
    opponent_stats: TeamStats | None = None
    odds: Odds | None = None


@dataclass
class TeamChannelContext:
    """Team channel configuration from database."""

    team_id: str
    league: str
    sport: str
    team_name: str
    team_abbrev: str | None = None
    team_logo_url: str | None = None
    league_name: str | None = None  # "NFL", "NBA", etc.
    channel_id: str | None = None

    # Soccer-specific
    soccer_primary_league: str | None = None
    soccer_primary_league_id: str | None = None


@dataclass
class TemplateContext:
    """Complete context for template resolution.

    This is the top-level context passed to the template resolver.
    Contains current game, next game, last game, and team-level data.
    """

    # Current game context (for base variables)
    game_context: GameContext | None

    # Team identity and season stats
    team_config: TeamChannelContext
    team_stats: TeamStats | None

    # Team object (convenience field)
    team: Team | None = None

    # Related games for suffix resolution
    next_game: GameContext | None = None  # For .next suffix
    last_game: GameContext | None = None  # For .last suffix
