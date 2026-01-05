"""Team-based EPG generation.

Takes team configuration, fetches schedule, generates programmes with template support.

Two-phase data flow:
- Discovery (schedule, 8hr cache): Event IDs, teams, start times (batch)
- Enrichment (summary, 30min cache): Odds, rich data (per event, ESPN only)
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from teamarr.core import Event, Programme, TemplateConfig
from teamarr.services import SportsDataService
from teamarr.templates.context_builder import ContextBuilder
from teamarr.templates.resolver import TemplateResolver
from teamarr.utilities.sports import get_effective_duration, get_sport_duration
from teamarr.utilities.tz import now_user, to_user_tz

logger = logging.getLogger(__name__)


@dataclass
class TeamEPGOptions:
    """Options for team-based EPG generation."""

    schedule_days_ahead: int = 30  # How far to fetch schedule (for .next vars)
    output_days_ahead: int = 14  # How many days to include in XMLTV
    pregame_minutes: int = 0
    default_duration_hours: float = 3.0
    template: TemplateConfig | None = None  # REQUIRED - must be loaded from database

    # Filler generation options
    filler_enabled: bool = True  # Enable filler generation
    filler_config: Any = None  # Pre-loaded FillerConfig (avoids DB access in threads)
    epg_timezone: str = "America/New_York"
    midnight_crossover_mode: str = "postgame"  # 'postgame' or 'idle'

    # Sport durations (from database settings)
    # Keys: basketball, football, hockey, baseball, soccer
    sport_durations: dict[str, float] = field(default_factory=dict)

    # Database template ID for loading filler config
    # If set, filler config is loaded from database template
    template_id: int | None = None

    # Include completed (final) events in EPG output
    # If False (default), events with status="final" that have ended are skipped
    # If True, today's final events are included (same-day completed games)
    include_final_events: bool = False

    # Backwards compatibility
    @property
    def days_ahead(self) -> int:
        return self.output_days_ahead


class TeamEPGGenerator:
    """Generates EPG programmes for a team-based channel.

    Supports multi-league teams (e.g., soccer teams playing in domestic league,
    Champions League, cup competitions, etc.).
    """

    def __init__(self, service: SportsDataService):
        self._service = service
        self._context_builder = ContextBuilder(service)
        self._resolver = TemplateResolver()
        self._filler_generator = None  # Lazy loaded

    def generate_auto_discover(
        self,
        team_id: str,
        primary_league: str,
        channel_id: str,
        team_name: str,
        team_abbrev: str | None = None,
        logo_url: str | None = None,
        options: TeamEPGOptions | None = None,
        provider: str = "espn",
        sport: str | None = None,
    ) -> list[Programme]:
        """Generate EPG with automatic multi-league discovery.

        Uses the team/league cache to find all leagues the team plays in.

        NOTE: Multi-league discovery is ONLY enabled for soccer. In soccer,
        teams play in multiple competitions (domestic league, Champions League,
        cups) with the same team ID. In US sports (NBA, MLB, NFL, NHL), team IDs
        are NOT correlated across leagues - NBA team_id 8 (Pistons) is unrelated
        to NCAAM team_id 8 (Razorbacks).

        Args:
            team_id: Provider team ID
            primary_league: Primary league identifier
            channel_id: XMLTV channel ID
            team_name: Display name for the team
            team_abbrev: Team abbreviation
            logo_url: Team/channel logo URL
            options: Generation options
            provider: Data provider ('espn' or 'tsdb')
            sport: Sport type (baseball, basketball, etc.) - REQUIRED to avoid
                   cross-sport ID collisions in ESPN

        Returns:
            List of Programme entries from all discovered leagues
        """
        additional_leagues: list[str] = []

        # Multi-league discovery ONLY for soccer
        # Soccer teams play same competitions across leagues (EPL + Champions League + FA Cup)
        # US sports have unrelated team IDs across leagues (NBA vs NCAAM vs WNBA)
        if sport == "soccer":
            from teamarr.consumers.cache import get_cache

            cache = get_cache()
            additional_leagues = cache.get_team_leagues(team_id, provider, sport=sport)

            # Remove primary league from additional (will be added back in generate)
            additional_leagues = [lg for lg in additional_leagues if lg != primary_league]

        return self.generate(
            team_id=team_id,
            league=primary_league,
            channel_id=channel_id,
            team_name=team_name,
            team_abbrev=team_abbrev,
            logo_url=logo_url,
            options=options,
            additional_leagues=additional_leagues,
        )

    def generate(
        self,
        team_id: str,
        league: str,
        channel_id: str,
        team_name: str,
        team_abbrev: str | None = None,
        logo_url: str | None = None,
        options: TeamEPGOptions | None = None,
        additional_leagues: list[str] | None = None,
    ) -> list[Programme]:
        """Generate EPG programmes for a team.

        Args:
            team_id: Provider team ID
            league: Primary league identifier (nfl, nba, etc.)
            channel_id: XMLTV channel ID
            team_name: Display name for the team
            team_abbrev: Team abbreviation (e.g., "DET")
            logo_url: Team/channel logo URL
            options: Generation options including templates
            additional_leagues: Extra leagues to fetch schedule from (for multi-league teams)

        Returns:
            List of Programme entries for XMLTV
        """
        options = options or TeamEPGOptions()

        # Load template from database if template_id is set and not already pre-loaded
        # Template should be pre-loaded by TeamProcessor to avoid DB access in threads
        if options.template_id and options.template is None:
            loaded_template = self._load_programme_template(options.template_id)
            if loaded_template:
                options.template = loaded_template

        # CRITICAL: Template is REQUIRED - no hardcoded defaults
        # If no template is available, return empty list
        if options.template is None:
            logger.warning(
                f"No template configured for team {team_id} in league {league}. "
                "EPG generation requires a template. Skipping."
            )
            return []

        # Collect all leagues to fetch from
        leagues_to_fetch = [league]
        if additional_leagues:
            leagues_to_fetch.extend(lg for lg in additional_leagues if lg != league)

        # Fetch team schedule from all leagues (parallel for multi-league teams)
        all_events: list[Event] = []
        seen_event_ids: set = set()

        def fetch_league(lg: str) -> list[Event]:
            return self._service.get_team_schedule(
                team_id=team_id,
                league=lg,
                days_ahead=options.schedule_days_ahead,
            )

        # Single league: fetch directly (no thread overhead)
        # Multi-league: fetch in parallel (e.g., soccer teams in 6+ competitions)
        if len(leagues_to_fetch) == 1:
            events = fetch_league(leagues_to_fetch[0])
            all_events.extend(events)
            seen_event_ids.update(e.id for e in events)
        else:
            with ThreadPoolExecutor(max_workers=len(leagues_to_fetch)) as executor:
                futures = {executor.submit(fetch_league, lg): lg for lg in leagues_to_fetch}
                for future in as_completed(futures):
                    events = future.result()
                    # Dedupe by event ID across leagues
                    for event in events:
                        if event.id not in seen_event_ids:
                            seen_event_ids.add(event.id)
                            all_events.append(event)

        # Enrich all events for rich data (odds, etc.)
        # Only ESPN events benefit - TSDB enrichment adds no value
        all_events = self._enrich_events(all_events)

        # Fetch team stats once for all events
        team_stats = self._service.get_team_stats(team_id, league)

        # Sort events by time to determine next/last relationships
        sorted_events = sorted(all_events, key=lambda e: e.start_time)

        # Calculate output window
        now = now_user()
        today = now.date()
        # Use end-of-day in user timezone for cutoff to avoid excluding evening games
        # whose UTC time falls on the next day
        output_cutoff_date = today + timedelta(days=options.output_days_ahead)

        programmes = []
        included_events = []  # Track events that generated programmes (for filler)

        for i, event in enumerate(sorted_events):
            # Determine next/last events for suffix resolution
            # (uses full schedule for accurate .next vars)
            next_event = sorted_events[i + 1] if i + 1 < len(sorted_events) else None
            last_event = sorted_events[i - 1] if i > 0 else None

            # Build template context (always build for .next/.last vars)
            context = self._context_builder.build_for_event(
                event=event,
                team_id=team_id,
                league=league,
                team_stats=team_stats,
                next_event=next_event,
                last_event=last_event,
            )

            # Calculate when this event's programme would end
            # V1 Parity: Use template custom duration if set
            template_dict = {
                "game_duration_mode": options.template.game_duration_mode,
                "game_duration_override": options.template.game_duration_override,
            } if options.template else None
            duration = get_effective_duration(
                event.sport, options.sport_durations, options.default_duration_hours,
                template=template_dict,
            )
            event_end = event.start_time + timedelta(hours=duration)

            # Skip completed (final) events - matching V1 logic:
            # - Past day finals: ALWAYS excluded (regardless of include_final_events)
            # - Today's finals: honor include_final_events setting
            if event.status.state == "final" and event_end < now:
                event_day = event.start_time.date()
                if event_day < today:
                    # Past day completed event - always skip
                    continue
                elif event_day == today and not options.include_final_events:
                    # Today's final, but include_final_events is False - skip
                    continue
                # else: Today's final with include_final_events=True - include it

            # Skip events beyond the output window
            # Compare dates in user timezone to match filler generation behavior
            event_date = to_user_tz(event.start_time).date()
            if event_date > output_cutoff_date:
                continue

            # Generate programme with template resolution
            programme = self._event_to_programme(
                event=event,
                context=context,
                channel_id=channel_id,
                logo_url=logo_url,
                options=options,
            )
            if programme:
                programmes.append(programme)
                included_events.append(event)  # Track for filler generation

        # Generate filler content if enabled
        # Pass full schedule (sorted_events) so filler generator can see games
        # beyond output window for offseason detection. Uses team_schedule_days_ahead
        # (default 30) for lookahead - if any game exists, shows "Next game: ..."
        # instead of offseason content. TSDB capped at 14 days by provider.
        if options.filler_enabled:
            filler_programmes = self._generate_fillers(
                events=sorted_events,
                team_id=team_id,
                league=league,
                channel_id=channel_id,
                team_name=team_name,
                team_abbrev=team_abbrev,
                logo_url=logo_url,
                team_stats=team_stats,
                options=options,
            )
            programmes.extend(filler_programmes)

        # Sort all programmes by start time
        programmes.sort(key=lambda p: p.start)

        return programmes

    def _event_to_programme(
        self,
        event: Event,
        context,  # TemplateContext
        channel_id: str,
        logo_url: str | None,
        options: TeamEPGOptions,
    ) -> Programme | None:
        """Convert an Event to a Programme with template resolution."""
        start = event.start_time - timedelta(minutes=options.pregame_minutes)
        # V1 Parity: Use template custom duration if set
        template_dict = {
            "game_duration_mode": options.template.game_duration_mode,
            "game_duration_override": options.template.game_duration_override,
        } if options.template else None
        duration = get_effective_duration(
            event.sport, options.sport_durations, options.default_duration_hours,
            template=template_dict,
        )
        stop = event.start_time + timedelta(hours=duration)

        # Resolve templates
        title = self._resolver.resolve(options.template.title_format, context)
        subtitle = self._resolver.resolve(options.template.subtitle_format, context)

        # Use conditional description selector if conditions are defined
        description = None
        if options.template.conditional_descriptions:
            from teamarr.templates.conditions import get_condition_selector

            selector = get_condition_selector()
            selected_template = selector.select(
                options.template.conditional_descriptions,
                context,
                context.game_context,  # GameContext for current event
            )
            if selected_template:
                description = self._resolver.resolve(selected_template, context)

        # Fallback to default description format
        if not description:
            description = self._resolver.resolve(options.template.description_format, context)

        # Icon priority: template program_art_url > channel logo > home team logo
        # Resolve template variables in program_art_url if present
        icon = None
        if options.template.program_art_url:
            resolved_art = self._resolver.resolve(options.template.program_art_url, context)
            # Only use if resolution succeeded (no unresolved placeholders)
            if "{" not in resolved_art:
                icon = resolved_art
        if not icon:
            icon = logo_url or (event.home_team.logo_url if event.home_team else None)

        return Programme(
            channel_id=channel_id,
            title=title,
            start=start,
            stop=stop,
            description=description,
            subtitle=subtitle,
            category=options.template.category,
            icon=icon,
        )

    def _enrich_events(self, events: list[Event]) -> list[Event]:
        """Enrich events with data from summary endpoint.

        Two-phase architecture:
        - Discovery (schedule endpoint, 8hr cache): IDs, teams, start times
        - Enrichment (summary endpoint, 30min cache): Odds, rich data

        Only enriches ESPN events - TSDB's lookupevent returns identical
        data to eventsday, so enrichment wastes API quota.
        """
        # Split ESPN events (need enrichment) from others (pass through)
        espn_events = [e for e in events if e.provider == "espn"]
        other_events = [e for e in events if e.provider != "espn"]

        # No ESPN events? Return as-is
        if not espn_events:
            return events

        def enrich_single(event: Event) -> Event:
            fresh = self._service.get_event(event.id, event.league)
            return fresh if fresh else event

        # Single event: fetch directly (no thread overhead)
        # Multiple events: fetch in parallel (e.g., 30+ events over 30 days)
        enriched_espn = []
        if len(espn_events) == 1:
            enriched_espn.append(enrich_single(espn_events[0]))
        else:
            with ThreadPoolExecutor(max_workers=min(len(espn_events), 20)) as executor:
                futures = {executor.submit(enrich_single, e): e for e in espn_events}
                for future in as_completed(futures):
                    enriched_espn.append(future.result())

        return enriched_espn + other_events

    def _generate_fillers(
        self,
        events: list[Event],
        team_id: str,
        league: str,
        channel_id: str,
        team_name: str,
        team_abbrev: str | None,
        logo_url: str | None,
        team_stats,
        options: TeamEPGOptions,
    ) -> list[Programme]:
        """Generate filler programmes for gaps between events.

        Uses FillerGenerator to create pregame, postgame, and idle content.
        """
        # Lazy import to avoid circular dependency
        from teamarr.consumers.filler import FillerGenerator, FillerOptions

        # Initialize filler generator if not already done
        if self._filler_generator is None:
            self._filler_generator = FillerGenerator(self._service)

        # Build filler options from EPG options
        filler_options = FillerOptions(
            output_days_ahead=options.output_days_ahead,
            epg_timezone=options.epg_timezone,
            midnight_crossover_mode=options.midnight_crossover_mode,
            sport_durations=options.sport_durations,
            default_duration=options.default_duration_hours,
        )

        # Load filler config from database if template_id is set
        filler_config = self._load_filler_config(options)

        return self._filler_generator.generate(
            events=events,
            team_id=team_id,
            league=league,
            channel_id=channel_id,
            team_name=team_name,
            team_abbrev=team_abbrev,
            logo_url=logo_url,
            team_stats=team_stats,
            options=filler_options,
            config=filler_config,
        )

    def _load_filler_config(self, options: TeamEPGOptions):
        """Load filler config from database template or use defaults.

        If options.filler_config is already set (pre-loaded by TeamProcessor),
        returns it directly to avoid DB access in threads.
        """
        from teamarr.consumers.filler import FillerConfig

        # Use pre-loaded config if available (critical for thread-safety)
        if options.filler_config is not None:
            return options.filler_config

        # Fallback: load from database (only for non-parallel usage)
        if options.template_id:
            try:
                from teamarr.database import get_db
                from teamarr.database.templates import get_template, template_to_filler_config

                with get_db() as conn:
                    template = get_template(conn, options.template_id)
                    if template:
                        return template_to_filler_config(template)
            except Exception as e:
                logger.debug(
                    f"Failed to load filler config for template {options.template_id}: {e}"
                )

        # Default filler config
        return FillerConfig(
            category=options.template.category if options.template else "Sports",
        )

    def _load_programme_template(self, template_id: int) -> TemplateConfig | None:
        """Load main programme template from database.

        Args:
            template_id: Template ID to load

        Returns:
            TemplateConfig or None if not found/error
        """
        try:
            from teamarr.database import get_db
            from teamarr.database.templates import get_template, template_to_programme_config

            with get_db() as conn:
                template = get_template(conn, template_id)
                if template:
                    return template_to_programme_config(template)
        except Exception as e:
            logger.debug(f"Failed to load programme template {template_id}: {e}")

        return None
