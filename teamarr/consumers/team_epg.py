"""Team-based EPG generation.

Takes team configuration, fetches schedule, generates programmes with template support.

Data flow:
- Schedule endpoint (8hr cache): Events with teams, start times, venue, broadcasts
- Scoreboard provides odds for today's games (when betting lines are released)
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from teamarr.consumers.event_epg import prepend_postponed_label
from teamarr.core import Event, Programme, TemplateConfig
from teamarr.services import SportsDataService
from teamarr.templates.context_builder import ContextBuilder
from teamarr.templates.resolver import TemplateResolver
from teamarr.utilities.event_status import is_event_final
from teamarr.utilities.sports import get_effective_duration
from teamarr.utilities.tz import now_user, to_user_tz

logger = logging.getLogger(__name__)


@dataclass
class TeamEPGOptions:
    """Options for team-based EPG generation."""

    schedule_days_ahead: int = 30  # How far to fetch schedule (for .next vars)
    output_days_ahead: int = 14  # How many days to include in XMLTV
    lookback_hours: int = 6  # How far back to include events (for recently finished games)
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

    # XMLTV generator metadata (for orchestrator-based generation)
    generator_name: str | None = None
    generator_url: str | None = None

    # Postponed event label
    # When True, prepends "Postponed: " to EPG title, subtitle, and description
    # for events with status.state == "postponed"
    prepend_postponed_label: bool = True

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

        logger.debug(
            "[STARTED] Team EPG: team=%s league=%s days=%d",
            team_id,
            league,
            options.output_days_ahead,
        )

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

        # Ensure schedule_days_ahead > output_days_ahead for accurate "next game" info
        # on the last days of the EPG window. Add 7-day buffer minimum.
        min_schedule_days = options.output_days_ahead + 7
        effective_schedule_days = max(options.schedule_days_ahead, min_schedule_days)
        if effective_schedule_days != options.schedule_days_ahead:
            logger.debug(
                f"Extended schedule fetch from {options.schedule_days_ahead} to "
                f"{effective_schedule_days} days for accurate 'next game' info"
            )

        # Fetch team schedule from all leagues (parallel for multi-league teams)
        all_events: list[Event] = []
        seen_event_ids: set = set()

        def fetch_league(lg: str) -> list[Event]:
            return self._service.get_team_schedule(
                team_id=team_id,
                league=lg,
                days_ahead=effective_schedule_days,
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

        # Fetch team stats once for all events
        team_stats = self._service.get_team_stats(team_id, league)

        # Sort events by time to determine next/last relationships
        sorted_events = sorted(all_events, key=lambda e: e.start_time)

        # Calculate output window
        now = now_user()
        today = now.date()
        # EPG start: lookback_hours before now (for recently finished games)
        output_start_time = now - timedelta(hours=options.lookback_hours)
        # EPG end: output_days_ahead from today
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
            template_dict = (
                {
                    "game_duration_mode": options.template.game_duration_mode,
                    "game_duration_override": options.template.game_duration_override,
                }
                if options.template
                else None
            )
            duration = get_effective_duration(
                event.sport,
                options.sport_durations,
                options.default_duration_hours,
                template=template_dict,
            )
            event_end = event.start_time + timedelta(hours=duration)

            # Skip completed (final) events - matching V1 logic:
            # - Past day finals: ALWAYS excluded (regardless of include_final_events)
            # - Today's finals: honor include_final_events setting
            if is_event_final(event) and event_end < now:
                event_day = event.start_time.date()
                if event_day < today:
                    # Past day completed event - always skip
                    continue
                elif event_day == today and not options.include_final_events:
                    # Today's final, but include_final_events is False - skip
                    continue
                # else: Today's final with include_final_events=True - include it

            # Skip events before the lookback window
            # Use event start time (in user timezone) for comparison
            event_start_user = to_user_tz(event.start_time)
            if event_start_user < output_start_time:
                continue

            # Skip events beyond the output window
            # Compare dates in user timezone to match filler generation behavior
            event_date = event_start_user.date()
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

        logger.debug(
            "[COMPLETED] Team EPG: team=%s events=%d programmes=%d filler=%s",
            team_id,
            len(included_events),
            len(programmes),
            options.filler_enabled,
        )

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
        template_dict = (
            {
                "game_duration_mode": options.template.game_duration_mode,
                "game_duration_override": options.template.game_duration_override,
            }
            if options.template
            else None
        )
        duration = get_effective_duration(
            event.sport,
            options.sport_durations,
            options.default_duration_hours,
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

        # Prepend "Postponed: " label if event is postponed and setting is enabled
        title = prepend_postponed_label(title, event, options.prepend_postponed_label)
        subtitle = prepend_postponed_label(subtitle, event, options.prepend_postponed_label)
        description = prepend_postponed_label(description, event, options.prepend_postponed_label)

        # Icon: template program_art_url > channel logo > home team logo
        # Unknown variables stay literal (e.g., {bad_var}) so user can identify issues
        if options.template.program_art_url:
            icon = self._resolver.resolve(options.template.program_art_url, context)
        else:
            icon = logo_url or (event.home_team.logo_url if event.home_team else None)

        # Resolve categories (may contain {sport} variable)
        # Preserve user's original casing for custom categories
        resolved_categories = []
        for cat in options.template.xmltv_categories:
            if "{" in cat:
                resolved_categories.append(self._resolver.resolve(cat, context))
            else:
                resolved_categories.append(cat)

        return Programme(
            channel_id=channel_id,
            title=title,
            start=start,
            stop=stop,
            description=description,
            subtitle=subtitle,
            icon=icon,
            categories=resolved_categories,
            xmltv_flags=options.template.xmltv_flags,
            xmltv_video=options.template.xmltv_video,
        )

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
            lookback_hours=options.lookback_hours,
            epg_timezone=options.epg_timezone,
            midnight_crossover_mode=options.midnight_crossover_mode,
            sport_durations=options.sport_durations,
            default_duration=options.default_duration_hours,
            prepend_postponed_label=options.prepend_postponed_label,
        )

        # Load filler config from database if template_id is set
        filler_config = self._load_filler_config(options)

        # Skip filler generation if no template loaded
        if filler_config is None:
            return []

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

        # No template loaded - return None to signal generation should be skipped
        logger.warning(
            "No template loaded - skipping filler generation. "
            "Assign a template to the team to enable EPG generation."
        )
        return None

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
            logger.debug("[TEAM_EPG] Failed to load programme template %s: %s", template_id, e)

        return None
