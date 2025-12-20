"""Team Processor - orchestrates the full team-based EPG flow.

Processes all active teams from the database:
1. Load team configs from database
2. Generate EPG using TeamEPGGenerator
3. Store XMLTV in database
4. Track processing stats

This is the main entry point for team-based EPG generation from the scheduler.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from sqlite3 import Connection
from typing import Any

from teamarr.consumers.team_epg import TeamEPGGenerator, TeamEPGOptions
from teamarr.core import Programme
from teamarr.database.stats import create_run, save_run
from teamarr.services import SportsDataService, create_default_service
from teamarr.utilities.xmltv import programmes_to_xmltv

logger = logging.getLogger(__name__)


@dataclass
class TeamConfig:
    """Team configuration from database."""

    id: int
    provider: str
    provider_team_id: str
    league: str
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

    def process_all_teams(self) -> BatchTeamResult:
        """Process all active teams.

        Returns:
            BatchTeamResult with all team results and combined XMLTV
        """
        batch_result = BatchTeamResult()

        with self._db_factory() as conn:
            teams = self._get_active_teams(conn)

            channels: list[dict] = []

            for team in teams:
                result = self._process_team_internal(conn, team)
                batch_result.results.append(result)

                # Collect channel info for XMLTV
                if result.programmes_generated > 0:
                    channels.append(
                        {
                            "id": team.channel_id,
                            "name": team.team_name,
                            "icon": team.channel_logo_url or team.team_logo_url,
                        }
                    )

            # Get all programmes from results (regenerate from storage)
            for team in teams:
                xmltv = self._get_team_xmltv(conn, team.id)
                if xmltv:
                    # Store individual team XMLTV
                    pass

            # Generate combined XMLTV from all teams
            if channels:
                # Re-generate all programmes for combined XMLTV
                combined_programmes = self._generate_all_programmes(conn, teams)
                if combined_programmes:
                    batch_result.total_xmltv = programmes_to_xmltv(combined_programmes, channels)

        batch_result.completed_at = datetime.now()
        return batch_result

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
            logger.warning(
                f"Skipping team '{team.team_name}': no template assigned"
            )
            result.errors.append("No template assigned - EPG generation requires a template")
            result.completed_at = datetime.now()
            return result

        # Create stats run for tracking
        stats_run = create_run(conn, run_type="team_epg", team_id=team.id)

        try:
            # Build options
            options = self._build_options(conn, team)

            # Generate programmes using TeamEPGGenerator
            programmes = self._epg_generator.generate_auto_discover(
                team_id=team.provider_team_id,
                primary_league=team.league,
                channel_id=team.channel_id,
                team_name=team.team_name,
                team_abbrev=team.team_abbrev,
                logo_url=team.channel_logo_url or team.team_logo_url,
                options=options,
                provider=team.provider,
                sport=team.sport,
            )

            # Count programme types
            result.programmes_generated = len(programmes)
            for prog in programmes:
                # Categorize by title patterns (heuristic)
                title_lower = prog.title.lower() if prog.title else ""
                pregame_terms = ["preview", "pre-game", "pregame", "starting soon"]
                postgame_terms = ["recap", "highlights", "replay", "postgame", "post-game"]
                if any(x in title_lower for x in pregame_terms):
                    result.programmes_pregame += 1
                elif any(x in title_lower for x in postgame_terms):
                    result.programmes_postgame += 1
                elif any(x in title_lower for x in ["programming", "off day", "no games"]):
                    result.programmes_idle += 1
                else:
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

            # Update stats
            stats_run.programmes_total = result.programmes_generated
            stats_run.programmes_events = result.programmes_events
            stats_run.programmes_pregame = result.programmes_pregame
            stats_run.programmes_postgame = result.programmes_postgame
            stats_run.programmes_idle = result.programmes_idle
            stats_run.complete(status="completed")

            logger.info(
                f"Processed team '{team.team_name}': {result.programmes_generated} programmes"
            )

        except Exception as e:
            logger.exception(f"Error processing team {team.team_name}")
            result.errors.append(str(e))
            stats_run.complete(status="failed", error=str(e))

        # Save stats run
        save_run(conn, stats_run)

        result.completed_at = datetime.now()
        return result

    def _build_options(self, conn: Connection, team: TeamConfig) -> TeamEPGOptions:
        """Build TeamEPGOptions from database settings."""
        # Load global settings
        row = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
        settings = dict(row) if row else {}

        # Sport durations
        sport_durations = {
            "basketball": settings.get("duration_basketball", 3.0),
            "football": settings.get("duration_football", 3.5),
            "hockey": settings.get("duration_hockey", 3.0),
            "baseball": settings.get("duration_baseball", 3.5),
            "soccer": settings.get("duration_soccer", 2.5),
            "mma": settings.get("duration_mma", 5.0),
            "boxing": settings.get("duration_boxing", 4.0),
        }

        return TeamEPGOptions(
            schedule_days_ahead=settings.get("team_schedule_days_ahead", 30),
            output_days_ahead=settings.get("epg_output_days_ahead", 14),
            default_duration_hours=settings.get("duration_default", 3.0),
            sport_durations=sport_durations,
            epg_timezone=settings.get("epg_timezone", "America/New_York"),
            midnight_crossover_mode=settings.get("midnight_crossover_mode", "postgame"),
            template_id=team.template_id,
            filler_enabled=True,
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
        return TeamConfig(
            id=row["id"],
            provider=row["provider"],
            provider_team_id=row["provider_team_id"],
            league=row["league"],
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
        except Exception:
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
                primary_league=team.league,
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
    """Get all stored XMLTV content for teams.

    Args:
        conn: Database connection
        team_ids: Optional list of team IDs to filter (None = all)

    Returns:
        List of XMLTV content strings
    """
    try:
        if team_ids:
            placeholders = ",".join("?" * len(team_ids))
            cursor = conn.execute(
                f"SELECT xmltv_content FROM team_epg_xmltv WHERE team_id IN ({placeholders})",
                team_ids,
            )
        else:
            cursor = conn.execute("SELECT xmltv_content FROM team_epg_xmltv")

        return [row["xmltv_content"] for row in cursor.fetchall()]
    except Exception:
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
) -> BatchTeamResult:
    """Process all active teams.

    Convenience function that creates a processor and runs it.

    Args:
        db_factory: Factory function returning database connection

    Returns:
        BatchTeamResult
    """
    processor = TeamProcessor(db_factory=db_factory)
    return processor.process_all_teams()
