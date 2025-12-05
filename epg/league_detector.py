"""
League Detector for Multi-Sport Event Groups

Detects the appropriate league for a stream when the group spans multiple sports/leagues.
Uses a tiered detection system with fallback strategies.

Detection Tiers:
    Tier 1: League indicator + Teams → Direct match (e.g., "NHL: Predators vs Panthers")
    Tier 2: Sport indicator + Teams → Match within sport's leagues
    Tier 3a: Teams + Date + Time → Exact schedule match across candidate leagues
    Tier 3b: Teams + Time only → Infer today's date, exact schedule match
    Tier 3c: Teams only → Closest game to now across candidate leagues

Usage:
    from epg.league_detector import LeagueDetector, DetectionResult

    detector = LeagueDetector(enabled_leagues=['nhl', 'nba', 'nfl', 'mlb'])

    result = detector.detect(
        stream_name="ESPN+ 51 : Nashville Predators vs. Florida Panthers",
        team1="Predators",
        team2="Panthers",
        game_date=None,
        game_time=None
    )

    if result.detected:
        print(f"League: {result.league} (Tier {result.tier})")
"""

import re
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any, Set, Tuple
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from utils.logger import get_logger

logger = get_logger(__name__)


# =============================================================================
# CONSTANTS
# =============================================================================

# League indicator patterns - these can appear ANYWHERE in stream name
# Maps pattern (case-insensitive) to league code
LEAGUE_INDICATORS = {
    # Hockey
    r'\bNHL\b': 'nhl',
    r'\bNational Hockey League\b': 'nhl',
    r'\bNCAA Hockey\b': 'ncaah',
    r'\bCollege Hockey\b': 'ncaah',

    # Basketball
    r'\bNBA\b': 'nba',
    r'\bNational Basketball Association\b': 'nba',
    r'\bNBA G[ -]?League\b': 'nba-g',
    r'\bG[ -]?League\b': 'nba-g',
    r'\bWNBA\b': 'wnba',
    r"\bWomen'?s NBA\b": 'wnba',
    r'\bNCAA Men\'?s Basketball\b': 'ncaam',
    r'\bMen\'?s College Basketball\b': 'ncaam',
    r'\bNCAA Women\'?s Basketball\b': 'ncaaw',
    r'\bWomen\'?s College Basketball\b': 'ncaaw',

    # Football
    r'\bNFL\b': 'nfl',
    r'\bNational Football League\b': 'nfl',
    r'\bCollege Football\b': 'ncaaf',
    r'\bNCAA Football\b': 'ncaaf',
    r'\bCFB\b': 'ncaaf',

    # Baseball
    r'\bMLB\b': 'mlb',
    r'\bMajor League Baseball\b': 'mlb',

    # Volleyball
    r'\bNCAA Men\'?s Volleyball\b': 'ncaavb-m',
    r'\bMen\'?s College Volleyball\b': 'ncaavb-m',
    r'\bNCAA Women\'?s Volleyball\b': 'ncaavb-w',
    r'\bWomen\'?s College Volleyball\b': 'ncaavb-w',
}

# Sport indicator patterns - maps to list of leagues for that sport
SPORT_INDICATORS = {
    r'\bHockey\b': ['nhl', 'ncaah'],
    r'\bBasketball\b': ['nba', 'nba-g', 'wnba', 'ncaam', 'ncaaw'],
    r'\bFootball\b': ['nfl', 'ncaaf'],
    r'\bBaseball\b': ['mlb'],
    r'\bVolleyball\b': ['ncaavb-m', 'ncaavb-w'],
    r'\bSoccer\b': [],  # Soccer uses SoccerMultiLeague, not handled here
}

# Map league codes to their sport for grouping
LEAGUE_TO_SPORT = {
    'nhl': 'hockey',
    'ncaah': 'hockey',
    'nba': 'basketball',
    'nba-g': 'basketball',
    'wnba': 'basketball',
    'ncaam': 'basketball',
    'ncaaw': 'basketball',
    'nfl': 'football',
    'ncaaf': 'football',
    'mlb': 'baseball',
    'ncaavb-m': 'volleyball',
    'ncaavb-w': 'volleyball',
}

# Time tolerance for schedule matching (±30 minutes)
TIME_TOLERANCE_MINUTES = 30


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class DetectionResult:
    """Result of league detection for a stream."""
    detected: bool
    league: Optional[str] = None
    sport: Optional[str] = None
    tier: Optional[int] = None  # 1, 2, 3 (3a/3b/3c reported as 3)
    tier_detail: Optional[str] = None  # '1', '2', '3a', '3b', '3c'
    method: Optional[str] = None  # Human-readable description
    candidates_checked: List[str] = None  # Leagues that were considered
    event_id: Optional[str] = None  # ESPN event ID if schedule-matched
    event_date: Optional[datetime] = None  # Event date/time if matched

    def __post_init__(self):
        if self.candidates_checked is None:
            self.candidates_checked = []


@dataclass
class ScheduleMatch:
    """A matching event from schedule search."""
    league: str
    event_id: str
    event_date: datetime
    home_team_id: str
    away_team_id: str
    time_diff_minutes: float  # Difference from target time (or from now)


# =============================================================================
# MAIN CLASS
# =============================================================================

class LeagueDetector:
    """
    Detects the appropriate league for multi-sport event groups.

    Uses tiered detection with fallback strategies.
    """

    def __init__(
        self,
        espn_client=None,
        enabled_leagues: List[str] = None,
        lookahead_days: int = 7
    ):
        """
        Initialize LeagueDetector.

        Args:
            espn_client: ESPNClient instance for schedule queries (optional for Tier 1/2)
            enabled_leagues: List of league codes to consider (None = all non-soccer)
            lookahead_days: How many days ahead to search for games
        """
        self.espn = espn_client
        self.lookahead_days = lookahead_days

        # Default to all non-soccer leagues if not specified
        if enabled_leagues is None:
            self.enabled_leagues = list(LEAGUE_TO_SPORT.keys())
        else:
            self.enabled_leagues = [l for l in enabled_leagues if l in LEAGUE_TO_SPORT]

        # Pre-compile league indicator patterns
        self._league_patterns = [
            (re.compile(pattern, re.IGNORECASE), league)
            for pattern, league in LEAGUE_INDICATORS.items()
        ]

        # Pre-compile sport indicator patterns
        self._sport_patterns = [
            (re.compile(pattern, re.IGNORECASE), leagues)
            for pattern, leagues in SPORT_INDICATORS.items()
        ]

    # ==========================================================================
    # PUBLIC API
    # ==========================================================================

    def detect(
        self,
        stream_name: str,
        team1: str = None,
        team2: str = None,
        team1_id: str = None,
        team2_id: str = None,
        game_date: datetime = None,
        game_time: datetime = None
    ) -> DetectionResult:
        """
        Detect the league for a stream.

        Tries detection tiers in order:
        1. League indicator in stream name + teams
        2. Sport indicator in stream name + teams
        3a. Teams + date + time → schedule match
        3b. Teams + time only → infer today, schedule match
        3c. Teams only → closest game to now

        Args:
            stream_name: Raw stream name (for indicator detection)
            team1: First team name (from TeamMatcher)
            team2: Second team name (from TeamMatcher)
            team1_id: ESPN team ID for team1 (if already resolved)
            team2_id: ESPN team ID for team2 (if already resolved)
            game_date: Extracted date from stream name (or None)
            game_time: Extracted time from stream name (or None)

        Returns:
            DetectionResult with league, tier, and method details
        """
        # Tier 1: Check for explicit league indicator
        result = self._detect_tier1(stream_name, team1, team2)
        if result.detected:
            return result

        # Tier 2: Check for sport indicator
        result = self._detect_tier2(stream_name, team1, team2)
        if result.detected:
            return result

        # Tier 3: Team-based lookup with schedule disambiguation
        if team1 and team2:
            result = self._detect_tier3(
                team1, team2,
                team1_id, team2_id,
                game_date, game_time
            )
            if result.detected:
                return result

        # No detection possible
        return DetectionResult(
            detected=False,
            method="No league detected - no indicators or team matches"
        )

    def find_candidate_leagues(self, team1: str, team2: str, include_soccer: bool = True) -> List[str]:
        """
        Find all enabled leagues where both teams might exist.

        Uses TeamLeagueCache for non-soccer teams and SoccerMultiLeague for soccer.

        Args:
            team1: First team name
            team2: Second team name
            include_soccer: Whether to also check soccer leagues (default True)

        Returns:
            List of league codes where both teams exist
        """
        from epg.team_league_cache import TeamLeagueCache

        # Get non-soccer leagues for these teams
        candidates = TeamLeagueCache.find_candidate_leagues(
            team1, team2,
            enabled_leagues=self.enabled_leagues
        )

        # Also check soccer leagues if enabled
        if include_soccer:
            try:
                soccer_leagues = self._find_soccer_leagues_for_teams(team1, team2)
                for league in soccer_leagues:
                    if league not in candidates:
                        candidates.append(league)
            except Exception as e:
                logger.debug(f"Error checking soccer leagues: {e}")

        return candidates

    def find_candidate_leagues_by_id(
        self,
        team1_id: str,
        team2_id: str
    ) -> List[str]:
        """
        Find all enabled leagues where both team IDs exist.

        Args:
            team1_id: ESPN team ID for first team
            team2_id: ESPN team ID for second team

        Returns:
            List of league codes where both teams exist
        """
        from epg.team_league_cache import TeamLeagueCache
        from database import get_connection

        # Query by team ID
        conn = get_connection()
        try:
            cursor = conn.cursor()

            # Find leagues for team1
            cursor.execute("""
                SELECT DISTINCT league_code FROM team_league_cache
                WHERE espn_team_id = ?
            """, (str(team1_id),))
            leagues1 = {row[0] for row in cursor.fetchall()}

            # Find leagues for team2
            cursor.execute("""
                SELECT DISTINCT league_code FROM team_league_cache
                WHERE espn_team_id = ?
            """, (str(team2_id),))
            leagues2 = {row[0] for row in cursor.fetchall()}

            # Intersection filtered by enabled leagues
            candidates = leagues1 & leagues2
            if self.enabled_leagues:
                candidates = candidates & set(self.enabled_leagues)

            return list(candidates)

        finally:
            conn.close()

    def _find_soccer_leagues_for_teams(
        self,
        team1: str,
        team2: str
    ) -> List[str]:
        """
        Find soccer leagues where both teams exist using the soccer_team_leagues cache.

        Unlike TeamLeagueCache (which uses team IDs), this queries by team name
        since we may not have ESPN team IDs yet during detection.

        Args:
            team1: First team name
            team2: Second team name

        Returns:
            List of soccer league slugs where both teams exist
        """
        from database import get_connection

        if not team1 or not team2:
            return []

        conn = get_connection()
        try:
            cursor = conn.cursor()

            # Normalize team names for fuzzy matching
            # Use LIKE for partial matching since team names may vary
            team1_lower = team1.lower().strip()
            team2_lower = team2.lower().strip()

            # Find leagues for team1 (case-insensitive match)
            cursor.execute("""
                SELECT DISTINCT league_slug FROM soccer_team_leagues
                WHERE LOWER(team_name) LIKE ? OR LOWER(team_name) LIKE ?
            """, (f"%{team1_lower}%", f"{team1_lower}%"))
            leagues1 = {row[0] for row in cursor.fetchall()}

            if not leagues1:
                return []

            # Find leagues for team2
            cursor.execute("""
                SELECT DISTINCT league_slug FROM soccer_team_leagues
                WHERE LOWER(team_name) LIKE ? OR LOWER(team_name) LIKE ?
            """, (f"%{team2_lower}%", f"{team2_lower}%"))
            leagues2 = {row[0] for row in cursor.fetchall()}

            if not leagues2:
                return []

            # Intersection - leagues where both teams exist
            candidates = leagues1 & leagues2

            logger.debug(
                f"Soccer leagues for '{team1}' vs '{team2}': "
                f"team1={len(leagues1)}, team2={len(leagues2)}, "
                f"intersection={len(candidates)}"
            )

            return list(candidates)

        except Exception as e:
            logger.debug(f"Error querying soccer_team_leagues: {e}")
            return []
        finally:
            conn.close()

    # ==========================================================================
    # TIER 1: League Indicator Detection
    # ==========================================================================

    def _detect_tier1(
        self,
        stream_name: str,
        team1: str,
        team2: str
    ) -> DetectionResult:
        """
        Tier 1: Detect league from explicit league indicator in stream name.

        Example: "NHL: Predators vs Panthers" → NHL
        """
        detected_league = None

        for pattern, league in self._league_patterns:
            if pattern.search(stream_name):
                # Check if this league is enabled
                if league in self.enabled_leagues:
                    detected_league = league
                    break

        if not detected_league:
            return DetectionResult(detected=False)

        # Validate teams exist in this league
        if team1 and team2:
            candidates = self.find_candidate_leagues(team1, team2)
            if detected_league not in candidates:
                logger.debug(
                    f"Tier 1: League indicator {detected_league} found but teams "
                    f"'{team1}' vs '{team2}' not found in that league"
                )
                return DetectionResult(
                    detected=False,
                    method=f"League indicator {detected_league} found but teams not in league",
                    candidates_checked=[detected_league]
                )

        sport = LEAGUE_TO_SPORT.get(detected_league)

        return DetectionResult(
            detected=True,
            league=detected_league,
            sport=sport,
            tier=1,
            tier_detail='1',
            method=f"League indicator '{detected_league.upper()}' found in stream name",
            candidates_checked=[detected_league]
        )

    # ==========================================================================
    # TIER 2: Sport Indicator Detection
    # ==========================================================================

    def _detect_tier2(
        self,
        stream_name: str,
        team1: str,
        team2: str
    ) -> DetectionResult:
        """
        Tier 2: Detect league from sport indicator + team lookup.

        Example: "Hockey: Predators vs Panthers" + teams in NHL → NHL
        """
        detected_sport = None
        sport_leagues = []

        for pattern, leagues in self._sport_patterns:
            if pattern.search(stream_name):
                detected_sport = pattern.pattern.strip(r'\b')
                sport_leagues = [l for l in leagues if l in self.enabled_leagues]
                break

        if not sport_leagues:
            return DetectionResult(detected=False)

        # If we have teams, find which sport league(s) they're in
        if team1 and team2:
            candidates = self.find_candidate_leagues(team1, team2)
            matching_leagues = [l for l in candidates if l in sport_leagues]

            if len(matching_leagues) == 1:
                league = matching_leagues[0]
                return DetectionResult(
                    detected=True,
                    league=league,
                    sport=LEAGUE_TO_SPORT.get(league),
                    tier=2,
                    tier_detail='2',
                    method=f"Sport indicator '{detected_sport}' + teams in {league.upper()}",
                    candidates_checked=sport_leagues
                )
            elif len(matching_leagues) > 1:
                # Multiple leagues within sport - need Tier 3 disambiguation
                logger.debug(
                    f"Tier 2: Sport {detected_sport} found, multiple league matches: {matching_leagues}"
                )
                return DetectionResult(detected=False, candidates_checked=matching_leagues)
            else:
                # No matching leagues for these teams
                logger.debug(
                    f"Tier 2: Sport {detected_sport} found but teams not in any {sport_leagues}"
                )
                return DetectionResult(detected=False, candidates_checked=sport_leagues)

        # Sport indicator but no teams to validate
        return DetectionResult(detected=False)

    # ==========================================================================
    # TIER 3: Schedule-Based Disambiguation
    # ==========================================================================

    def _detect_tier3(
        self,
        team1: str,
        team2: str,
        team1_id: str = None,
        team2_id: str = None,
        game_date: datetime = None,
        game_time: datetime = None
    ) -> DetectionResult:
        """
        Tier 3: Detect league via team lookup + schedule disambiguation.

        3a: Date + time → exact schedule match
        3b: Time only → infer today, exact schedule match
        3c: Teams only → closest game to now
        """
        # Find candidate leagues for these teams
        if team1_id and team2_id:
            candidates = self.find_candidate_leagues_by_id(team1_id, team2_id)
        else:
            candidates = self.find_candidate_leagues(team1, team2)

        if not candidates:
            return DetectionResult(
                detected=False,
                method=f"No leagues found containing both '{team1}' and '{team2}'"
            )

        if len(candidates) == 1:
            # Unambiguous - only one league has both teams
            league = candidates[0]
            return DetectionResult(
                detected=True,
                league=league,
                sport=LEAGUE_TO_SPORT.get(league),
                tier=3,
                tier_detail='3c',  # Single candidate, no schedule check needed
                method=f"Only league with both teams: {league.upper()}",
                candidates_checked=candidates
            )

        # Multiple candidates - need schedule disambiguation
        if not self.espn:
            logger.warning("Multiple league candidates but no ESPN client for schedule check")
            return DetectionResult(
                detected=False,
                method=f"Multiple candidates {candidates} but no ESPN client",
                candidates_checked=candidates
            )

        # Determine tier based on available date/time
        if game_date and game_time:
            # Tier 3a: Exact date + time
            return self._detect_tier3a(
                team1_id or team1,
                team2_id or team2,
                candidates,
                game_date,
                game_time
            )
        elif game_time:
            # Tier 3b: Time only, infer today
            return self._detect_tier3b(
                team1_id or team1,
                team2_id or team2,
                candidates,
                game_time
            )
        else:
            # Tier 3c: Teams only, find closest game
            return self._detect_tier3c(
                team1_id or team1,
                team2_id or team2,
                candidates
            )

    def _detect_tier3a(
        self,
        team1: str,
        team2: str,
        candidates: List[str],
        game_date: datetime,
        game_time: datetime
    ) -> DetectionResult:
        """
        Tier 3a: Date + time available, find exact schedule match.
        """
        # Combine date and time
        target_dt = datetime.combine(
            game_date.date(),
            game_time.time(),
            tzinfo=game_time.tzinfo or ZoneInfo('UTC')
        )

        matches = self._search_schedules(
            team1, team2, candidates,
            target_datetime=target_dt,
            tolerance_minutes=TIME_TOLERANCE_MINUTES
        )

        if len(matches) == 1:
            match = matches[0]
            return DetectionResult(
                detected=True,
                league=match.league,
                sport=LEAGUE_TO_SPORT.get(match.league),
                tier=3,
                tier_detail='3a',
                method=f"Schedule match: {match.league.upper()} at {match.event_date}",
                candidates_checked=candidates,
                event_id=match.event_id,
                event_date=match.event_date
            )
        elif len(matches) > 1:
            # Multiple matches - extremely rare (identical game times)
            leagues = list(set(m.league for m in matches))
            logger.warning(f"Tier 3a: Multiple schedule matches: {leagues}")
            return DetectionResult(
                detected=False,
                method=f"Ambiguous: multiple games at {target_dt} in {leagues}",
                candidates_checked=candidates
            )
        else:
            return DetectionResult(
                detected=False,
                method=f"No game found at {target_dt} in {candidates}",
                candidates_checked=candidates
            )

    def _detect_tier3b(
        self,
        team1: str,
        team2: str,
        candidates: List[str],
        game_time: datetime
    ) -> DetectionResult:
        """
        Tier 3b: Time only, infer today's date.
        """
        # Use today's date with the given time
        now = datetime.now(ZoneInfo('UTC'))
        target_dt = datetime.combine(
            now.date(),
            game_time.time(),
            tzinfo=game_time.tzinfo or ZoneInfo('UTC')
        )

        matches = self._search_schedules(
            team1, team2, candidates,
            target_datetime=target_dt,
            tolerance_minutes=TIME_TOLERANCE_MINUTES
        )

        if len(matches) == 1:
            match = matches[0]
            return DetectionResult(
                detected=True,
                league=match.league,
                sport=LEAGUE_TO_SPORT.get(match.league),
                tier=3,
                tier_detail='3b',
                method=f"Schedule match (inferred today): {match.league.upper()} at {match.event_date}",
                candidates_checked=candidates,
                event_id=match.event_id,
                event_date=match.event_date
            )
        elif len(matches) > 1:
            leagues = list(set(m.league for m in matches))
            logger.warning(f"Tier 3b: Multiple schedule matches: {leagues}")
            return DetectionResult(
                detected=False,
                method=f"Ambiguous: multiple games at {target_dt} in {leagues}",
                candidates_checked=candidates
            )
        else:
            return DetectionResult(
                detected=False,
                method=f"No game found today at {game_time.strftime('%H:%M')} in {candidates}",
                candidates_checked=candidates
            )

    def _detect_tier3c(
        self,
        team1: str,
        team2: str,
        candidates: List[str]
    ) -> DetectionResult:
        """
        Tier 3c: Teams only, find closest game to now.
        """
        matches = self._search_schedules(
            team1, team2, candidates,
            target_datetime=None,  # Will search full lookahead
            tolerance_minutes=None  # Return all matches
        )

        if not matches:
            return DetectionResult(
                detected=False,
                method=f"No upcoming games found for teams in {candidates}",
                candidates_checked=candidates
            )

        # Sort by absolute time difference from now
        now = datetime.now(ZoneInfo('UTC'))
        matches.sort(key=lambda m: abs((m.event_date - now).total_seconds()))

        closest = matches[0]

        # Check for tie (multiple games equally close - within 5 minutes)
        if len(matches) > 1:
            second = matches[1]
            time_diff = abs((closest.event_date - second.event_date).total_seconds())
            if time_diff < 300:  # 5 minutes
                leagues = [closest.league, second.league]
                logger.warning(f"Tier 3c: Tie between {leagues}")
                return DetectionResult(
                    detected=False,
                    method=f"Tie: games in {leagues} at similar times",
                    candidates_checked=candidates
                )

        return DetectionResult(
            detected=True,
            league=closest.league,
            sport=LEAGUE_TO_SPORT.get(closest.league),
            tier=3,
            tier_detail='3c',
            method=f"Closest game: {closest.league.upper()} at {closest.event_date}",
            candidates_checked=candidates,
            event_id=closest.event_id,
            event_date=closest.event_date
        )

    # ==========================================================================
    # SCHEDULE SEARCH HELPERS
    # ==========================================================================

    def _search_schedules(
        self,
        team1: str,
        team2: str,
        candidates: List[str],
        target_datetime: datetime = None,
        tolerance_minutes: int = None
    ) -> List[ScheduleMatch]:
        """
        Search schedules across multiple leagues for a team matchup.

        Args:
            team1: Team name or ID
            team2: Team name or ID
            candidates: List of league codes to search
            target_datetime: Specific datetime to match (None = search all)
            tolerance_minutes: Time tolerance for matching (None = any time)

        Returns:
            List of ScheduleMatch objects
        """
        from epg.league_config import get_league_config, parse_api_path

        matches = []
        now = datetime.now(ZoneInfo('UTC'))
        cutoff_future = now + timedelta(days=self.lookahead_days)
        cutoff_past = now - timedelta(days=1)  # Include games from yesterday

        for league in candidates:
            try:
                config = get_league_config(league)
                if not config:
                    continue

                sport, api_league = parse_api_path(config['api_path'])
                if not sport:
                    continue

                # Search team1's schedule
                schedule = self.espn.get_team_schedule(sport, api_league, str(team1))
                if not schedule or 'events' not in schedule:
                    continue

                for event in schedule.get('events', []):
                    try:
                        event_date_str = event.get('date', '')
                        if not event_date_str:
                            continue

                        event_date = datetime.fromisoformat(
                            event_date_str.replace('Z', '+00:00')
                        )

                        # Skip events outside window
                        if event_date < cutoff_past or event_date > cutoff_future:
                            continue

                        # Check if team2 is in this game
                        competitions = event.get('competitions', [])
                        if not competitions:
                            continue

                        competitors = competitions[0].get('competitors', [])
                        team_ids = [
                            str(c.get('team', {}).get('id', c.get('id')))
                            for c in competitors
                        ]

                        if str(team2) not in team_ids:
                            continue

                        # Calculate time difference
                        if target_datetime:
                            time_diff = abs((event_date - target_datetime).total_seconds() / 60)

                            # Apply tolerance filter
                            if tolerance_minutes and time_diff > tolerance_minutes:
                                continue
                        else:
                            time_diff = abs((event_date - now).total_seconds() / 60)

                        # Extract home/away
                        home_id = away_id = None
                        for c in competitors:
                            tid = str(c.get('team', {}).get('id', c.get('id')))
                            if c.get('homeAway') == 'home':
                                home_id = tid
                            else:
                                away_id = tid

                        matches.append(ScheduleMatch(
                            league=league,
                            event_id=event.get('id'),
                            event_date=event_date,
                            home_team_id=home_id,
                            away_team_id=away_id,
                            time_diff_minutes=time_diff
                        ))

                    except Exception as e:
                        logger.debug(f"Error parsing event in {league}: {e}")
                        continue

            except Exception as e:
                logger.warning(f"Error searching {league} schedule: {e}")
                continue

        return matches


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def create_league_detector(
    enabled_leagues: List[str] = None,
    lookahead_days: int = 7
) -> LeagueDetector:
    """
    Create a LeagueDetector with default configuration.

    Args:
        enabled_leagues: List of league codes to consider
        lookahead_days: How many days ahead to search

    Returns:
        Configured LeagueDetector instance
    """
    from api.espn_client import ESPNClient

    espn = ESPNClient()
    return LeagueDetector(
        espn_client=espn,
        enabled_leagues=enabled_leagues,
        lookahead_days=lookahead_days
    )
