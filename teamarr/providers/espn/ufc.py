"""UFC event parsing for ESPN provider.

Extracted as a mixin to keep provider.py focused on core sports.
"""

import logging
from datetime import date

from teamarr.core import Event, EventStatus, Team
from teamarr.utilities.tz import to_user_tz

logger = logging.getLogger(__name__)


class UFCParserMixin:
    """Mixin providing UFC-specific parsing methods.

    Requires:
        - self._client: ESPNClient instance
        - self.name: Provider name ('espn')
        - self._parse_datetime(date_str): Parse datetime from string
    """

    def _get_ufc_events(self, target_date: date) -> list[Event]:
        """Fetch and parse UFC events for a specific date.

        UFC API returns all upcoming events, so we filter to target_date.
        """
        data = self._client.get_ufc_events()
        if not data:
            return []

        try:
            ufc_events = data["sports"][0]["leagues"][0]["events"]
        except (KeyError, IndexError):
            logger.warning("Unexpected UFC events response structure")
            return []

        events = []
        for event_data in ufc_events:
            event = self._parse_ufc_event(event_data)
            if event:
                # Compare dates in user timezone (late night UTC = same day locally)
                local_date = to_user_tz(event.start_time).date()
                if local_date == target_date:
                    events.append(event)

        return events

    def _parse_ufc_event(self, data: dict) -> Event | None:
        """Parse UFC fight card into Event.

        Maps the main event fighters as home_team/away_team for compatibility.
        Extracts prelims vs main card start times.
        """
        try:
            event_id = str(data.get("id", ""))
            if not event_id:
                return None

            competitions = data.get("competitions", [])
            if not competitions:
                return None

            # Group bouts by start time to find prelims vs main card
            bout_times = set()
            for comp in competitions:
                if "date" in comp:
                    bout_times.add(comp["date"])

            if not bout_times:
                return None

            prelims_start = min(bout_times)
            main_card_start_str = max(bout_times) if len(bout_times) > 1 else None

            # Find the main event (first bout at main card time)
            main_event = None
            if main_card_start_str:
                main_event = next(
                    (c for c in competitions if c.get("date") == main_card_start_str),
                    None,
                )
            if not main_event:
                main_event = competitions[0]

            # Extract fighters as "teams"
            competitors = main_event.get("competitors", [])
            if len(competitors) < 2:
                return None

            fighter1 = self._parse_fighter_as_team(competitors[0])
            fighter2 = self._parse_fighter_as_team(competitors[1])

            # Parse times
            start_time = self._parse_datetime(prelims_start)
            if not start_time:
                return None

            main_card_start = None
            if main_card_start_str and main_card_start_str != prelims_start:
                main_card_start = self._parse_datetime(main_card_start_str)

            # Parse status from main event
            status = self._parse_ufc_status(main_event.get("status", {}))

            return Event(
                id=event_id,
                provider=self.name,
                name=data.get("name", ""),
                short_name=f"{fighter1.short_name} vs {fighter2.short_name}",
                start_time=start_time,
                home_team=fighter1,
                away_team=fighter2,
                status=status,
                league="ufc",
                sport="mma",
                main_card_start=main_card_start,
            )
        except Exception as e:
            logger.warning(f"Failed to parse UFC event {data.get('id', 'unknown')}: {e}")
            return None

    def _parse_fighter_as_team(self, competitor: dict) -> Team:
        """Convert UFC fighter to Team dataclass for compatibility."""
        athlete = competitor.get("athlete", {})

        # Get headshot URL
        headshots = athlete.get("headshots", {})
        logo_url = None
        if headshots:
            # Prefer full size, fallback to any available
            logo_url = headshots.get("full", {}).get("href")
            if not logo_url:
                for size in ["xlarge", "large", "medium"]:
                    if size in headshots:
                        logo_url = headshots[size].get("href")
                        break

        short_name = athlete.get("shortName", "")

        return Team(
            id=str(athlete.get("id", "")),
            provider=self.name,
            name=athlete.get("displayName", ""),
            short_name=short_name,
            abbreviation=short_name.replace(".", "").replace(" ", ""),
            league="ufc",
            sport="mma",
            logo_url=logo_url,
            color=None,
        )

    def _parse_ufc_status(self, status_data: dict) -> EventStatus:
        """Parse UFC event status."""
        state_map = {
            "pre": "scheduled",
            "in": "live",
            "post": "final",
        }
        state = status_data.get("state", "pre")
        mapped_state = state_map.get(state, "scheduled")

        return EventStatus(
            state=mapped_state,
            detail=status_data.get("description"),
            period=None,
            clock=None,
        )
