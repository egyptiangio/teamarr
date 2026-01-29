"""Event-based filler generation.

Generates pregame and postgame filler for event channels.
Simpler than team filler - single event context, no .next/.last suffixes.

Reuses:
- time_blocks.create_filler_chunks for time alignment
- FillerTemplate for template structure
- TemplateResolver for variable substitution
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from teamarr.consumers.event_epg import POSTPONED_LABEL, is_event_postponed
from teamarr.core import Event, Programme, TeamStats
from teamarr.services.sports_data import SportsDataService
from teamarr.templates.context import GameContext, Odds, TeamChannelContext, TemplateContext
from teamarr.templates.resolver import TemplateResolver
from teamarr.utilities.sports import get_sport_duration
from teamarr.utilities.time_blocks import create_filler_chunks

from .types import ConditionalFillerTemplate, FillerTemplate

logger = logging.getLogger(__name__)


@dataclass
class EventFillerConfig:
    """Configuration for event-based filler.

    Simpler than FillerConfig - no idle/offseason since event channels
    are single-event focused. No hardcoded defaults - schema.sql provides them.
    """

    # Pregame settings
    pregame_enabled: bool = True
    pregame_template: FillerTemplate = field(
        default_factory=lambda: FillerTemplate(title="", description="")
    )

    # Postgame settings
    postgame_enabled: bool = True
    postgame_template: FillerTemplate = field(
        default_factory=lambda: FillerTemplate(title="", description="")
    )
    postgame_conditional: ConditionalFillerTemplate = field(
        default_factory=ConditionalFillerTemplate
    )

    # XMLTV categories (list for multiple categories)
    xmltv_categories: list[str] = field(default_factory=list)
    # Whether categories apply to filler ('all') or just events ('events')
    categories_apply_to: str = "events"


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

    # Override event end time (for UFC segments with known end times)
    event_end_override: datetime | None = None

    # Postponed label - prepend "Postponed: " to filler for postponed events
    prepend_postponed_label: bool = True


@dataclass
class EventFillerResult:
    """Result of generating event filler with counts."""

    programmes: list[Programme] = field(default_factory=list)
    pregame_count: int = 0
    postgame_count: int = 0


class EventFillerGenerator:
    """Generates filler for event-based channels.

    Simpler than FillerGenerator - handles single events without
    schedule awareness or .next/.last context.

    Usage:
        generator = EventFillerGenerator(service)
        programmes = generator.generate(
            event=event,
            channel_id="teamarr-event-12345",
            config=EventFillerConfig(),
            options=EventFillerOptions(),
        )
    """

    def __init__(self, service: SportsDataService | None = None):
        self._service = service
        self._resolver = TemplateResolver()
        # Cache for team stats to avoid redundant API calls within a generation run
        self._stats_cache: dict[tuple[str, str], TeamStats | None] = {}

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
        # Use override if provided (e.g., UFC segment end times)
        if options.event_end_override:
            event_end = options.event_end_override
        else:
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
                config=config,
                logo_url=event.home_team.logo_url,
                filler_type="pregame",
                event=event,
                prepend_postponed_label=options.prepend_postponed_label,
            )
            programmes.extend(pregame_programmes)
            logger.debug(
                "[FILLER] event=%s: %d pregame programmes",
                event.id,
                len(pregame_programmes),
            )

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
                config=config,
                logo_url=event.home_team.logo_url,
                filler_type="postgame",
                event=event,
                prepend_postponed_label=options.prepend_postponed_label,
            )
            programmes.extend(postgame_programmes)
            logger.debug(
                "[FILLER] event=%s: %d postgame programmes",
                event.id,
                len(postgame_programmes),
            )

        return programmes

    def generate_with_counts(
        self,
        event: Event,
        channel_id: str,
        config: EventFillerConfig | None = None,
        options: EventFillerOptions | None = None,
    ) -> EventFillerResult:
        """Generate filler with separate pregame/postgame counts.

        Same as generate() but returns structured result with counts.
        """
        config = config or EventFillerConfig()
        options = options or EventFillerOptions()

        result = EventFillerResult()

        # Calculate event times
        event_start = event.start_time
        # Use override if provided (e.g., UFC segment end times)
        if options.event_end_override:
            event_end = options.event_end_override
        else:
            event_duration = get_sport_duration(
                event.sport, options.sport_durations, options.default_duration
            )
            event_end = event_start + timedelta(hours=event_duration)

        # Calculate EPG window
        epg_start = options.epg_start or datetime.now(event_start.tzinfo)
        epg_end = options.epg_end or (event_end + timedelta(hours=options.postgame_buffer_hours))

        # Build context once
        context = self._build_event_context(event)

        # Generate pregame filler
        if config.pregame_enabled and epg_start < event_start:
            pregame_programmes = self._generate_filler(
                start_dt=epg_start,
                end_dt=event_start,
                template=config.pregame_template,
                context=context,
                channel_id=channel_id,
                config=config,
                logo_url=event.home_team.logo_url,
                filler_type="pregame",
                event=event,
                prepend_postponed_label=options.prepend_postponed_label,
            )
            result.programmes.extend(pregame_programmes)
            result.pregame_count = len(pregame_programmes)

        # Generate postgame filler
        if config.postgame_enabled and event_end < epg_end:
            postgame_template = self._select_postgame_template(event, config)

            postgame_programmes = self._generate_filler(
                start_dt=event_end,
                end_dt=epg_end,
                template=postgame_template,
                context=context,
                channel_id=channel_id,
                config=config,
                logo_url=event.home_team.logo_url,
                filler_type="postgame",
                event=event,
                prepend_postponed_label=options.prepend_postponed_label,
            )
            result.programmes.extend(postgame_programmes)
            result.postgame_count = len(postgame_programmes)

        return result

    def _generate_filler(
        self,
        start_dt: datetime,
        end_dt: datetime,
        template: FillerTemplate,
        context: TemplateContext,
        channel_id: str,
        config: EventFillerConfig,
        logo_url: str | None,
        filler_type: str,
        event: Event | None = None,
        prepend_postponed_label: bool = True,
    ) -> list[Programme]:
        """Generate filler programmes for a time range.

        Uses 6-hour time block alignment from shared utilities.
        """
        # Split into time-block-aligned chunks
        chunks = create_filler_chunks(start_dt, end_dt)

        if not chunks:
            return []

        # Check if we should prepend "Postponed: " label
        should_prepend = prepend_postponed_label and event and is_event_postponed(event)

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

            # Prepend "Postponed: " label if applicable
            if should_prepend:
                title = f"{POSTPONED_LABEL}{title}"
                if subtitle:
                    subtitle = f"{POSTPONED_LABEL}{subtitle}"
                if description:
                    description = f"{POSTPONED_LABEL}{description}"

            # Resolve art URL if present
            # Unknown variables stay literal (e.g., {bad_var}) so user can identify issues
            icon = self._resolver.resolve(template.art_url, context) if template.art_url else None

            # Only include categories if categories_apply_to == "all"
            # Filler never gets xmltv_flags (new/live/date are for live events only)
            # Apply title case for proper XMLTV formatting (e.g., "Football" not "football")
            filler_categories = []
            if config.categories_apply_to == "all":
                # Resolve any {sport} variables in categories
                for cat in config.xmltv_categories:
                    if "{" in cat:
                        filler_categories.append(self._resolver.resolve(cat, context).title())
                    else:
                        filler_categories.append(cat.title())

            programme = Programme(
                channel_id=channel_id,
                title=title,
                start=chunk_start,
                stop=chunk_end,
                description=description,
                subtitle=subtitle,
                icon=icon,
                filler_type=filler_type,
                categories=filler_categories,
                # No xmltv_flags for filler - new/live/date are for live events only
            )
            programmes.append(programme)

        return programmes

    def _build_event_context(self, event: Event) -> TemplateContext:
        """Build template context for event filler.

        Event filler uses positional variables (home_team, away_team)
        not perspective-based (team_name, opponent). No .next/.last support.

        Fetches team stats if service available. Odds come from scoreboard
        (event.odds_data) - no enrichment needed.
        """
        # Build minimal team config for context (home team perspective)
        team_config = TeamChannelContext(
            team_id=event.home_team.id,
            league=event.league,
            sport=event.sport,
            team_name=event.home_team.name,
            team_abbrev=event.home_team.abbreviation,
        )

        # Fetch team stats if service is available
        home_stats = self._get_team_stats(event.home_team.id, event.league)
        away_stats = self._get_team_stats(event.away_team.id, event.league)

        # Build odds from event data (home team perspective)
        odds = self._build_odds(event.odds_data, is_home=True) if event.odds_data else None

        # Build game context with home perspective (for positional vars)
        # Include opponent_stats for away team record variables
        game_context = GameContext(
            event=event,
            is_home=True,
            team=event.home_team,
            opponent=event.away_team,
            opponent_stats=away_stats,
            odds=odds,
        )

        return TemplateContext(
            game_context=game_context,
            team_config=team_config,
            team_stats=home_stats,  # Home team stats for {home_team_record}
            team=event.home_team,
            next_game=None,  # No .next for event filler
            last_game=None,  # No .last for event filler
        )

    def _build_odds(self, odds_data: dict, is_home: bool) -> Odds:
        """Convert raw odds dict to Odds dataclass.

        Adjusts moneylines based on home/away perspective.
        """
        if is_home:
            team_ml = odds_data.get("home_moneyline") or 0
            opp_ml = odds_data.get("away_moneyline") or 0
        else:
            team_ml = odds_data.get("away_moneyline") or 0
            opp_ml = odds_data.get("home_moneyline") or 0

        return Odds(
            provider=odds_data.get("provider", ""),
            spread=abs(odds_data.get("spread", 0.0)),
            over_under=odds_data.get("over_under", 0.0),
            details=odds_data.get("details", ""),
            team_moneyline=team_ml,
            opponent_moneyline=opp_ml,
        )

    def _get_team_stats(self, team_id: str, league: str) -> TeamStats | None:
        """Get team stats with caching.

        Returns None if no service is available or stats can't be fetched.
        """
        if not self._service:
            return None

        cache_key = (team_id, league)
        if cache_key not in self._stats_cache:
            try:
                self._stats_cache[cache_key] = self._service.get_team_stats(team_id, league)
            except Exception as e:
                logger.warning("[STATS] Failed to fetch stats for team %s: %s", team_id, e)
                self._stats_cache[cache_key] = None
        return self._stats_cache[cache_key]

    def _select_postgame_template(self, event: Event, config: EventFillerConfig) -> FillerTemplate:
        """Select appropriate postgame template based on game status.

        Supports conditional descriptions (final vs in-progress).
        Fetches fresh status from provider for accurate final detection.
        """
        if not config.postgame_conditional.enabled:
            return config.postgame_template

        # Refresh event status for accurate final detection
        is_final = self._check_event_final(event)

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

    def _check_event_final(self, event: Event) -> bool:
        """Check if event is final, refreshing status from provider if needed.

        Fetches fresh status via summary endpoint to get accurate final detection.
        """
        if not event:
            return False

        # Refresh event status from provider for accurate final detection
        if self._service:
            refreshed = self._service.refresh_event_status(event)
        else:
            refreshed = event

        # Use unified final status check
        from teamarr.utilities.event_status import is_event_final

        return is_event_final(refreshed)


def template_to_event_filler_config(template) -> EventFillerConfig:
    """Convert database Template to EventFillerConfig.

    Args:
        template: Template from database (duck-typed for import avoidance)

    Returns:
        EventFillerConfig ready for EventFillerGenerator
    """
    # Build pregame template from fallback (no hardcoded defaults - schema provides them)
    pregame_fb = getattr(template, "pregame_fallback", None) or {}
    pregame_template = FillerTemplate(
        title=pregame_fb.get("title", ""),
        subtitle=pregame_fb.get("subtitle"),
        description=pregame_fb.get("description", ""),
        art_url=pregame_fb.get("art_url"),
    )

    # Build postgame template from fallback (no hardcoded defaults - schema provides them)
    postgame_fb = getattr(template, "postgame_fallback", None) or {}
    postgame_template = FillerTemplate(
        title=postgame_fb.get("title", ""),
        subtitle=postgame_fb.get("subtitle"),
        description=postgame_fb.get("description", ""),
        art_url=postgame_fb.get("art_url"),
    )

    # Postgame conditional
    pg_cond = getattr(template, "postgame_conditional", None) or {}
    postgame_conditional = ConditionalFillerTemplate(
        enabled=pg_cond.get("enabled", False),
        description_final=pg_cond.get("description_final"),
        description_not_final=pg_cond.get("description_not_final"),
    )

    # Get categories from template
    categories = getattr(template, "xmltv_categories", None) or []

    return EventFillerConfig(
        pregame_enabled=getattr(template, "pregame_enabled", True),
        pregame_template=pregame_template,
        postgame_enabled=getattr(template, "postgame_enabled", True),
        postgame_template=postgame_template,
        postgame_conditional=postgame_conditional,
        xmltv_categories=categories,
        categories_apply_to=getattr(template, "categories_apply_to", "events"),
    )
