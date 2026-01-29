"""League mapping service.

Provides database-backed implementation of LeagueMappingSource.
Providers depend on this service, not the database directly.

IMPORTANT: All league mappings are cached in memory at initialization.
This is critical for thread-safety during parallel team processing.
"""

import logging
from collections.abc import Callable, Generator
from sqlite3 import Connection

from teamarr.core import LeagueMapping
from teamarr.core.sports import get_sport_display_names_from_db

logger = logging.getLogger(__name__)


class LeagueMappingService:
    """Cached league mapping source.

    Implements the LeagueMappingSource protocol defined in core.
    Providers receive an instance of this service at construction time.

    THREAD-SAFETY: All mappings are loaded into memory at initialization.
    No database access occurs after initialization, making this safe for
    use in parallel processing threads.

    Provides methods for template variable resolution:
    - get_league_alias(league_code) - for {league}: league_alias or display_name
    - get_league_id(league_code) - for {league_id}: league_id or league_code
    - get_league_display_name(league_code) - for {league_name}: display_name
    """

    def __init__(
        self,
        db_getter: Callable[[], Generator[Connection, None, None]],
    ):
        self._db_getter = db_getter
        # Cache all mappings at initialization for thread-safety
        self._mappings: dict[tuple[str, str], LeagueMapping] = {}
        self._provider_leagues: dict[str, list[LeagueMapping]] = {}
        # Template variable caches (league_code -> value)
        # {league}: league_alias if set, else display_name
        self._league_aliases: dict[str, str] = {}
        # {league_id}: league_id if set, else league_code (handled in getter)
        self._league_ids: dict[str, str] = {}
        # {league_name}: display_name (always)
        self._league_display_names: dict[str, str] = {}
        # {gracenote_category}: curated value or auto-generated
        self._gracenote_categories: dict[str, str] = {}
        # Sport per league (for gracenote_category fallback)
        self._league_sports: dict[str, str] = {}
        # Fallback from league_cache for discovered leagues
        self._league_cache_names: dict[str, str] = {}
        # {sport}: sport display name (e.g., 'mma' -> 'MMA')
        self._sport_display_names: dict[str, str] = {}
        self._load_all_mappings()

    def _load_all_mappings(self) -> None:
        """Load all league mappings into memory.

        Called once at initialization. After this, no DB access is needed.
        Also loads template variable values:
        - league_alias for {league}
        - league_id for {league_id}
        - display_name for {league_name}
        """
        with self._db_getter() as conn:
            # Load configured leagues
            cursor = conn.execute(
                """
                SELECT league_code, provider, provider_league_id,
                       provider_league_name, sport, display_name, logo_url,
                       league_alias, league_id, gracenote_category,
                       fallback_provider, fallback_league_id
                FROM leagues
                WHERE enabled = 1
                ORDER BY provider, league_code
                """
            )
            for row in cursor.fetchall():
                mapping = LeagueMapping(
                    league_code=row["league_code"],
                    provider=row["provider"],
                    provider_league_id=row["provider_league_id"],
                    provider_league_name=row["provider_league_name"],
                    sport=row["sport"],
                    display_name=row["display_name"],
                    logo_url=row["logo_url"],
                    league_id=row["league_id"],
                    fallback_provider=row["fallback_provider"],
                    fallback_league_id=row["fallback_league_id"],
                )
                # Index by (league_code, provider) for fast lookup
                key = (row["league_code"].lower(), row["provider"])
                self._mappings[key] = mapping

                # Also index by provider for get_leagues_for_provider
                if row["provider"] not in self._provider_leagues:
                    self._provider_leagues[row["provider"]] = []
                self._provider_leagues[row["provider"]].append(mapping)

                league_code_lower = row["league_code"].lower()

                # Cache league_alias for {league} - fallback to display_name
                if row["league_alias"]:
                    self._league_aliases[league_code_lower] = row["league_alias"]
                elif row["display_name"]:
                    self._league_aliases[league_code_lower] = row["display_name"]

                # Cache league_id for {league_id}
                if row["league_id"]:
                    self._league_ids[league_code_lower] = row["league_id"]

                # Cache display_name for {league_name}
                if row["display_name"]:
                    self._league_display_names[league_code_lower] = row["display_name"]

                # Cache gracenote_category
                if row["gracenote_category"]:
                    self._gracenote_categories[league_code_lower] = row["gracenote_category"]

                # Cache sport for gracenote_category fallback
                if row["sport"]:
                    self._league_sports[league_code_lower] = row["sport"]

            # Also load league names from league_cache for fallback
            cursor = conn.execute(
                """
                SELECT league_slug, league_name
                FROM league_cache
                WHERE league_name IS NOT NULL
                """
            )
            for row in cursor.fetchall():
                slug = row["league_slug"].lower()
                if slug not in self._league_cache_names:
                    self._league_cache_names[slug] = row["league_name"]

            # Load sport display names from sports table
            self._sport_display_names = get_sport_display_names_from_db(conn)

        logger.info(
            "[LEAGUE_MAPPING] Loaded %d mappings (%d providers, %d aliases, %d sports)",
            len(self._mappings),
            len(self._provider_leagues),
            len(self._league_aliases),
            len(self._sport_display_names),
        )

    def reload(self) -> None:
        """Reload all mappings from database.

        Call this if leagues table is modified and you need fresh data.
        """
        self._mappings.clear()
        self._provider_leagues.clear()
        self._league_aliases.clear()
        self._league_ids.clear()
        self._league_display_names.clear()
        self._gracenote_categories.clear()
        self._league_sports.clear()
        self._league_cache_names.clear()
        self._sport_display_names.clear()
        self._load_all_mappings()

    def get_league_alias(self, league_code: str) -> str:
        """Get short display alias for {league} variable.

        Fallback chain:
            1. league_alias from leagues table (e.g., 'EPL', 'UCL')
            2. display_name from leagues table (e.g., 'NFL', 'La Liga')
            3. league_name from league_cache table
            4. league_code uppercase

        Thread-safe: uses in-memory cache, no DB access.

        Args:
            league_code: Raw league code (e.g., 'eng.1', 'nfl')

        Returns:
            Short alias (e.g., 'EPL', 'NFL', 'La Liga')
        """
        key = league_code.lower()

        # Try league_alias (already includes display_name fallback from load)
        if key in self._league_aliases:
            return self._league_aliases[key]

        # Fallback to league_cache name
        if key in self._league_cache_names:
            return self._league_cache_names[key]

        # Final fallback
        return league_code.upper()

    def get_league_id(self, league_code: str) -> str:
        """Get the URL-safe league ID for a league.

        Returns league_id if configured, otherwise returns league_code.
        Thread-safe: uses in-memory cache, no DB access.

        Args:
            league_code: Raw league code (e.g., 'eng.1', 'college-football')

        Returns:
            league_id (e.g., 'epl', 'ncaaf') if configured, otherwise league_code
        """
        key = league_code.lower()
        return self._league_ids.get(key, league_code)

    def get_league_display_name(self, league_code: str) -> str:
        """Get the full display name for a league.

        Fallback chain:
            1. display_name from leagues table
            2. league_name from league_cache table
            3. league_code uppercase

        Thread-safe: uses in-memory cache, no DB access.

        Args:
            league_code: Raw league code (e.g., 'eng.1', 'nfl')

        Returns:
            Display name (e.g., 'English Premier League', 'NFL')
        """
        key = league_code.lower()

        # Try display_name from leagues table
        if key in self._league_display_names:
            return self._league_display_names[key]

        # Fallback to league_name from league_cache
        if key in self._league_cache_names:
            return self._league_cache_names[key]

        # Final fallback to league_code uppercase
        return league_code.upper()

    def get_gracenote_category(self, league_code: str) -> str:
        """Get Gracenote-compatible category for {gracenote_category} variable.

        Fallback chain:
            1. gracenote_category from leagues table (curated value)
            2. Auto-generated: "{display_name} {Sport}" (e.g., 'NFL Football')

        Thread-safe: uses in-memory cache, no DB access.

        Args:
            league_code: Raw league code (e.g., 'nfl', 'eng.1')

        Returns:
            Gracenote category (e.g., 'NFL Football', 'College Basketball')
        """
        key = league_code.lower()

        # Try curated gracenote_category
        if key in self._gracenote_categories:
            return self._gracenote_categories[key]

        # Auto-generate from display_name + sport (with proper sport display name)
        display_name = self.get_league_display_name(league_code)
        sport_code = self._league_sports.get(key, "")

        if display_name and sport_code:
            sport_display = self.get_sport_display_name(sport_code)
            return f"{display_name} {sport_display}"

        # Fallback to just display_name
        return display_name

    def get_sport_display_name(self, sport_code: str) -> str:
        """Get display name for {sport} variable.

        Fallback chain:
            1. display_name from sports table (e.g., 'MMA', 'Football')
            2. Title case of sport_code (e.g., 'mma' -> 'Mma')

        Thread-safe: uses in-memory cache, no DB access.

        Args:
            sport_code: Lowercase sport code (e.g., 'mma', 'football')

        Returns:
            Display name (e.g., 'MMA', 'Football')
        """
        key = sport_code.lower()

        # Look up in cached sports table
        if key in self._sport_display_names:
            return self._sport_display_names[key]

        # Fallback to title case
        return sport_code.title()

    def get_mapping(self, league_code: str, provider: str) -> LeagueMapping | None:
        """Get mapping for a specific league and provider.

        Thread-safe: uses in-memory cache, no DB access.
        """
        key = (league_code.lower(), provider)
        return self._mappings.get(key)

    def supports_league(self, league_code: str, provider: str) -> bool:
        """Check if provider supports the given league.

        Thread-safe: uses in-memory cache, no DB access.
        """
        key = (league_code.lower(), provider)
        return key in self._mappings

    def get_leagues_for_provider(self, provider: str) -> list[LeagueMapping]:
        """Get all leagues supported by a provider.

        Thread-safe: uses in-memory cache, no DB access.
        """
        return self._provider_leagues.get(provider, [])

    def get_effective_provider(self, league_code: str) -> tuple[str, str] | None:
        """Get the effective (provider, league_id) for a league, considering fallbacks.

        This implements provider fallback resolution. For leagues with a fallback
        configured (e.g., cricket with TSDB primary, Cricbuzz fallback):
        - If primary provider has premium/full access: return primary
        - If primary provider is limited: return fallback if available

        Thread-safe: uses in-memory cache + ProviderRegistry check.

        Args:
            league_code: Canonical league code (e.g., 'ipl', 'nfl')

        Returns:
            Tuple of (provider_name, provider_league_id) or None if not found.

        Example:
            For IPL with TSDB free tier:
            - Primary: ('tsdb', '4460')
            - Fallback: ('cricbuzz', '9241/indian-premier-league-2026')

            If TSDB is not premium, returns fallback if configured.
        """
        key_lower = league_code.lower()

        # Find the mapping for this league (any provider)
        # First, find by iterating through all mappings
        mapping: LeagueMapping | None = None
        for (code, _provider), m in self._mappings.items():
            if code == key_lower:
                mapping = m
                break

        if mapping is None:
            return None

        # Check if primary provider has premium/full capabilities
        from teamarr.providers import ProviderRegistry

        if not ProviderRegistry.is_provider_premium(mapping.provider):
            # Primary provider is limited, check for fallback
            if mapping.fallback_provider and mapping.fallback_league_id:
                logger.debug(
                    "[LEAGUE_MAPPING] Using fallback for %s: %s/%s (primary %s not premium)",
                    league_code,
                    mapping.fallback_provider,
                    mapping.fallback_league_id,
                    mapping.provider,
                )
                return (mapping.fallback_provider, mapping.fallback_league_id)

        # Use primary provider
        return (mapping.provider, mapping.provider_league_id)

    def get_mapping_by_league(self, league_code: str) -> LeagueMapping | None:
        """Get mapping for a league code (any provider).

        Unlike get_mapping(), this doesn't require specifying the provider.
        Returns the first matching mapping found.

        Thread-safe: uses in-memory cache, no DB access.
        """
        key_lower = league_code.lower()
        for (code, _provider), mapping in self._mappings.items():
            if code == key_lower:
                return mapping
        return None


# Singleton instance - initialized by app startup
_league_mapping_service: LeagueMappingService | None = None


def init_league_mapping_service(
    db_getter: Callable[[], Generator[Connection, None, None]],
) -> LeagueMappingService:
    """Initialize the global league mapping service.

    Called during app startup after database is ready.
    """
    global _league_mapping_service
    _league_mapping_service = LeagueMappingService(db_getter)
    return _league_mapping_service


def get_league_mapping_service() -> LeagueMappingService:
    """Get the global league mapping service.

    Raises RuntimeError if not initialized.
    """
    if _league_mapping_service is None:
        raise RuntimeError(
            "LeagueMappingService not initialized. Call init_league_mapping_service() first."
        )
    return _league_mapping_service
