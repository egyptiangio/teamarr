"""Event-based EPG generation.

Fetches events from data providers and generates EPG programmes.
Each event gets its own channel.

Note: This queries DATA providers (ESPN, TheSportsDB) by league.
Event groups (M3U provider stream collections) are a separate concept
handled elsewhere.

Data flow:
- Scoreboard endpoint (8hr cache): Events with teams, start times, venue, broadcasts
- Scoreboard includes odds when betting lines are released (typically same-day)
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from teamarr.core import Event, Programme
from teamarr.database.templates import EventTemplateConfig
from teamarr.services import SportsDataService
from teamarr.templates.context_builder import ContextBuilder
from teamarr.templates.resolver import TemplateResolver
from teamarr.utilities.sports import get_sport_duration


@dataclass
class EventChannelInfo:
    """Generated channel info for an event."""

    channel_id: str
    name: str
    icon: str | None = None


@dataclass
class EventEPGOptions:
    """Options for event-based EPG generation."""

    pregame_minutes: int = 0
    default_duration_hours: float = 3.0
    template: EventTemplateConfig = field(default_factory=EventTemplateConfig)

    # Sport durations (from database settings)
    # Keys: basketball, football, hockey, baseball, soccer
    sport_durations: dict[str, float] = field(default_factory=dict)

    # XMLTV generator metadata (for orchestrator-based generation)
    generator_name: str | None = None
    generator_url: str | None = None


class EventEPGGenerator:
    """Generates EPG programmes for events from data providers."""

    def __init__(self, service: SportsDataService):
        self._service = service
        self._context_builder = ContextBuilder(service)
        self._resolver = TemplateResolver()

    def generate_for_leagues(
        self,
        leagues: list[str],
        target_date: date,
        channel_prefix: str,
        options: EventEPGOptions | None = None,
    ) -> tuple[list[Programme], list[EventChannelInfo]]:
        """Generate EPG for all events in specified leagues.

        Args:
            leagues: League codes to fetch events from
            target_date: Date to fetch events for
            channel_prefix: Prefix for generated channel IDs
            options: Generation options

        Returns:
            Tuple of (programmes, channels)
        """
        options = options or EventEPGOptions()

        all_events: list[Event] = []
        for league in leagues:
            # TSDB leagues use cache-only (no API calls during generation)
            is_tsdb = self._service.get_provider_name(league) == "tsdb"
            events = self._service.get_events(league, target_date, cache_only=is_tsdb)
            all_events.extend(events)

        programmes = []
        channels = []

        for event in all_events:
            channel_id = f"{channel_prefix}-{event.id}"

            # Build context using home team perspective for event-based EPG
            context = self._context_builder.build_for_event(
                event=event,
                team_id=event.home_team.id,
                league=event.league,
            )

            # Generate channel name from template
            # Unknown variables stay literal (e.g., {bad_var}) so user can identify issues
            channel_name = self._resolver.resolve(options.template.channel_name_format, context)

            # Use template-configured logo if set (no fallback to team logo)
            # Resolve template variables in logo URL (e.g., {league_id}, {home_team_pascal})
            channel_icon = None
            if options.template.event_channel_logo_url:
                channel_icon = self._resolver.resolve(
                    options.template.event_channel_logo_url, context
                )

            channel_info = EventChannelInfo(
                channel_id=channel_id,
                name=channel_name,
                icon=channel_icon,
            )
            channels.append(channel_info)

            programme = self._event_to_programme(event, context, channel_id, options)
            programmes.append(programme)

        return programmes, channels

    def generate_for_event(
        self,
        event_id: str,
        league: str,
        channel_id: str,
        options: EventEPGOptions | None = None,
    ) -> Programme | None:
        """Generate EPG for a specific event."""
        options = options or EventEPGOptions()

        event = self._service.get_event(event_id, league)
        if not event:
            return None

        # Build context using home team perspective
        context = self._context_builder.build_for_event(
            event=event,
            team_id=event.home_team.id,
            league=league,
        )

        return self._event_to_programme(event, context, channel_id, options)

    def _event_to_programme(
        self,
        event: Event,
        context,  # TemplateContext
        channel_id: str,
        options: EventEPGOptions,
        stream_name: str | None = None,
    ) -> Programme:
        """Convert an Event to a Programme with template resolution.

        Args:
            event: Event to convert
            context: Template context
            channel_id: XMLTV channel ID
            options: Generation options
            stream_name: Optional stream name (for UFC prelim/main detection)
        """
        # UFC/MMA events have special time handling based on stream name
        if event.sport == "mma" and stream_name and event.main_card_start:
            start, stop = self._get_ufc_programme_times(
                event, stream_name, options.sport_durations, options.default_duration_hours
            )
            # Apply pregame offset to start
            start = start - timedelta(minutes=options.pregame_minutes)
        else:
            # Standard handling for team sports
            start = event.start_time - timedelta(minutes=options.pregame_minutes)
            duration = get_sport_duration(
                event.sport, options.sport_durations, options.default_duration_hours
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
                context.game_context,  # GameContext for the event
            )
            if selected_template:
                description = self._resolver.resolve(selected_template, context)

        # Fallback to default description format
        if not description:
            description = self._resolver.resolve(options.template.description_format, context)

        # Icon: use template program_art_url if set (no fallback to team logo)
        # Unknown variables stay literal (e.g., {bad_var}) so user can identify issues
        icon = None
        if options.template.program_art_url:
            icon = self._resolver.resolve(options.template.program_art_url, context)

        # Resolve categories (may contain {sport} variable)
        # Apply title case for proper XMLTV formatting (e.g., "Football" not "football")
        resolved_categories = []
        for cat in options.template.xmltv_categories:
            if "{" in cat:
                resolved_categories.append(self._resolver.resolve(cat, context).title())
            else:
                resolved_categories.append(cat.title())

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

    # Keywords for detecting UFC prelim streams
    UFC_PRELIM_KEYWORDS = ["prelim", "prelims", "early", "pre-show", "early prelim"]

    # Keywords for detecting UFC main card streams
    UFC_MAIN_KEYWORDS = ["main", "main card", "main event", "ppv"]

    def _get_ufc_programme_times(
        self,
        event: Event,
        stream_name: str,
        sport_durations: dict[str, float],
        default_duration: float,
    ) -> tuple[datetime, datetime]:
        """Get start/end times for UFC events based on stream type.

        Detects prelims vs main card from stream name and adjusts times accordingly.
        Uses expanded keyword detection for better matching.

        Args:
            event: UFC Event with main_card_start set
            stream_name: Stream name to check for prelim/main indicators
            sport_durations: Duration settings from database
            default_duration: Fallback duration

        Returns:
            Tuple of (start_time, stop_time)
        """
        stream_lower = stream_name.lower()
        mma_duration = sport_durations.get("mma", default_duration)

        is_prelim = any(kw in stream_lower for kw in self.UFC_PRELIM_KEYWORDS)
        is_main = any(kw in stream_lower for kw in self.UFC_MAIN_KEYWORDS)

        if is_prelim and event.main_card_start:
            # Prelims only: event start → main card start
            return event.start_time, event.main_card_start
        elif is_main and event.main_card_start:
            # Main card only: main card start → estimated end
            # Main card is typically half the total duration
            main_duration = timedelta(hours=mma_duration / 2)
            return event.main_card_start, event.main_card_start + main_duration
        else:
            # Full event: prelims start → full duration
            return event.start_time, event.start_time + timedelta(hours=mma_duration)

    def generate_for_matched_streams(
        self,
        matched_streams: list[dict],
        options: EventEPGOptions | None = None,
    ) -> tuple[list[Programme], list[EventChannelInfo]]:
        """Generate EPG for already-matched streams.

        This is the main entry point for EventGroupProcessor.
        Unlike generate_for_leagues which fetches events, this takes
        pre-matched stream/event pairs from the matcher.

        Events come from scoreboard which already includes odds when available.

        Args:
            matched_streams: List of dicts with 'stream' and 'event' keys.
                stream: dict with 'id', 'name', 'tvg_id' etc
                event: Event dataclass
            options: Generation options

        Returns:
            Tuple of (programmes, channels)
        """
        options = options or EventEPGOptions()

        programmes = []
        channels = []

        for match in matched_streams:
            stream = match.get("stream", {})
            event = match.get("event")

            if not event:
                continue

            # Generate consistent tvg_id matching what ChannelLifecycleService uses
            # This ensures XMLTV channel IDs match managed_channels.tvg_id for EPG association
            from teamarr.consumers.lifecycle import generate_event_tvg_id

            tvg_id = generate_event_tvg_id(event.id, event.provider)
            stream_name = stream.get("name", "")

            # Build context using home team perspective
            context = self._context_builder.build_for_event(
                event=event,
                team_id=event.home_team.id,
                league=event.league,
            )

            # Generate channel name from template
            # Unknown variables stay literal (e.g., {bad_var}) so user can identify issues
            channel_name = self._resolver.resolve(options.template.channel_name_format, context)

            # Use template-configured logo if set (no fallback to team logo)
            # Resolve template variables in logo URL (e.g., {league_id}, {home_team_pascal})
            channel_icon = None
            if options.template.event_channel_logo_url:
                channel_icon = self._resolver.resolve(
                    options.template.event_channel_logo_url, context
                )

            channel_info = EventChannelInfo(
                channel_id=tvg_id,
                name=channel_name,
                icon=channel_icon,
            )
            channels.append(channel_info)

            # Generate programme - pass stream_name for UFC detection
            programme = self._event_to_programme(
                event, context, tvg_id, options, stream_name=stream_name
            )
            programmes.append(programme)

        return programmes, channels
