"""Tournament event parsing for ESPN provider.

Handles sports like tennis, golf, and racing that don't have
traditional home/away matchups.
"""

import logging
from datetime import date, datetime

from teamarr.core import Event, EventStatus, Team, Venue

logger = logging.getLogger(__name__)


class TournamentParserMixin:
    """Mixin providing tournament-specific parsing methods.

    Requires:
        - self._client: ESPNClient instance
        - self.name: Provider name ('espn')
    """

    def _get_tournament_events(self, league: str, target_date: date, sport: str) -> list[Event]:
        """Get events for tournament sports (tennis, golf, racing).

        These sports have tournaments/races as events with many competitors,
        not head-to-head matchups with home/away.
        """
        date_str = target_date.strftime("%Y%m%d")
        data = self._client.get_scoreboard(league, date_str)
        if not data:
            return []

        events = []
        for event_data in data.get("events", []):
            event = self._parse_tournament_event(event_data, league, sport)
            if event:
                events.append(event)

        return events

    def _parse_tournament_event(self, data: dict, league: str, sport: str) -> Event | None:
        """Parse a tournament-style event (tennis, golf, racing).

        Creates placeholder 'teams' representing the tournament/event itself.
        """
        try:
            event_id = data.get("id", "")
            if not event_id:
                return None

            # Parse start time
            date_str = data.get("date")
            if not date_str:
                return None

            start_time = datetime.fromisoformat(date_str.replace("Z", "+00:00"))

            event_name = data.get("name", "")
            short_name = data.get("shortName", event_name)

            # For tournaments, create placeholder "teams"
            # This allows the event to work with existing matching logic
            tournament_team = Team(
                id=f"tournament_{event_id}",
                provider=self.name,
                name=event_name,
                short_name=short_name[:20] if short_name else "",
                abbreviation=self._make_tournament_abbrev(event_name),
                league=league,
                sport=sport,
                logo_url=None,
                color=None,
            )

            # Parse status
            status_data = data.get("status", {})
            type_data = status_data.get("type", {}) if status_data else {}
            state = type_data.get("state", "pre")

            if state == "in":
                status = EventStatus(state="live", detail=type_data.get("detail"))
            elif state == "post":
                status = EventStatus(state="final", detail=type_data.get("detail"))
            else:
                status = EventStatus(state="scheduled")

            # Parse venue if available
            venue = None
            competitions = data.get("competitions", [])
            if competitions:
                venue_data = competitions[0].get("venue")
                if venue_data:
                    venue = Venue(
                        name=venue_data.get("fullName", ""),
                        city=venue_data.get("address", {}).get("city", ""),
                        state=venue_data.get("address", {}).get("state", ""),
                        country=venue_data.get("address", {}).get("country", ""),
                    )

            return Event(
                id=str(event_id),
                provider=self.name,
                name=event_name,
                short_name=short_name,
                start_time=start_time,
                home_team=tournament_team,
                away_team=tournament_team,  # Same team for tournaments
                status=status,
                league=league,
                sport=sport,
                venue=venue,
                broadcasts=[],
            )

        except Exception as e:
            logger.warning(f"Failed to parse tournament event: {e}")
            return None

    def _make_tournament_abbrev(self, name: str) -> str:
        """Make abbreviation for tournament name."""
        # Take first letters of significant words
        words = [w for w in name.split() if len(w) > 2]
        if len(words) >= 2:
            return "".join(w[0].upper() for w in words[:4])
        return name[:6].upper()
