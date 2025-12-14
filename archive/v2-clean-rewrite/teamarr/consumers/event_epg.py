"""Event-based EPG generation.

Fetches events from data providers and generates EPG programmes.
Each event gets its own channel.

Note: This queries DATA providers (ESPN, TheSportsDB) by league.
Event groups (M3U provider stream collections) are a separate concept
handled elsewhere.

Data flow:
- Scoreboard endpoint (8hr cache): Event discovery (IDs, teams, start times)
- Single event endpoint (30min cache): Fresh status/scores for today's events
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from teamarr.core import Event, Programme
from teamarr.services import SportsDataService
from teamarr.templates.context_builder import ContextBuilder
from teamarr.templates.resolver import TemplateResolver
from teamarr.utilities.sports import get_sport_duration
from teamarr.utilities.tz import now_user


@dataclass
class EventChannelInfo:
    """Generated channel info for an event."""

    channel_id: str
    name: str
    icon: str | None = None


@dataclass
class EventTemplateConfig:
    """Template configuration for event-based EPG."""

    # Event EPG uses home team perspective by default
    title_format: str = "{away_team} @ {home_team}"
    channel_name_format: str = "{away_team_abbrev} @ {home_team_abbrev}"
    description_format: str = "{matchup} | {venue_full} | {broadcast_simple}"
    subtitle_format: str = "{venue_city}"
    category: str = "Sports"


@dataclass
class EventEPGOptions:
    """Options for event-based EPG generation."""

    pregame_minutes: int = 30
    default_duration_hours: float = 3.0
    template: EventTemplateConfig = field(default_factory=EventTemplateConfig)

    # Sport durations (from database settings)
    # Keys: basketball, football, hockey, baseball, soccer
    sport_durations: dict[str, float] = field(default_factory=dict)


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
            events = self._service.get_events(league, target_date)
            all_events.extend(events)

        # Enrich events if target is today/yesterday (need fresh status/scores)
        all_events = self._enrich_if_recent(all_events, target_date)

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
            channel_name = self._resolver.resolve(
                options.template.channel_name_format, context
            )

            channel_info = EventChannelInfo(
                channel_id=channel_id,
                name=channel_name,
                icon=event.home_team.logo_url,
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
        description = self._resolver.resolve(options.template.description_format, context)
        subtitle = self._resolver.resolve(options.template.subtitle_format, context)

        return Programme(
            channel_id=channel_id,
            title=title,
            start=start,
            stop=stop,
            description=description,
            subtitle=subtitle,
            category=options.template.category,
            icon=event.home_team.logo_url,
        )

    def _get_ufc_programme_times(
        self,
        event: Event,
        stream_name: str,
        sport_durations: dict[str, float],
        default_duration: float,
    ) -> tuple[datetime, datetime]:
        """Get start/end times for UFC events based on stream type.

        Detects prelims vs main card from stream name and adjusts times accordingly.

        Args:
            event: UFC Event with main_card_start set
            stream_name: Stream name to check for "prelim" or "main"
            sport_durations: Duration settings from database
            default_duration: Fallback duration

        Returns:
            Tuple of (start_time, stop_time)
        """
        stream_lower = stream_name.lower()
        mma_duration = sport_durations.get("mma", default_duration)

        if "prelim" in stream_lower and event.main_card_start:
            # Prelims only: event start → main card start
            return event.start_time, event.main_card_start
        elif "main" in stream_lower and event.main_card_start:
            # Main card only: main card start → estimated end
            # Main card is typically half the total duration
            main_duration = timedelta(hours=mma_duration / 2)
            return event.main_card_start, event.main_card_start + main_duration
        else:
            # Full event: prelims start → full duration
            return event.start_time, event.start_time + timedelta(hours=mma_duration)

    def _enrich_if_recent(
        self, events: list[Event], target_date: date
    ) -> list[Event]:
        """Fetch fresh data for events if target is today/yesterday.

        Scoreboard endpoint is cached for 8hr (discovery only).
        For recent events that need current status/scores, we fetch
        from single event endpoint (30min cache).
        """
        today = now_user().date()
        yesterday = today - timedelta(days=1)

        # Only enrich if target is today or yesterday
        if target_date not in (today, yesterday):
            return events

        enriched = []
        for event in events:
            fresh = self._service.get_event(event.id, event.league)
            if fresh:
                enriched.append(fresh)
            else:
                enriched.append(event)  # Fallback to cached

        return enriched
