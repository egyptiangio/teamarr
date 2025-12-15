"""Sport and league utilities (FALLBACK ONLY).

The authoritative source for sport is the data provider (ESPN, TSDB, etc.).
Use event.sport or team.sport when available.

These utilities are ONLY for edge cases where no Event/Team is available:
- Filler generation with empty events list
- Minimal context building without events

When a provider is available, it knows what sport each league belongs to.
"""

# League to sport mapping
# Key: league identifier (lowercase)
# Value: human-readable sport name
LEAGUE_SPORT_MAP = {
    # American Football
    "nfl": "Football",
    "college-football": "Football",
    # Basketball
    "nba": "Basketball",
    "wnba": "Basketball",
    "mens-college-basketball": "Basketball",
    "womens-college-basketball": "Basketball",
    # Hockey
    "nhl": "Hockey",
    # Baseball
    "mlb": "Baseball",
    # Soccer (US)
    "mls": "Soccer",
    # MMA
    "ufc": "MMA",
}

# Country codes that indicate soccer leagues (e.g., "eng.1", "ger.1")
SOCCER_COUNTRY_CODES = frozenset({
    "ger",  # Germany
    "eng",  # England
    "esp",  # Spain
    "ita",  # Italy
    "fra",  # France
    "usa",  # USA (non-MLS leagues)
    "aus",  # Australia
    "ned",  # Netherlands
    "por",  # Portugal
    "sco",  # Scotland
    "bel",  # Belgium
    "mex",  # Mexico
    "bra",  # Brazil
    "arg",  # Argentina
})


def get_sport_from_league(league: str) -> str:
    """Derive sport name from league identifier (FALLBACK).

    PREFER using event.sport or team.sport when available.
    The data provider (ESPN, TSDB) is the authoritative source.

    This is only for edge cases where no Event/Team is available.

    Args:
        league: League identifier (e.g., 'nfl', 'nba', 'eng.1')

    Returns:
        Human-readable sport name (e.g., 'Football', 'Basketball', 'Soccer')

    Examples:
        >>> get_sport_from_league('nfl')
        'Football'
        >>> get_sport_from_league('eng.1')
        'Soccer'
        >>> get_sport_from_league('unknown')
        'Sports'
    """
    league_lower = league.lower()

    # Check for soccer-style leagues (country.division format)
    if "." in league_lower:
        parts = league_lower.split(".")
        if parts[0] in SOCCER_COUNTRY_CODES:
            return "Soccer"
        # Unknown dotted format - default to Sports
        return "Sports"

    # Look up in standard map
    return LEAGUE_SPORT_MAP.get(league_lower, "Sports")


def is_soccer_league(league: str) -> bool:
    """Check if a league is a soccer league.

    Args:
        league: League identifier

    Returns:
        True if the league is soccer
    """
    return get_sport_from_league(league) == "Soccer"


def get_sport_duration(
    sport: str,
    sport_durations: dict[str, float],
    default: float = 3.0,
) -> float:
    """Get duration for a sport from settings.

    Args:
        sport: Sport name (e.g., 'Basketball', 'Football')
        sport_durations: Durations dict from database settings
        default: Default duration if sport not found

    Returns:
        Duration in hours
    """
    return sport_durations.get(sport.lower(), default)
