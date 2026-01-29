"""Cricket hybrid provider implementation.

Combines:
- TSDB: Team info, logos (from team_cache database)
- Cricbuzz: Event schedules, live scores

This abstraction lets the service layer treat cricket as a single provider,
unaware that data flows from multiple sources underneath.
"""

import dataclasses
import logging
from datetime import date

from teamarr.core import Event, SportsProvider, Team, TeamStats
from teamarr.core.interfaces import LeagueMappingSource

logger = logging.getLogger(__name__)


class CricketHybridProvider(SportsProvider):
    """Hybrid provider for cricket leagues without TSDB premium.

    Architecture:
    - Events/schedules: Cricbuzz (web scraping)
    - Team logos/info: TSDB (from seeded team_cache)

    The service layer just sees this as another provider.
    """

    def __init__(
        self,
        cricbuzz_provider: SportsProvider,
        league_mapping_source: LeagueMappingSource,
        db_factory: callable,
    ):
        """Initialize hybrid provider.

        Args:
            cricbuzz_provider: Cricbuzz provider for events
            league_mapping_source: For league config lookups
            db_factory: Database connection factory (get_db)
        """
        self._cricbuzz = cricbuzz_provider
        self._league_mapping_source = league_mapping_source
        self._db_factory = db_factory
        self._team_logo_cache: dict[str, str | None] = {}

    @property
    def name(self) -> str:
        return "cricket_hybrid"

    def supports_league(self, league: str) -> bool:
        """Support leagues that have Cricbuzz as fallback provider."""
        mapping = self._league_mapping_source.get_mapping_by_league(league)
        if not mapping:
            return False
        return mapping.fallback_provider == "cricbuzz"

    def get_events(self, league: str, target_date: date) -> list[Event]:
        """Get events from Cricbuzz, enriched with TSDB team logos."""
        # Get Cricbuzz league ID from fallback config
        cricbuzz_league_id = self._get_cricbuzz_league_id(league)
        if not cricbuzz_league_id:
            logger.warning("[CRICKET_HYBRID] No Cricbuzz fallback configured for %s", league)
            return []

        # Get events from Cricbuzz
        events = self._cricbuzz.get_events(cricbuzz_league_id, target_date)

        # Enrich with TSDB team data (logos)
        enriched = []
        for event in events:
            try:
                enriched.append(self._enrich_event(event, league))
            except Exception as e:
                logger.warning("[CRICKET_HYBRID] Failed to enrich event %s: %s", event.id, e)
                enriched.append(event)  # Use original if enrichment fails

        return enriched

    def get_team_schedule(self, team_id: str, league: str, days_ahead: int = 14) -> list[Event]:
        """Get team schedule from Cricbuzz, enriched with TSDB logos."""
        cricbuzz_league_id = self._get_cricbuzz_league_id(league)
        if not cricbuzz_league_id:
            return []

        events = self._cricbuzz.get_team_schedule(team_id, cricbuzz_league_id, days_ahead)
        return [self._enrich_event(e, league) for e in events]

    def get_team(self, team_id: str, league: str) -> Team | None:
        """Get team info - prefer TSDB cache for logo."""
        # Try to find team in TSDB cache first
        tsdb_team = self._get_cached_team(team_id, league)
        if tsdb_team:
            return tsdb_team

        # Fallback to Cricbuzz
        cricbuzz_league_id = self._get_cricbuzz_league_id(league)
        if cricbuzz_league_id:
            return self._cricbuzz.get_team(team_id, cricbuzz_league_id)

        return None

    def get_event(self, event_id: str, league: str) -> Event | None:
        """Get single event from Cricbuzz, enriched."""
        cricbuzz_league_id = self._get_cricbuzz_league_id(league)
        if not cricbuzz_league_id:
            return None

        event = self._cricbuzz.get_event(event_id, cricbuzz_league_id)
        if event:
            return self._enrich_event(event, league)
        return None

    def get_team_stats(self, team_id: str, league: str) -> TeamStats | None:
        """Team stats not available in hybrid mode."""
        # TSDB free tier doesn't provide stats, Cricbuzz doesn't either
        return None

    def _get_cricbuzz_league_id(self, league: str) -> str | None:
        """Get Cricbuzz series ID from league mapping."""
        mapping = self._league_mapping_source.get_mapping_by_league(league)
        if mapping and mapping.fallback_league_id:
            return mapping.fallback_league_id
        return None

    def _enrich_event(self, event: Event, league: str) -> Event:
        """Enrich event with TSDB team logos."""
        home_team = self._enrich_team(event.home_team, league)
        away_team = self._enrich_team(event.away_team, league)

        # Only create new event if teams changed
        if home_team is event.home_team and away_team is event.away_team:
            return event

        return dataclasses.replace(event, home_team=home_team, away_team=away_team)

    def _enrich_team(self, team: Team, league: str) -> Team:
        """Enrich team with TSDB logo - always prefer TSDB over Cricbuzz."""
        # Look up TSDB logo from cache (preferred source)
        tsdb_logo = self._get_team_logo(team.name, league)
        if tsdb_logo:
            return dataclasses.replace(team, logo_url=tsdb_logo)

        # Fall back to existing logo (from Cricbuzz) if TSDB not found
        return team

    def _get_team_logo(self, team_name: str, league: str) -> str | None:
        """Get team logo from TSDB team_cache."""
        cache_key = f"{league}:{team_name}"

        # Check in-memory cache
        if cache_key in self._team_logo_cache:
            return self._team_logo_cache[cache_key]

        # Query database
        logo_url = self._lookup_team_logo(team_name, league)
        self._team_logo_cache[cache_key] = logo_url
        return logo_url

    def _lookup_team_logo(self, team_name: str, league: str) -> str | None:
        """Query team_cache for logo URL."""
        with self._db_factory() as db:
            # Exact match first
            cursor = db.execute(
                """
                SELECT logo_url FROM team_cache
                WHERE league = ? AND team_name = ? AND logo_url IS NOT NULL
                """,
                (league, team_name),
            )
            row = cursor.fetchone()
            if row:
                return row["logo_url"]

            # Fuzzy match - team name might differ slightly
            # e.g., "Durban Super Giants" vs "Durban's Super Giants"
            cursor = db.execute(
                """
                SELECT team_name, logo_url FROM team_cache
                WHERE league = ? AND logo_url IS NOT NULL
                """,
                (league,),
            )
            teams = cursor.fetchall()

            # Simple fuzzy: check if names contain each other's key words
            team_name_lower = team_name.lower()
            for row in teams:
                cached_name_lower = row["team_name"].lower()
                # Check for significant overlap
                if self._names_match(team_name_lower, cached_name_lower):
                    logger.debug(
                        "[CRICKET_HYBRID] Fuzzy matched team '%s' -> '%s'",
                        team_name,
                        row["team_name"],
                    )
                    return row["logo_url"]

        return None

    def _names_match(self, name1: str, name2: str) -> bool:
        """Check if two team names likely refer to the same team."""
        # Remove common suffixes and check core name
        for suffix in ["fc", "sc", "afc", "united", "city"]:
            name1 = name1.replace(suffix, "").strip()
            name2 = name2.replace(suffix, "").strip()

        # Check if one contains the other's main word
        words1 = set(name1.split())
        words2 = set(name2.split())

        # Remove common words
        common_words = {"the", "de", "fc", "sc", "super", "kings", "royals"}
        words1 = words1 - common_words
        words2 = words2 - common_words

        # Need at least one significant word overlap
        overlap = words1 & words2
        if overlap:
            return True

        # Check for substring match (e.g., "Mumbai" in "MI Mumbai")
        for w1 in words1:
            if len(w1) > 3:  # Skip short words
                for w2 in words2:
                    if w1 in w2 or w2 in w1:
                        return True

        return False

    def _get_cached_team(self, team_id: str, league: str) -> Team | None:
        """Get full team info from cache if available."""
        with self._db_factory() as db:
            cursor = db.execute(
                """
                SELECT team_name, team_short_name, team_abbrev, logo_url, sport
                FROM team_cache
                WHERE league = ? AND provider_team_id = ?
                """,
                (league, team_id),
            )
            row = cursor.fetchone()
            if row:
                return Team(
                    id=team_id,
                    provider="tsdb",
                    name=row["team_name"],
                    short_name=row["team_short_name"] or row["team_name"][:10],
                    abbreviation=row["team_abbrev"] or row["team_name"][:3].upper(),
                    league=league,
                    sport=row["sport"] or "Cricket",
                    logo_url=row["logo_url"],
                )
        return None

    def clear_cache(self) -> None:
        """Clear internal caches."""
        self._team_logo_cache.clear()
        if hasattr(self._cricbuzz, "clear_cache"):
            self._cricbuzz.clear_cache()
