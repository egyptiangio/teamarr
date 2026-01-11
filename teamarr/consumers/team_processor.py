"""Team Processor - orchestrates the full team-based EPG flow.

Processes all active teams from the database:
1. Load team configs from database
2. Generate EPG using TeamEPGGenerator (parallel with ThreadPoolExecutor)
3. Store XMLTV in database
4. Track processing stats

This is the main entry point for team-based EPG generation from the scheduler.
"""

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from sqlite3 import Connection
from typing import Any

from teamarr.consumers.team_epg import TeamEPGGenerator, TeamEPGOptions
from teamarr.core import Programme
from teamarr.services import SportsDataService, create_default_service
from teamarr.utilities.xmltv import programmes_to_xmltv

# Number of parallel workers for team processing
MAX_WORKERS = 100

logger = logging.getLogger(__name__)


@dataclass
class TeamConfig:
    """Team configuration from database."""

    id: int
    provider: str
    provider_team_id: str
    primary_league: str
    leagues: list[str]
    sport: str
    team_name: str
    team_abbrev: str | None
    team_logo_url: str | None
    channel_id: str
    channel_logo_url: str | None
    template_id: int | None
    active: bool


@dataclass
class TeamProcessingResult:
    """Result of processing a single team."""

    team_id: int
    team_name: str
    channel_id: str
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None

    # EPG generation
    programmes_generated: int = 0
    programmes_events: int = 0
    programmes_pregame: int = 0
    programmes_postgame: int = 0
    programmes_idle: int = 0

    # Errors
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return {
            "team_id": self.team_id,
            "team_name": self.team_name,
            "channel_id": self.channel_id,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "programmes": {
                "total": self.programmes_generated,
                "events": self.programmes_events,
                "pregame": self.programmes_pregame,
                "postgame": self.programmes_postgame,
                "idle": self.programmes_idle,
            },
            "errors": self.errors,
        }


@dataclass
class BatchTeamResult:
    """Result of processing multiple teams."""

    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    results: list[TeamProcessingResult] = field(default_factory=list)
    total_xmltv: str = ""

    @property
    def teams_processed(self) -> int:
        return len(self.results)

    @property
    def total_programmes(self) -> int:
        return sum(r.programmes_generated for r in self.results)

    @property
    def total_events(self) -> int:
        return sum(r.programmes_events for r in self.results)

    @property
    def total_pregame(self) -> int:
        return sum(r.programmes_pregame for r in self.results)

    @property
    def total_postgame(self) -> int:
        return sum(r.programmes_postgame for r in self.results)

    @property
    def total_idle(self) -> int:
        return sum(r.programmes_idle for r in self.results)

    @property
    def total_errors(self) -> int:
        return sum(len(r.errors) for r in self.results)

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return {
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "teams_processed": self.teams_processed,
            "total_programmes": self.total_programmes,
            "total_errors": self.total_errors,
            "results": [r.to_dict() for r in self.results],
        }


class TeamProcessor:
    """Processes teams - generates EPG for team-based channels.

    Usage:
        from teamarr.database import get_db

        processor = TeamProcessor(db_factory=get_db)

        # Process a single team
        result = processor.process_team(team_id=1)

        # Process all active teams
        result = processor.process_all_teams()
    """

    def __init__(
        self,
        db_factory: Any,
        service: SportsDataService | None = None,
    ):
        """Initialize the processor.

        Args:
            db_factory: Factory function returning database connection
            service: Optional SportsDataService (creates default if not provided)
        """
        self._db_factory = db_factory
        self._service = service or create_default_service()
        self._epg_generator = TeamEPGGenerator(self._service)

    def process_team(self, team_id: int) -> TeamProcessingResult:
        """Process a single team.

        Args:
            team_id: Team ID to process

        Returns:
            TeamProcessingResult with all details
        """
        with self._db_factory() as conn:
            team = self._get_team(conn, team_id)
            if not team:
                result = TeamProcessingResult(
                    team_id=team_id,
                    team_name="Unknown",
                    channel_id="unknown",
                )
                result.errors.append(f"Team {team_id} not found")
                result.completed_at = datetime.now()
                return result

            return self._process_team_internal(conn, team)

    def process_all_teams(
        self,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> BatchTeamResult:
        """Process all active teams.

        ESPN teams are processed in parallel (up to MAX_WORKERS).
        TSDB teams are processed sequentially (rate limit is ~10/min).

        Args:
            progress_callback: Optional callback(current, total, team_name)

        Returns:
            BatchTeamResult with all team results and combined XMLTV
        """
        batch_result = BatchTeamResult()

        with self._db_factory() as conn:
            teams = self._get_active_teams(conn)

        if not teams:
            batch_result.completed_at = datetime.now()
            return batch_result

        total_teams = len(teams)
        processed_count = 0

        # Separate teams by provider
        espn_teams = [t for t in teams if t.provider == "espn"]
        tsdb_teams = [t for t in teams if t.provider == "tsdb"]

        channels: list[dict] = []

        # Process ESPN teams in parallel
        if espn_teams:
            num_workers = min(MAX_WORKERS, len(espn_teams))
            logger.info(
                f"Processing {len(espn_teams)} ESPN teams with {num_workers} parallel workers"
            )

            # Track in-progress teams for accurate progress display
            in_progress: set[str] = set()
            in_progress_lock = threading.Lock()

            def process_with_tracking(team: TeamConfig) -> TeamProcessingResult:
                """Wrapper to track in-progress state."""
                with in_progress_lock:
                    in_progress.add(team.team_name)
                    # Report which team is now being processed
                    if progress_callback:
                        progress_callback(
                            processed_count,
                            total_teams,
                            f"Processing {team.team_name}...",
                        )
                try:
                    return self._process_team_parallel(team)
                finally:
                    with in_progress_lock:
                        in_progress.discard(team.team_name)

            with ThreadPoolExecutor(max_workers=num_workers) as executor:
                future_to_team = {
                    executor.submit(process_with_tracking, team): team for team in espn_teams
                }

                for future in as_completed(future_to_team):
                    logger.debug(f"Future completed, processing result...")
                    team = future_to_team[future]
                    processed_count += 1
                    try:
                        result = future.result()
                        batch_result.results.append(result)

                        if result.programmes_generated > 0:
                            channels.append(
                                {
                                    "id": team.channel_id,
                                    "name": team.team_name,
                                    "icon": team.channel_logo_url or team.team_logo_url,
                                }
                            )
                    except Exception as e:
                        logger.exception(f"Error processing team {team.team_name}")
                        error_result = TeamProcessingResult(
                            team_id=team.id,
                            team_name=team.team_name,
                            channel_id=team.channel_id,
                        )
                        error_result.errors.append(str(e))
                        error_result.completed_at = datetime.now()
                        batch_result.results.append(error_result)

                    # Report progress with remaining in-progress teams
                    if progress_callback:
                        with in_progress_lock:
                            still_processing = list(in_progress)
                        if still_processing:
                            msg = f"Finished {team.team_name}, now processing: {', '.join(still_processing[:3])}"
                            if len(still_processing) > 3:
                                msg += f" (+{len(still_processing) - 3} more)"
                        else:
                            msg = f"Finished {team.team_name}"
                        progress_callback(processed_count, total_teams, msg)

            logger.info("ESPN parallel processing loop complete, exiting executor context")

        logger.info("Executor context exited")

        # Process TSDB teams sequentially (rate limited API)
        if tsdb_teams:
            # Extract unique leagues and pre-warm cache
            tsdb_leagues = set()
            for team in tsdb_teams:
                tsdb_leagues.add(team.primary_league)
                tsdb_leagues.update(team.leagues)

            logger.info(
                f"Processing {len(tsdb_teams)} TSDB teams from "
                f"{len(tsdb_leagues)} leagues sequentially"
            )

            # Report that we're warming cache (this can take a while)
            if progress_callback:
                progress_callback(
                    processed_count,
                    total_teams,
                    f"Warming TSDB cache ({len(tsdb_leagues)} leagues)...",
                )

            # Pre-warm TSDB cache for all leagues (2 API calls per league)
            # This ensures cache hits when processing individual teams
            self._service.prewarm_tsdb_leagues(list(tsdb_leagues))

            # Group teams by primary league for better cache utilization
            # Teams in the same league share eventsday.php cache entries
            sorted_tsdb_teams = sorted(tsdb_teams, key=lambda t: t.primary_league)

            for team in sorted_tsdb_teams:
                processed_count += 1
                try:
                    result = self._process_team_parallel(team)
                    batch_result.results.append(result)

                    if result.programmes_generated > 0:
                        channels.append(
                            {
                                "id": team.channel_id,
                                "name": team.team_name,
                                "icon": team.channel_logo_url or team.team_logo_url,
                            }
                        )
                except Exception as e:
                    logger.exception(f"Error processing team {team.team_name}")
                    error_result = TeamProcessingResult(
                        team_id=team.id,
                        team_name=team.team_name,
                        channel_id=team.channel_id,
                    )
                    error_result.errors.append(str(e))
                    error_result.completed_at = datetime.now()
                    batch_result.results.append(error_result)

                # Report progress
                if progress_callback:
                    progress_callback(processed_count, total_teams, team.team_name)

        # Note: Combined XMLTV is read from database in generation.py
        # Each team's XMLTV is already stored during _process_team_internal

        batch_result.completed_at = datetime.now()
        logger.info(f"Completed processing {len(teams)} teams")
        return batch_result

    def _process_team_parallel(self, team: TeamConfig) -> TeamProcessingResult:
        """Process a single team with its own DB connection (for parallel execution)."""
        with self._db_factory() as conn:
            return self._process_team_internal(conn, team)

    def _process_team_internal(
        self,
        conn: Connection,
        team: TeamConfig,
    ) -> TeamProcessingResult:
        """Internal processing for a single team."""
        result = TeamProcessingResult(
            team_id=team.id,
            team_name=team.team_name,
            channel_id=team.channel_id,
        )

        # Skip teams without a valid template
        if team.template_id is None:
            logger.warning(f"Skipping team '{team.team_name}': no template assigned")
            result.errors.append("No template assigned - EPG generation requires a template")
            result.completed_at = datetime.now()
            return result

        try:
            # Build options
            options = self._build_options(conn, team)

            # Generate programmes using TeamEPGGenerator
            programmes = self._epg_generator.generate_auto_discover(
                team_id=team.provider_team_id,
                primary_league=team.primary_league,
                channel_id=team.channel_id,
                team_name=team.team_name,
                team_abbrev=team.team_abbrev,
                logo_url=team.channel_logo_url or team.team_logo_url,
                options=options,
                provider=team.provider,
                sport=team.sport,
            )

            # Count programme types by filler_type field (set during creation)
            result.programmes_generated = len(programmes)
            for prog in programmes:
                if prog.filler_type == "pregame":
                    result.programmes_pregame += 1
                elif prog.filler_type == "postgame":
                    result.programmes_postgame += 1
                elif prog.filler_type == "idle":
                    result.programmes_idle += 1
                else:
                    # filler_type is None = actual event programme
                    result.programmes_events += 1

            # Generate XMLTV for this team
            if programmes:
                channel_dict = {
                    "id": team.channel_id,
                    "name": team.team_name,
                    "icon": team.channel_logo_url or team.team_logo_url,
                }
                xmltv_content = programmes_to_xmltv(programmes, [channel_dict])
                self._store_team_xmltv(conn, team.id, xmltv_content)

            logger.info(
                f"Processed team '{team.team_name}': {result.programmes_generated} programmes"
            )

        except Exception as e:
            logger.exception(f"Error processing team {team.team_name}")
            result.errors.append(str(e))

        result.completed_at = datetime.now()
        return result

    def _build_options(self, conn: Connection, team: TeamConfig) -> TeamEPGOptions:
        """Build TeamEPGOptions from database settings.

        Pre-loads the template and filler config here to avoid DB access
        in the EPG generator, which is critical for thread-safety during
        parallel processing.
        """
        from teamarr.database.settings import get_all_settings
        from teamarr.database.templates import (
            get_template,
            template_to_filler_config,
            template_to_programme_config,
        )

        # Load global settings
        all_settings = get_all_settings(conn)

        # Sport durations - dynamically loaded from DurationSettings dataclass
        sport_durations = asdict(all_settings.durations)

        # Pre-load template and filler config (avoids DB access in parallel threads)
        template_config = None
        filler_config = None
        if team.template_id:
            template = get_template(conn, team.template_id)
            if template:
                template_config = template_to_programme_config(template)
                filler_config = template_to_filler_config(template)
                logger.debug(f"Loaded template {team.template_id} for {team.team_name}")
            else:
                logger.warning(
                    f"Template {team.template_id} not found for {team.team_name} - "
                    "using hardcoded defaults. Assign a valid template to fix."
                )
        else:
            logger.warning(
                f"No template assigned to {team.team_name} - using hardcoded defaults. "
                "Assign a template in the Teams page to customize filler content."
            )

        return TeamEPGOptions(
            schedule_days_ahead=all_settings.epg.team_schedule_days_ahead,
            output_days_ahead=all_settings.epg.epg_output_days_ahead,
            lookback_hours=all_settings.epg.epg_lookback_hours,
            default_duration_hours=all_settings.durations.default,
            sport_durations=sport_durations,
            epg_timezone=all_settings.epg.epg_timezone,
            midnight_crossover_mode=all_settings.epg.midnight_crossover_mode,
            template_id=team.template_id,
            template=template_config,  # Pre-loaded template
            filler_config=filler_config,  # Pre-loaded filler config
            filler_enabled=True,
            include_final_events=all_settings.epg.include_final_events,
        )

    def _get_team(self, conn: Connection, team_id: int) -> TeamConfig | None:
        """Get team by ID."""
        row = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
        if not row:
            return None
        return self._row_to_team(row)

    def _get_active_teams(self, conn: Connection) -> list[TeamConfig]:
        """Get all active teams."""
        cursor = conn.execute("SELECT * FROM teams WHERE active = 1 ORDER BY team_name")
        return [self._row_to_team(row) for row in cursor.fetchall()]

    def _row_to_team(self, row) -> TeamConfig:
        """Convert database row to TeamConfig."""
        import json

        # Parse leagues JSON
        leagues_str = row["leagues"]
        try:
            leagues = json.loads(leagues_str) if leagues_str else []
        except (json.JSONDecodeError, TypeError):
            leagues = []

        return TeamConfig(
            id=row["id"],
            provider=row["provider"],
            provider_team_id=row["provider_team_id"],
            primary_league=row["primary_league"],
            leagues=leagues,
            sport=row["sport"],
            team_name=row["team_name"],
            team_abbrev=row["team_abbrev"],
            team_logo_url=row["team_logo_url"],
            channel_id=row["channel_id"],
            channel_logo_url=row["channel_logo_url"],
            template_id=row["template_id"],
            active=bool(row["active"]),
        )

    def _store_team_xmltv(
        self,
        conn: Connection,
        team_id: int,
        xmltv_content: str,
    ) -> None:
        """Store XMLTV content for a team in the database."""
        # Use a similar table structure as event_epg_xmltv
        # First, ensure the table exists (will be added to schema)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS team_epg_xmltv (
                team_id INTEGER PRIMARY KEY,
                xmltv_content TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE
            )
            """
        )

        conn.execute(
            """
            INSERT INTO team_epg_xmltv (team_id, xmltv_content, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(team_id) DO UPDATE SET
                xmltv_content = excluded.xmltv_content,
                updated_at = datetime('now')
            """,
            (team_id, xmltv_content),
        )
        conn.commit()

    def _get_team_xmltv(self, conn: Connection, team_id: int) -> str | None:
        """Get stored XMLTV for a team."""
        try:
            row = conn.execute(
                "SELECT xmltv_content FROM team_epg_xmltv WHERE team_id = ?",
                (team_id,),
            ).fetchone()
            return row["xmltv_content"] if row else None
        except Exception as e:
            logger.debug(f"Failed to get stored XMLTV for team {team_id}: {e}")
            return None

    def _generate_all_programmes(
        self,
        conn: Connection,
        teams: list[TeamConfig],
    ) -> list[Programme]:
        """Regenerate all programmes for combined XMLTV."""
        all_programmes: list[Programme] = []

        for team in teams:
            # Skip teams without a template
            if team.template_id is None:
                continue

            options = self._build_options(conn, team)

            programmes = self._epg_generator.generate_auto_discover(
                team_id=team.provider_team_id,
                primary_league=team.primary_league,
                channel_id=team.channel_id,
                team_name=team.team_name,
                team_abbrev=team.team_abbrev,
                logo_url=team.channel_logo_url or team.team_logo_url,
                options=options,
                provider=team.provider,
                sport=team.sport,
            )
            all_programmes.extend(programmes)

        return all_programmes


def get_all_team_xmltv(conn: Connection, team_ids: list[int] | None = None) -> list[str]:
    """Get all stored XMLTV content for enabled teams.

    Args:
        conn: Database connection
        team_ids: Optional list of team IDs to filter (None = all enabled)

    Returns:
        List of XMLTV content strings
    """
    try:
        if team_ids:
            placeholders = ",".join("?" * len(team_ids))
            cursor = conn.execute(
                f"""SELECT x.xmltv_content FROM team_epg_xmltv x
                    JOIN teams t ON x.team_id = t.id
                    WHERE t.id IN ({placeholders}) AND t.active = 1
                    AND x.xmltv_content IS NOT NULL AND x.xmltv_content != ''""",
                team_ids,
            )
        else:
            # Get XMLTV for all active teams only
            cursor = conn.execute(
                """SELECT x.xmltv_content FROM team_epg_xmltv x
                   JOIN teams t ON x.team_id = t.id
                   WHERE t.active = 1
                   AND x.xmltv_content IS NOT NULL AND x.xmltv_content != ''"""
            )

        return [row["xmltv_content"] for row in cursor.fetchall()]
    except Exception as e:
        logger.debug(f"Failed to get team XMLTV content: {e}")
        return []


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================


def process_team(
    db_factory: Any,
    team_id: int,
) -> TeamProcessingResult:
    """Process a single team.

    Convenience function that creates a processor and runs it.

    Args:
        db_factory: Factory function returning database connection
        team_id: Team ID to process

    Returns:
        TeamProcessingResult
    """
    processor = TeamProcessor(db_factory=db_factory)
    return processor.process_team(team_id)


def process_all_teams(
    db_factory: Any,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> BatchTeamResult:
    """Process all active teams.

    Convenience function that creates a processor and runs it.

    Args:
        db_factory: Factory function returning database connection
        progress_callback: Optional callback(current, total, team_name)

    Returns:
        BatchTeamResult
    """
    processor = TeamProcessor(db_factory=db_factory)
    return processor.process_all_teams(progress_callback=progress_callback)
