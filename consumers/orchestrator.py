"""EPG Orchestrator - coordinates EPG generation.

Pure dataclass pipeline:
DB rows → TeamChannelConfig → TeamEPGGenerator → Programme → XMLTV

This module provides a clean interface for EPG generation without
dict conversion layers. All processing uses dataclasses.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from consumers.team_epg import (
    TeamChannelConfig,
    TeamEPGGenerator,
    TeamEPGOptions,
)
from core import Programme
from database import get_connection
from services import SportsDataService, create_default_service
from utilities.xmltv import programmes_to_xmltv

logger = logging.getLogger(__name__)


@dataclass
class GenerationResult:
    """Result of an EPG generation run."""

    programmes: list[Programme]
    xmltv: str
    teams_processed: int
    started_at: datetime
    completed_at: datetime
    api_calls: int = 0


class Orchestrator:
    """Coordinates EPG generation workflow.

    Reads team configurations from database, generates EPG
    using pure dataclass pipeline, outputs XMLTV.
    """

    def __init__(self, service: SportsDataService | None = None):
        self._service = service or create_default_service()
        self._team_generator = TeamEPGGenerator(self._service)

    def generate_for_teams(
        self,
        team_configs: list[TeamChannelConfig],
        options: TeamEPGOptions | None = None,
    ) -> GenerationResult:
        """Generate EPG for provided team configurations.

        Use this when you have already loaded team configs from the database.
        """
        started_at = datetime.now()
        base_options = options or TeamEPGOptions()

        all_programmes: list[Programme] = []

        for config in team_configs:
            # Fetch additional leagues for multi-league support
            additional_leagues = self._get_additional_leagues(config)

            programmes = self._team_generator.generate(
                config=config,
                options=base_options,
                additional_leagues=additional_leagues,
            )
            all_programmes.extend(programmes)

        # Build channel list for XMLTV
        channels = [
            {
                "id": config.channel_id,
                "name": config.team_name,
                "icon": config.logo_url,
            }
            for config in team_configs
        ]

        xmltv = programmes_to_xmltv(all_programmes, channels)

        return GenerationResult(
            programmes=all_programmes,
            xmltv=xmltv,
            teams_processed=len(team_configs),
            started_at=started_at,
            completed_at=datetime.now(),
        )

    def generate_from_database(
        self,
        options: TeamEPGOptions | None = None,
        progress_callback=None,
    ) -> GenerationResult:
        """Generate EPG for all active teams from database.

        This is the main entry point for EPG generation.
        Loads teams with templates from database, generates EPG.

        Args:
            options: Generation options
            progress_callback: Optional callback(current, total, team_name)
        """
        started_at = datetime.now()
        base_options = options or self._load_options_from_database()

        # Load team configs from database
        team_configs = self._load_teams_from_database()

        if not team_configs:
            logger.warning("No active teams with templates found")
            return GenerationResult(
                programmes=[],
                xmltv="",
                teams_processed=0,
                started_at=started_at,
                completed_at=datetime.now(),
            )

        logger.info(f"Processing {len(team_configs)} teams")

        all_programmes: list[Programme] = []

        for i, config in enumerate(team_configs):
            if progress_callback:
                progress_callback(i + 1, len(team_configs), config.team_name)

            # Fetch additional leagues for multi-league support
            additional_leagues = self._get_additional_leagues(config)

            programmes = self._team_generator.generate(
                config=config,
                options=base_options,
                additional_leagues=additional_leagues,
            )
            all_programmes.extend(programmes)
            logger.debug(
                f"Generated {len(programmes)} programmes for {config.team_name}"
            )

        # Build channel list for XMLTV
        channels = [
            {
                "id": config.channel_id,
                "name": config.team_name,
                "icon": config.logo_url,
            }
            for config in team_configs
        ]

        xmltv = programmes_to_xmltv(all_programmes, channels)

        logger.info(
            f"Generated {len(all_programmes)} total programmes for {len(team_configs)} teams"
        )

        return GenerationResult(
            programmes=all_programmes,
            xmltv=xmltv,
            teams_processed=len(team_configs),
            started_at=started_at,
            completed_at=datetime.now(),
        )

    def _load_teams_from_database(self) -> list[TeamChannelConfig]:
        """Load active teams with templates from database.

        Returns TeamChannelConfig dataclasses (no dicts).
        """
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT
                    t.espn_team_id,
                    t.league,
                    t.team_name,
                    t.team_abbrev,
                    t.channel_id,
                    t.team_logo_url,
                    tp.title_template,
                    tp.description_template,
                    tp.subtitle_template,
                    tp.default_category,
                    tp.pregame_title,
                    tp.pregame_description,
                    tp.postgame_title,
                    tp.postgame_description,
                    tp.idle_title,
                    tp.idle_description,
                    lc.sport as league_sport
                FROM teams t
                INNER JOIN templates tp ON t.template_id = tp.id
                LEFT JOIN league_config lc ON t.league = lc.league_code
                WHERE t.active = 1 AND t.template_id IS NOT NULL
                ORDER BY t.team_name
            """
            ).fetchall()

            configs = []
            for row in rows:
                config = TeamChannelConfig(
                    team_id=str(row["espn_team_id"]),
                    league=row["league"],
                    channel_id=row["channel_id"] or f"team-{row['espn_team_id']}",
                    team_name=row["team_name"],
                    team_abbrev=row["team_abbrev"],
                    logo_url=row["team_logo_url"],
                    sport=row["league_sport"],
                    # Template overrides
                    title_format=row["title_template"],
                    description_format=row["description_template"],
                    subtitle_format=row["subtitle_template"],
                    category=row["default_category"],
                    pregame_title=row["pregame_title"],
                    pregame_description=row["pregame_description"],
                    postgame_title=row["postgame_title"],
                    postgame_description=row["postgame_description"],
                    idle_title=row["idle_title"],
                    idle_description=row["idle_description"],
                )
                configs.append(config)

            logger.info(f"Loaded {len(configs)} active teams from database")
            return configs

        finally:
            conn.close()

    def _load_options_from_database(self) -> TeamEPGOptions:
        """Load EPG generation options from database settings."""
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM settings WHERE id = 1"
            ).fetchone()

            if not row:
                return TeamEPGOptions()

            settings = dict(row)

            # Build sport durations dict
            sport_durations = {
                "basketball": settings.get("duration_basketball", 3.0),
                "football": settings.get("duration_football", 3.5),
                "hockey": settings.get("duration_hockey", 3.0),
                "baseball": settings.get("duration_baseball", 3.5),
                "soccer": settings.get("duration_soccer", 2.5),
                "mma": settings.get("duration_mma", 5.0),
            }

            return TeamEPGOptions(
                schedule_days_ahead=settings.get("team_schedule_days_ahead", 30),
                output_days_ahead=settings.get("epg_output_days_ahead", 14),
                pregame_minutes=settings.get("pregame_minutes", 30),
                default_duration_hours=settings.get("duration_default", 3.0),
                filler_enabled=settings.get("filler_enabled", True),
                epg_timezone=settings.get("epg_timezone", "America/New_York"),
                sport_durations=sport_durations,
            )

        finally:
            conn.close()

    def _get_additional_leagues(self, config: TeamChannelConfig) -> list[str]:
        """Get additional leagues for multi-league support (soccer).

        Uses the team/league cache to find all leagues a team plays in.
        """
        # Try to use the unified cache
        try:
            from services import get_cache

            cache = get_cache()
            leagues = cache.get_team_leagues(config.team_id, "espn")
            # Remove primary league
            return [lg for lg in leagues if lg != config.league]
        except Exception:
            # Cache not available or error - return empty
            return []

    # Backward compatibility alias
    generate = generate_from_database
