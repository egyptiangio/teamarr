"""Template context dataclasses.

These typed dataclasses hold all data needed for template variable resolution.
Using dataclasses instead of dicts provides type safety and IDE support.
"""

from dataclasses import dataclass

from core import Event, Team, TeamStats


@dataclass
class HeadToHead:
    """Head-to-head record against current opponent."""

    team_wins: int = 0
    opponent_wins: int = 0
    previous_result: str | None = None  # "Win", "Loss", "Tie"
    previous_score: str | None = None  # "24-17"
    previous_venue: str | None = None
    previous_city: str | None = None
    days_since: int = 0


@dataclass
class Streaks:
    """Calculated streak data from team schedule."""

    overall: int = 0  # positive = wins, negative = losses
    home_streak: str = ""  # "W3" or "L2"
    away_streak: str = ""
    last_5_record: str = ""  # "4-1" or "3-1-1" for soccer
    last_10_record: str = ""


@dataclass
class PlayerLeaders:
    """Sport-specific player stat leaders (postgame only)."""

    # Basketball
    scoring_leader_name: str = ""
    scoring_leader_points: str = ""

    # Football
    passing_leader_name: str = ""
    passing_leader_stats: str = ""  # "285 YDS, 2 TD"
    rushing_leader_name: str = ""
    rushing_leader_stats: str = ""
    receiving_leader_name: str = ""
    receiving_leader_stats: str = ""


@dataclass
class Odds:
    """Betting odds for a game (available same-day only from scoreboard API)."""

    provider: str = ""  # "ESPN BET", "DraftKings", etc.
    spread: float = 0.0  # Point spread (absolute value)
    over_under: float = 0.0  # Total points line
    details: str = ""  # Full odds description
    team_moneyline: int = 0  # Our team's moneyline
    opponent_moneyline: int = 0  # Opponent's moneyline
    opponent_spread_odds: int = 0  # Opponent's spread odds


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
    h2h: HeadToHead | None = None
    streaks: Streaks | None = None
    odds: Odds | None = None
    head_coach: str = ""
    player_leaders: PlayerLeaders | None = None


@dataclass
class TeamConfig:
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
    team_config: TeamConfig
    team_stats: TeamStats | None

    # Team object (convenience field)
    team: Team | None = None

    # Related games for suffix resolution
    next_game: GameContext | None = None  # For .next suffix
    last_game: GameContext | None = None  # For .last suffix
