"""Event-based filler generation.

Generates pregame and postgame filler for event channels.
Simpler than team filler - single event context, no .next/.last suffixes.

Reuses:
- time_blocks.create_filler_chunks for time alignment
- FillerTemplate for template structure
- TemplateResolver for variable substitution
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from teamarr.core import Event, Programme
from teamarr.templates.context import GameContext, TeamChannelContext, TemplateContext
from teamarr.templates.resolver import TemplateResolver
from teamarr.utilities.sports import get_sport_duration
from teamarr.utilities.time_blocks import create_filler_chunks

from .types import ConditionalFillerTemplate, FillerTemplate


@dataclass
class EventFillerConfig:
    """Configuration for event-based filler.

    Simpler than FillerConfig - no idle/offseason since event channels
    are single-event focused.
    """

    # Pregame settings
    pregame_enabled: bool = True
    pregame_template: FillerTemplate = field(
        default_factory=lambda: FillerTemplate(
            title="Pregame Coverage",
            description="{away_team} @ {home_team} | {game_time} | {venue_full}",
        )
    )

    # Postgame settings
    postgame_enabled: bool = True
    postgame_template: FillerTemplate = field(
        default_factory=lambda: FillerTemplate(
            title="Postgame Recap",
            description="{away_team} @ {home_team} | Final",
        )
    )
    postgame_conditional: ConditionalFillerTemplate = field(
        default_factory=ConditionalFillerTemplate
    )

    # Category for filler content
    category: str = "Sports"


@dataclass
class EventFillerOptions:
    """Options for event filler generation."""

    # EPG window
    epg_start: datetime | None = None  # Defaults to now
    epg_end: datetime | None = None  # Defaults to event end + buffer

    # Timezone
    epg_timezone: str = "America/New_York"

    # Sport durations (hours) - for calculating event end
    sport_durations: dict[str, float] = field(default_factory=dict)
    default_duration: float = 3.0

    # Buffer after event for postgame (hours)
    postgame_buffer_hours: float = 24.0


class EventFillerGenerator:
    """Generates filler for event-based channels.

    Simpler than FillerGenerator - handles single events without
    schedule awareness or .next/.last context.

    Usage:
        generator = EventFillerGenerator()
        programmes = generator.generate(
            event=event,
            channel_id="teamarr-event-12345",
            config=EventFillerConfig(),
            options=EventFillerOptions(),
        )
    """

    def __init__(self):
        self._resolver = TemplateResolver()

    def generate(
        self,
        event: Event,
        channel_id: str,
        config: EventFillerConfig | None = None,
        options: EventFillerOptions | None = None,
    ) -> list[Programme]:
        """Generate pregame and postgame filler for an event.

        Args:
            event: The event to generate filler for
            channel_id: XMLTV channel ID
            config: Filler template configuration
            options: Generation options

        Returns:
            List of filler Programme entries
        """
        config = config or EventFillerConfig()
        options = options or EventFillerOptions()

        programmes: list[Programme] = []

        # Calculate event times
        event_start = event.start_time
        event_duration = get_sport_duration(
            event.sport, options.sport_durations, options.default_duration
        )
        event_end = event_start + timedelta(hours=event_duration)

        # Calculate EPG window
        epg_start = options.epg_start or datetime.now(event_start.tzinfo)
        epg_end = options.epg_end or (event_end + timedelta(hours=options.postgame_buffer_hours))

        # Build context once - event filler uses single context, no suffixes
        context = self._build_event_context(event)

        # Generate pregame filler
        if config.pregame_enabled and epg_start < event_start:
            pregame_programmes = self._generate_filler(
                start_dt=epg_start,
                end_dt=event_start,
                template=config.pregame_template,
                context=context,
                channel_id=channel_id,
                category=config.category,
                logo_url=event.home_team.logo_url,
            )
            programmes.extend(pregame_programmes)

        # Generate postgame filler
        if config.postgame_enabled and event_end < epg_end:
            # Select postgame template (conditional if enabled)
            postgame_template = self._select_postgame_template(event, config)

            postgame_programmes = self._generate_filler(
                start_dt=event_end,
                end_dt=epg_end,
                template=postgame_template,
                context=context,
                channel_id=channel_id,
                category=config.category,
                logo_url=event.home_team.logo_url,
            )
            programmes.extend(postgame_programmes)

        return programmes

    def _generate_filler(
        self,
        start_dt: datetime,
        end_dt: datetime,
        template: FillerTemplate,
        context: TemplateContext,
        channel_id: str,
        category: str,
        logo_url: str | None,
    ) -> list[Programme]:
        """Generate filler programmes for a time range.

        Uses 6-hour time block alignment from shared utilities.
        """
        # Split into time-block-aligned chunks
        chunks = create_filler_chunks(start_dt, end_dt)

        if not chunks:
            return []

        programmes: list[Programme] = []
        for chunk_start, chunk_end in chunks:
            # Resolve templates
            title = self._resolver.resolve(template.title, context)
            description = ""
            if template.description:
                description = self._resolver.resolve(template.description, context)
            subtitle = None
            if template.subtitle:
                subtitle = self._resolver.resolve(template.subtitle, context)

            programme = Programme(
                channel_id=channel_id,
                title=title,
                start=chunk_start,
                stop=chunk_end,
                description=description,
                subtitle=subtitle,
                category=category,
                icon=template.art_url or logo_url,
            )
            programmes.append(programme)

        return programmes

    def _build_event_context(self, event: Event) -> TemplateContext:
        """Build template context for event filler.

        Event filler uses positional variables (home_team, away_team)
        not perspective-based (team_name, opponent). No .next/.last support.
        """
        # Build minimal team config for context
        team_config = TeamChannelContext(
            team_id=event.home_team.id,
            league=event.league,
            sport=event.sport,
            team_name=event.home_team.name,
            team_abbrev=event.home_team.abbreviation,
        )

        # Build game context with home perspective (for positional vars)
        game_context = GameContext(
            event=event,
            is_home=True,
            team=event.home_team,
            opponent=event.away_team,
        )

        return TemplateContext(
            game_context=game_context,
            team_config=team_config,
            team_stats=None,  # Event filler doesn't need stats
            team=event.home_team,
            next_game=None,  # No .next for event filler
            last_game=None,  # No .last for event filler
        )

    def _select_postgame_template(self, event: Event, config: EventFillerConfig) -> FillerTemplate:
        """Select appropriate postgame template based on game status.

        Supports conditional descriptions (final vs in-progress).
        """
        if not config.postgame_conditional.enabled:
            return config.postgame_template

        # Check if game is final
        is_final = event.status.state == "post" or event.status.detail == "Final"

        if is_final and config.postgame_conditional.description_final:
            return FillerTemplate(
                title=config.postgame_template.title,
                subtitle=config.postgame_template.subtitle,
                description=config.postgame_conditional.description_final,
                art_url=config.postgame_template.art_url,
            )
        elif not is_final and config.postgame_conditional.description_not_final:
            return FillerTemplate(
                title=config.postgame_template.title,
                subtitle=config.postgame_template.subtitle,
                description=config.postgame_conditional.description_not_final,
                art_url=config.postgame_template.art_url,
            )

        return config.postgame_template


def template_to_event_filler_config(template) -> EventFillerConfig:
    """Convert database Template to EventFillerConfig.

    Args:
        template: Template from database (duck-typed for import avoidance)

    Returns:
        EventFillerConfig ready for EventFillerGenerator
    """
    # Build pregame template from fallback
    pregame_fb = getattr(template, "pregame_fallback", None) or {}
    pregame_template = FillerTemplate(
        title=pregame_fb.get("title", "Pregame Coverage"),
        subtitle=pregame_fb.get("subtitle"),
        description=pregame_fb.get(
            "description", "{away_team} @ {home_team} | {game_time} | {venue_full}"
        ),
        art_url=pregame_fb.get("art_url"),
    )

    # Build postgame template from fallback
    postgame_fb = getattr(template, "postgame_fallback", None) or {}
    postgame_template = FillerTemplate(
        title=postgame_fb.get("title", "Postgame Recap"),
        subtitle=postgame_fb.get("subtitle"),
        description=postgame_fb.get("description", "{away_team} @ {home_team} | Final"),
        art_url=postgame_fb.get("art_url"),
    )

    # Postgame conditional
    pg_cond = getattr(template, "postgame_conditional", None) or {}
    postgame_conditional = ConditionalFillerTemplate(
        enabled=pg_cond.get("enabled", False),
        description_final=pg_cond.get("description_final"),
        description_not_final=pg_cond.get("description_not_final"),
    )

    # Get category from xmltv_categories
    categories = getattr(template, "xmltv_categories", None) or ["Sports"]
    category = categories[0] if categories else "Sports"

    return EventFillerConfig(
        pregame_enabled=getattr(template, "pregame_enabled", True),
        pregame_template=pregame_template,
        postgame_enabled=getattr(template, "postgame_enabled", True),
        postgame_template=postgame_template,
        postgame_conditional=postgame_conditional,
        category=category,
    )
