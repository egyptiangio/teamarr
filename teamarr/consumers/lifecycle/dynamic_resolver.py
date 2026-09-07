"""Dynamic channel group and profile resolver.

Resolves {sport}, {league}, {conference} and {division} wildcards to actual
Dispatcharr group/profile IDs.
Auto-creates groups/profiles in Dispatcharr if they don't exist.
"""

import logging
from dataclasses import dataclass, field
from sqlite3 import Connection as SQLiteConnection
from typing import Any

from teamarr.core.sports import get_sport_display_names_from_db
from teamarr.dispatcharr.factory import get_dispatcharr_connection

logger = logging.getLogger(__name__)


def _group_key(name: str) -> str:
    """Cache key for a Dispatcharr channel-group or profile name (#745).

    Trims and lowercases — and deliberately does NOT collapse internal
    whitespace runs.

    Both managers post ``name.strip()`` when creating, so a name that differs
    from Dispatcharr's only at the ends can never resolve: the lookup misses,
    the create is refused as a duplicate of the group that was already there,
    the channel is left with a null group id, and the cycle repeats on every
    run because nothing about that state changes.

    Collapsing internal runs looks like the same idea and is not safe. Measured
    against a live install's 3,097 channel groups, trimming produced 0 colliding
    keys while collapsing produced 18 — real, distinct groups separated only by
    a non-breaking space where the other has a regular one ("UK| ULTIMATE POOL
    PPV" vs "UK| ULTIMATE\xa0POOL\xa0PPV"). Merging those would file channels
    under the wrong group, which is worse than the bug this fixes.
    """
    return name.strip().lower()


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
    # (provider, league, team_id) -> provider group dict or None (#91)
    _group_by_team: dict = field(default_factory=dict)
    _league_display_names: dict[str, str] = field(default_factory=dict)
    _league_aliases: dict[str, str] = field(default_factory=dict)
    # Resolutions already announced at INFO. Group resolution is answered from
    # _groups_by_name, so the same mapping is re-derived once per channel — 722
    # lines in one observed run, 330 of them character-identical. The DISTINCT
    # mappings are what a reader needs ("which group did MLB land in?"); the
    # repeats are noise that costs real signal. First of each is announced,
    # the rest go to DEBUG.
    _announced_resolutions: set[tuple] = field(default_factory=set)
    # Valid Dispatcharr channel-group ids seen at init (only trusted when the
    # group fetch succeeded — see _groups_loaded) so a stale/deleted configured
    # group id can be detected before it fails every channel creation.
    _known_group_ids: set[int] = field(default_factory=set)
    _groups_loaded: bool = False
    _initialized: bool = False
    # Warn once when {division} is used against a conference tree cached
    # before #717 (parent_name NULL) — otherwise every event silently falls
    # back to the static group with no hint that a cache refresh fixes it.
    _warned_missing_division: bool = False

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
        self._known_group_ids = set()
        self._groups_loaded = False
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

            # Fallback: discovered leagues from league_cache (not in static leagues table)
            cursor = self._db_conn.execute(
                "SELECT league_slug, league_name FROM league_cache"
            )
            for row in cursor.fetchall():
                slug = row["league_slug"]
                if slug not in self._league_display_names and row["league_name"]:
                    self._league_display_names[slug] = row["league_name"]
                if slug not in self._league_aliases and row["league_name"]:
                    self._league_aliases[slug] = row["league_name"]

        # Load existing Dispatcharr groups and profiles
        dispatcharr = self._get_dispatcharr()
        if dispatcharr:
            try:
                groups = dispatcharr.m3u.list_groups()
                for g in groups:
                    if g.name and g.id:
                        self._groups_by_name[_group_key(g.name)] = g.id
                        self._known_group_ids.add(g.id)
                self._groups_loaded = True
            except Exception as e:
                logger.warning("[RESOLVER] Failed to fetch channel groups: %s", e)

            try:
                profiles = dispatcharr.channels.list_profiles()
                for p in profiles:
                    if p.name and p.id:
                        self._profiles_by_name[_group_key(p.name)] = p.id
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

    def _get_event_group(self, event: Any) -> dict | None:
        """The home team's cached provider group (conference), or None.

        NCAA-only today (the cache holds only leagues with ESPN conference
        trees). The HOME team buckets the event — deterministic, and correct
        for conference games; non-conference games land in the host's
        conference, which is how venues bucket them too. Cross-division games
        follow the same rule, and land where you'd want them: in an
        FBS-vs-FCS game the FCS side is the visitor.
        """
        home_team = getattr(event, "home_team", None)
        league = getattr(event, "league", None)
        team_id = getattr(home_team, "id", None)
        if not league or not team_id:
            return None
        provider = getattr(event, "provider", "espn")
        key = (provider, league, team_id)
        if key not in self._group_by_team:
            group = None
            if self._db_conn is not None:
                try:
                    from teamarr.database.provider_groups import get_team_group

                    group = get_team_group(self._db_conn, provider, league, team_id)
                except Exception as e:
                    logger.debug("[RESOLVER] Conference lookup failed for %s: %s", key, e)
            self._group_by_team[key] = group
        return self._group_by_team[key]

    def get_event_conference(self, event: Any) -> str | None:
        """Conference name for an event's home team (#91)."""
        group = self._get_event_group(event)
        return group["name"] if group else None

    def get_event_division(self, event: Any) -> str | None:
        """Division for an event's home team — the conference's parent (#717).

        'FBS'/'FCS' for college football, 'Division I' for college basketball.
        None until the conference tree has been re-cached with parent data
        (pre-#717 rows), so the pattern falls back to the static group rather
        than inventing a bucket.
        """
        group = self._get_event_group(event)
        if not group:
            return None
        division = group.get("division")
        if not division and not self._warned_missing_division:
            self._warned_missing_division = True
            logger.warning(
                "[RESOLVER] {division} pattern used but the cached conference tree "
                "has no division data (cached before this feature existed) — "
                "channels fall back to the static group. Refresh the team cache "
                "to populate it."
            )
        return division or None

    def resolve_pattern(
        self,
        pattern: str,
        event_sport: str | None,
        event_league: str | None,
        conference: str | None = None,
        division: str | None = None,
    ) -> str:
        """Interpolate pattern with event data.

        Args:
            pattern: Pattern string like '{sport}', '{league}', or 'Sports | {sport} | {league}'
            event_sport: Event's sport code (e.g., 'soccer', 'mma')
            event_league: Event's league code (e.g., 'eng.1', 'nfl')
            conference: Conference name for the {conference} wildcard (#91)
            division: Division name for the {division} wildcard (#717)

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

        if conference and "{conference}" in result:
            result = result.replace("{conference}", conference)

        if division and "{division}" in result:
            result = result.replace("{division}", division)

        return result

    def _validate_group_id(self, group_id: int | None) -> int | None:
        """Drop a configured group id that no longer exists in Dispatcharr.

        A channel assigned to a deleted group id is rejected by Dispatcharr
        ("Invalid pk … object does not exist"), which would fail EVERY channel
        routed to that group (e.g. a static or per-league group the user deleted
        — observed as 0 channels created). If the current groups loaded
        successfully and the id isn't among them, fall back to ungrouped (None)
        so channels are still created. Only trusted when the fetch succeeded;
        otherwise assume valid to avoid dropping groups on a transient API error.
        """
        if group_id is None:
            return None
        self._ensure_initialized()
        if self._groups_loaded and group_id not in self._known_group_ids:
            logger.warning(
                "[RESOLVER] Configured channel group id=%s no longer exists in "
                "Dispatcharr — creating channels ungrouped. Re-select a channel "
                "group in Settings to restore grouping.",
                group_id,
            )
            return None
        return group_id

    def _log_resolution(self, key: tuple, message: str, *args) -> None:
        """Announce a group resolution once per distinct outcome (see above)."""
        if key in self._announced_resolutions:
            logger.debug(message, *args)
            return
        self._announced_resolutions.add(key)
        logger.info(message, *args)

    def _get_or_create_group(self, name: str) -> int | None:
        """Get group ID by name, creating if needed.

        Args:
            name: Group display name

        Returns:
            Group ID or None if creation failed
        """
        self._ensure_initialized()

        # Match Dispatcharr's own trimming (#745). create_channel_group already
        # posts name.strip(), so an untrimmed name could never create the group
        # it was looking for — it looked up a key nothing holds, then asked
        # Dispatcharr to create a name that already existed and got a duplicate
        # 400 back. Every run, forever, because nothing about that state changes.
        name = name.strip()
        if not name:
            logger.warning(
                "[RESOLVER] Refusing to resolve an empty channel-group name "
                "(pattern resolved to whitespace only) — channel left ungrouped"
            )
            return None

        key = _group_key(name)

        # Check cache
        if key in self._groups_by_name:
            return self._groups_by_name[key]

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
                    self._groups_by_name[key] = gid
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

        # Same trimming contract as _get_or_create_group (#745): create_profile
        # posts name.strip(), so an untrimmed lookup can only ever miss and then
        # ask Dispatcharr for a duplicate.
        name = name.strip()
        if not name:
            logger.warning("[RESOLVER] Refusing to resolve an empty channel-profile name")
            return None

        key = _group_key(name)

        # Check cache
        if key in self._profiles_by_name:
            return self._profiles_by_name[key]

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
                    self._profiles_by_name[key] = pid
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
        event: Any = None,
    ) -> int | None:
        """Resolve channel group ID based on mode.

        Args:
            mode: 'static' or pattern string containing
                {sport}/{league}/{conference}/{division}
            static_group_id: Group ID to use for 'static' mode
            event_sport: Event's sport code
            event_league: Event's league code
            event: The event itself — needed only for the {conference} (#91)
                and {division} (#717) wildcards, which resolve from the
                home team

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
            return self._validate_group_id(static_group_id)

        # Legacy mode support (pre-v39 databases)
        if mode == "sport" and event_sport:
            display_name = self.get_sport_display_name(event_sport)
            group_id = self._get_or_create_group(display_name)
            self._log_resolution(
                ("sport", event_sport, display_name, group_id),
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

        # Pattern mode: resolve {sport}/{league}/{conference}/{division} wildcards
        if "{" in mode:
            conference = (
                self.get_event_conference(event) if "{conference}" in mode else None
            )
            division = self.get_event_division(event) if "{division}" in mode else None
            resolved_name = self.resolve_pattern(
                mode, event_sport, event_league, conference, division
            )

            # Check if any wildcards remain unresolved (a non-NCAA event under
            # a {conference}/{division} pattern falls back to the static group)
            if (
                "{sport}" in resolved_name
                or "{league}" in resolved_name
                or "{conference}" in resolved_name
                or "{division}" in resolved_name
            ):
                logger.warning(
                    "[RESOLVER] Pattern has unresolved wildcards: %s -> %s (sport=%s, league=%s)",
                    mode,
                    resolved_name,
                    event_sport,
                    event_league,
                )
                return self._validate_group_id(static_group_id)  # Fallback

            group_id = self._get_or_create_group(resolved_name)
            self._log_resolution(
                ("pattern", mode, resolved_name, group_id),
                "[RESOLVER] Pattern mode: %s -> '%s' -> group_id=%s",
                mode,
                resolved_name,
                group_id,
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
