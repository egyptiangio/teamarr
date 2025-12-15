"""Context builder for template resolution.

Assembles TemplateContext from Event/Team data using SportsDataService.
This is the bridge between the data layer and the template engine.
"""

import logging

from teamarr.core import Event, TeamStats
from teamarr.services.sports_data import SportsDataService
from teamarr.templates.context import (
    GameContext,
    TeamConfig,
    TemplateContext,
)
from teamarr.utilities.sports import get_sport_from_league

logger = logging.getLogger(__name__)


class ContextBuilder:
    """Builds TemplateContext from events and team configuration.

    Usage:
        builder = ContextBuilder(sports_service)
        context = builder.build_for_event(
            event=event,
            team_id="8",
            league="nfl",
        )
        # Use context with TemplateResolver
    """

    def __init__(self, sports_service: SportsDataService):
        self._service = sports_service
        # Cache for team stats to avoid redundant API calls
        self._stats_cache: dict[tuple[str, str], TeamStats | None] = {}

    def build_for_event(
        self,
        event: Event,
        team_id: str,
        league: str,
        team_stats: TeamStats | None = None,
        next_event: Event | None = None,
        last_event: Event | None = None,
    ) -> TemplateContext:
        """Build complete template context for an event.

        Args:
            event: The current game/event
            team_id: ID of the team this context is for
            league: League identifier
            team_stats: Pre-fetched team stats (optional, will fetch if None)
            next_event: Next scheduled game (optional)
            last_event: Last completed game (optional)

        Returns:
            Complete TemplateContext ready for template resolution
        """
        # Get team info from event
        is_home = event.home_team.id == team_id
        team = event.home_team if is_home else event.away_team

        # Build team config - use event.sport (provider is authoritative)
        team_config = TeamConfig(
            team_id=team_id,
            league=league,
            sport=event.sport,
            team_name=team.name,
            team_abbrev=team.abbreviation,
        )

        # Fetch team stats if not provided
        if team_stats is None:
            team_stats = self._get_team_stats(team_id, league)

        # Build game context for current event
        game_context = self._build_game_context(
            event=event,
            team_id=team_id,
            league=league,
        )

        # Build contexts for next/last games if provided
        next_game = None
        if next_event:
            next_game = self._build_game_context(
                event=next_event,
                team_id=team_id,
                league=league,
            )

        last_game = None
        if last_event:
            last_game = self._build_game_context(
                event=last_event,
                team_id=team_id,
                league=league,
            )

        return TemplateContext(
            game_context=game_context,
            team_config=team_config,
            team_stats=team_stats,
            team=team,
            next_game=next_game,
            last_game=last_game,
        )

    def build_minimal(
        self,
        team_id: str,
        league: str,
        team_name: str,
        team_abbrev: str | None = None,
    ) -> TemplateContext:
        """Build minimal context without game info.

        Useful for non-game content or when event data isn't available.
        """
        team_config = TeamConfig(
            team_id=team_id,
            league=league,
            sport=self._get_sport(league),
            team_name=team_name,
            team_abbrev=team_abbrev,
        )

        team_stats = self._get_team_stats(team_id, league)

        return TemplateContext(
            game_context=None,
            team_config=team_config,
            team_stats=team_stats,
        )

    def _build_game_context(
        self,
        event: Event,
        team_id: str,
        league: str,
    ) -> GameContext:
        """Build GameContext for a single event."""
        is_home = event.home_team.id == team_id
        team = event.home_team if is_home else event.away_team
        opponent = event.away_team if is_home else event.home_team

        # Fetch opponent stats
        opponent_stats = self._get_team_stats(opponent.id, league)

        # H2H and streaks would require additional API calls/calculations
        # For now, leave as None - can be populated by orchestrator
        h2h = None
        streaks = None

        return GameContext(
            event=event,
            is_home=is_home,
            team=team,
            opponent=opponent,
            opponent_stats=opponent_stats,
            h2h=h2h,
            streaks=streaks,
        )

    def _get_team_stats(self, team_id: str, league: str) -> TeamStats | None:
        """Get team stats with caching."""
        cache_key = (team_id, league)
        if cache_key not in self._stats_cache:
            try:
                self._stats_cache[cache_key] = self._service.get_team_stats(
                    team_id, league
                )
            except Exception as e:
                logger.warning(f"Failed to fetch stats for team {team_id}: {e}")
                self._stats_cache[cache_key] = None
        return self._stats_cache[cache_key]

    def _get_sport(self, league: str) -> str:
        """Derive sport from league identifier (fallback).

        Prefer using event.sport or team.sport when available.
        This is only used when no Event is available (e.g., build_minimal).
        """
        return get_sport_from_league(league)

    def clear_cache(self) -> None:
        """Clear the stats cache."""
        self._stats_cache.clear()


def build_context_for_event(
    event: Event,
    team_id: str,
    league: str,
    sports_service: SportsDataService,
) -> TemplateContext:
    """Convenience function to build context for a single event.

    For batch processing, use ContextBuilder directly to benefit from caching.
    """
    builder = ContextBuilder(sports_service)
    return builder.build_for_event(event, team_id, league)
