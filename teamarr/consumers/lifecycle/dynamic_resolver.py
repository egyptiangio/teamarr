"""Dynamic channel group and profile resolver.

Resolves {sport} and {league} wildcards to actual Dispatcharr group/profile IDs.
Auto-creates groups/profiles in Dispatcharr if they don't exist.
"""

import logging
from dataclasses import dataclass, field
from sqlite3 import Connection as SQLiteConnection
from typing import Any

from teamarr.core.sports import get_sport_display_names_from_db

logger = logging.getLogger(__name__)


@dataclass
class DynamicResolver:
    """Resolves dynamic channel groups and profiles.

    Caches Dispatcharr groups/profiles and sport/league display names
    to minimize API calls during batch processing.

    Uses the global Dispatcharr connection from the factory.
    """

    _db_factory: Any = None
    _db_conn: SQLiteConnection | None = None

    # Caches (populated on first use)
    _groups_by_name: dict[str, int] = field(default_factory=dict)
    _profiles_by_name: dict[str, int] = field(default_factory=dict)
    _sport_display_names: dict[str, str] = field(default_factory=dict)
    _league_display_names: dict[str, str] = field(default_factory=dict)
    _league_aliases: dict[str, str] = field(default_factory=dict)
    _initialized: bool = False

    def initialize(
        self,
        db_factory: Any,
        db_conn: SQLiteConnection,
    ) -> None:
        """Initialize the resolver with connections.

        Args:
            db_factory: Database factory (for getting Dispatcharr connection)
            db_conn: Database connection for sport/league lookups
        """
        self._db_factory = db_factory
        self._db_conn = db_conn
        self._initialized = False
        self._groups_by_name = {}
        self._profiles_by_name = {}
        self._sport_display_names = {}
        self._league_display_names = {}
        self._league_aliases = {}

    def _ensure_initialized(self) -> None:
        """Lazy initialization of caches."""
        if self._initialized:
            return

        # Load sport display names
        if self._db_conn:
            self._sport_display_names = get_sport_display_names_from_db(self._db_conn)

            cursor = self._db_conn.execute(
                "SELECT league_code, display_name, league_alias FROM leagues"
            )
            for row in cursor.fetchall():
                self._league_display_names[row["league_code"]] = row["display_name"]
                # league_alias with fallback to display_name (matches {league} template variable)
                alias = row["league_alias"] or row["display_name"]
                if alias:
                    self._league_aliases[row["league_code"]] = alias

        # Load existing Dispatcharr groups and profiles
        dispatcharr = self._get_dispatcharr()
        if dispatcharr:
            try:
                groups = dispatcharr.m3u.list_groups()
                for g in groups:
                    if g.name and g.id:
                        self._groups_by_name[g.name.lower()] = g.id
            except Exception as e:
                logger.warning("[RESOLVER] Failed to fetch channel groups: %s", e)

            try:
                profiles = dispatcharr.channels.list_profiles()
                for p in profiles:
                    if p.name and p.id:
                        self._profiles_by_name[p.name.lower()] = p.id
            except Exception as e:
                logger.warning("[RESOLVER] Failed to fetch channel profiles: %s", e)

        self._initialized = True
        logger.debug(
            "[RESOLVER] Initialized with %d groups, %d profiles, %d sports, %d leagues",
            len(self._groups_by_name),
            len(self._profiles_by_name),
            len(self._sport_display_names),
            len(self._league_display_names),
        )

    def _get_dispatcharr(self):
        """Get the Dispatcharr connection from factory."""
        if not self._db_factory:
            return None
        try:
            from teamarr.dispatcharr.factory import get_dispatcharr_connection

            return get_dispatcharr_connection(self._db_factory)
        except Exception as e:
            logger.warning("[RESOLVER] Failed to get Dispatcharr connection: %s", e)
            return None

    def get_sport_display_name(self, sport_code: str) -> str:
        """Get display name for a sport code.

        Args:
            sport_code: Sport code (e.g., 'mma', 'football'). Case-insensitive.

        Returns:
            Display name from sports table (e.g., 'MMA', 'Football').
        """
        self._ensure_initialized()
        # Normalize to lowercase - sports table uses lowercase keys
        key = sport_code.lower() if sport_code else ""
        return self._sport_display_names.get(key, sport_code.title())

    def get_league_display_name(self, league_code: str) -> str:
        """Get display name for a league code."""
        self._ensure_initialized()
        return self._league_display_names.get(league_code, league_code.upper())

    def get_league_alias(self, league_code: str) -> str:
        """Get short alias for a league code (matches {league} template variable).

        Fallback chain:
            1. league_alias from leagues table (e.g., 'EPL', 'UCL')
            2. display_name from leagues table (e.g., 'NFL', 'La Liga')
            3. league_code uppercase

        This ensures consistency with how {league} is resolved in templates.
        """
        self._ensure_initialized()
        return self._league_aliases.get(league_code, league_code.upper())

    def resolve_pattern(
        self, pattern: str, event_sport: str | None, event_league: str | None
    ) -> str:
        """Interpolate pattern with event data.

        Args:
            pattern: Pattern string like '{sport}', '{league}', or 'Sports | {sport} | {league}'
            event_sport: Event's sport code (e.g., 'soccer', 'mma')
            event_league: Event's league code (e.g., 'eng.1', 'nfl')

        Returns:
            Resolved string with wildcards replaced by display names
        """
        result = pattern

        if event_sport and "{sport}" in result:
            display_name = self.get_sport_display_name(event_sport)
            result = result.replace("{sport}", display_name)

        if event_league and "{league}" in result:
            alias = self.get_league_alias(event_league)
            result = result.replace("{league}", alias)

        return result

    def _get_or_create_group(self, name: str) -> int | None:
        """Get group ID by name, creating if needed.

        Args:
            name: Group display name

        Returns:
            Group ID or None if creation failed
        """
        self._ensure_initialized()
        name_lower = name.lower()

        # Check cache
        if name_lower in self._groups_by_name:
            return self._groups_by_name[name_lower]

        # Create new group
        dispatcharr = self._get_dispatcharr()
        if not dispatcharr:
            logger.warning("[RESOLVER] Cannot create group '%s': Dispatcharr not connected", name)
            return None

        try:
            result = dispatcharr.m3u.create_channel_group(name)
            if result.success and result.data:
                gid = result.data.get("id")
                if gid:
                    self._groups_by_name[name_lower] = gid
                    logger.info("[RESOLVER] Created channel group '%s' (id=%d)", name, gid)
                    return gid
            else:
                logger.warning("[RESOLVER] Failed to create group '%s': %s", name, result.error)
        except Exception as e:
            logger.warning("[RESOLVER] Error creating group '%s': %s", name, e)

        return None

    def _get_or_create_profile(self, name: str) -> int | None:
        """Get profile ID by name, creating if needed.

        Args:
            name: Profile display name

        Returns:
            Profile ID or None if creation failed
        """
        self._ensure_initialized()
        name_lower = name.lower()

        # Check cache
        if name_lower in self._profiles_by_name:
            return self._profiles_by_name[name_lower]

        # Create new profile
        dispatcharr = self._get_dispatcharr()
        if not dispatcharr:
            logger.warning("[RESOLVER] Cannot create profile '%s': Dispatcharr not connected", name)
            return None

        try:
            result = dispatcharr.channels.create_profile(name)
            if result.success and result.data:
                pid = result.data.get("id")
                if pid:
                    self._profiles_by_name[name_lower] = pid
                    logger.info("[RESOLVER] Created channel profile '%s' (id=%d)", name, pid)
                    return pid
            else:
                logger.warning("[RESOLVER] Failed to create profile '%s': %s", name, result.error)
        except Exception as e:
            logger.warning("[RESOLVER] Error creating profile '%s': %s", name, e)

        return None

    def resolve_channel_group(
        self,
        mode: str,
        static_group_id: int | None,
        event_sport: str | None,
        event_league: str | None,
    ) -> int | None:
        """Resolve channel group ID based on mode.

        Args:
            mode: 'static' or pattern string containing {sport}/{league}
            static_group_id: Group ID to use for 'static' mode
            event_sport: Event's sport code
            event_league: Event's league code

        Returns:
            Resolved group ID or None
        """
        logger.debug(
            "[RESOLVER] resolve_channel_group called: mode=%s, static_id=%s, sport=%s, league=%s",
            mode,
            static_group_id,
            event_sport,
            event_league,
        )

        if mode == "static":
            return static_group_id

        # Legacy mode support (pre-v39 databases)
        if mode == "sport" and event_sport:
            display_name = self.get_sport_display_name(event_sport)
            group_id = self._get_or_create_group(display_name)
            logger.info(
                "[RESOLVER] Sport mode: %s -> '%s' -> group_id=%s",
                event_sport,
                display_name,
                group_id,
            )
            return group_id

        if mode == "league" and event_league:
            alias = self.get_league_alias(event_league)
            group_id = self._get_or_create_group(alias)
            logger.info(
                "[RESOLVER] League mode: %s -> '%s' -> group_id=%s", event_league, alias, group_id
            )
            return group_id

        # Pattern mode: resolve {sport} and {league} wildcards
        if "{" in mode:
            resolved_name = self.resolve_pattern(mode, event_sport, event_league)

            # Check if any wildcards remain unresolved
            if "{sport}" in resolved_name or "{league}" in resolved_name:
                logger.warning(
                    "[RESOLVER] Pattern has unresolved wildcards: %s -> %s (sport=%s, league=%s)",
                    mode,
                    resolved_name,
                    event_sport,
                    event_league,
                )
                return static_group_id  # Fallback

            group_id = self._get_or_create_group(resolved_name)
            logger.info(
                "[RESOLVER] Pattern mode: %s -> '%s' -> group_id=%s", mode, resolved_name, group_id
            )
            return group_id

        # Unknown mode, fallback
        logger.warning(
            "[RESOLVER] Unknown mode '%s', falling back to static_group_id=%s",
            mode,
            static_group_id,
        )
        return static_group_id

    def resolve_channel_profiles(
        self,
        profile_ids: list[int | str] | None,
        event_sport: str | None,
        event_league: str | None,
    ) -> list[int]:
        """Resolve channel profile IDs, expanding wildcards and patterns.

        Args:
            profile_ids: List of profile IDs, wildcards ("{sport}", "{league}"), or custom patterns
            event_sport: Event's sport code
            event_league: Event's league code

        Returns:
            List of resolved integer profile IDs
        """
        if not profile_ids:
            return []

        resolved: list[int] = []

        for item in profile_ids:
            if isinstance(item, int):
                # Static profile ID
                resolved.append(item)
            elif isinstance(item, str):
                if item.isdigit():
                    # String that's actually a number
                    resolved.append(int(item))
                elif "{" in item:
                    # Pattern - resolve it
                    resolved_name = self.resolve_pattern(item, event_sport, event_league)

                    # Check if wildcards remain unresolved
                    if "{sport}" in resolved_name or "{league}" in resolved_name:
                        logger.warning(
                            "[RESOLVER] Profile pattern has unresolved wildcards: %s -> %s",
                            item,
                            resolved_name,
                        )
                        continue  # Skip this pattern

                    pid = self._get_or_create_profile(resolved_name)
                    if pid and pid not in resolved:
                        resolved.append(pid)
                        logger.debug(
                            "[RESOLVER] Profile pattern: %s -> '%s' -> id=%s",
                            item,
                            resolved_name,
                            pid,
                        )

        return resolved
