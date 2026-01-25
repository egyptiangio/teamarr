"""UFC event parsing for ESPN provider.

Pure parsing layer - converts raw ESPN API responses into Event objects.
No API calls, no date filtering - that's the provider's responsibility.
"""

import logging

from teamarr.core import Event, EventStatus, Team

logger = logging.getLogger(__name__)


class UFCParserMixin:
    """Mixin providing UFC-specific parsing methods.

    Pure parsing only - no API calls or business logic.

    Requires:
        - self.name: Provider name ('espn')
        - self._parse_datetime(date_str): Parse datetime from string
    """

    def _parse_ufc_events(self, data: dict) -> list[Event]:
        """Parse UFC scoreboard response into Event objects.

        Pure parsing - no filtering, no API calls.

        Args:
            data: Raw ESPN scoreboard response

        Returns:
            List of parsed Event objects
        """
        if not data:
            return []

        events = []
        for event_data in data.get("events", []):
            event = self._parse_ufc_event(event_data)
            if event:
                events.append(event)

        return events

    def _parse_ufc_event(self, data: dict) -> Event | None:
        """Parse UFC fight card into Event.

        Maps the main event fighters as home_team/away_team for compatibility.
        Extracts exact segment times from ESPN bout-level data:
        - 3 distinct times: early_prelims, prelims, main_card (PPV events)
        - 2 distinct times: prelims, main_card (Fight Night events)
        - 1 time: main_card only
        """
        try:
            event_id = str(data.get("id", ""))
            if not event_id:
                return None

            competitions = data.get("competitions", [])
            if not competitions:
                return None

            # Group bouts by start time to derive segments
            # ESPN provides exact segment times via bout-level date fields
            bout_times: set[str] = set()
            for comp in competitions:
                if "date" in comp:
                    bout_times.add(comp["date"])

            if not bout_times:
                return None

            # Sort times chronologically to determine segments
            sorted_times = sorted(bout_times)

            # Build segment_times dict based on number of distinct times
            segment_times: dict[str, any] = {}
            if len(sorted_times) == 3:
                # PPV format: Early Prelims, Prelims, Main Card
                segment_times["early_prelims"] = self._parse_datetime(sorted_times[0])
                segment_times["prelims"] = self._parse_datetime(sorted_times[1])
                segment_times["main_card"] = self._parse_datetime(sorted_times[2])
            elif len(sorted_times) == 2:
                # Fight Night format: Prelims, Main Card
                segment_times["prelims"] = self._parse_datetime(sorted_times[0])
                segment_times["main_card"] = self._parse_datetime(sorted_times[1])
            else:
                # Single segment: Main Card only
                segment_times["main_card"] = self._parse_datetime(sorted_times[0])

            # Remove any None values (failed datetime parsing)
            segment_times = {k: v for k, v in segment_times.items() if v is not None}

            # Event start time is earliest segment
            start_time = min(segment_times.values()) if segment_times else None
            if not start_time:
                return None

            # Main card start for backwards compatibility
            main_card_start = segment_times.get("main_card")

            # Find the main event (first bout at main card time)
            main_card_time_str = sorted_times[-1]  # Latest time = main card
            main_event = next(
                (c for c in competitions if c.get("date") == main_card_time_str),
                competitions[0],
            )

            # Extract fighters as "teams"
            competitors = main_event.get("competitors", [])
            if len(competitors) < 2:
                return None

            fighter1 = self._parse_fighter_as_team(competitors[0])
            fighter2 = self._parse_fighter_as_team(competitors[1])

            # Parse status from main event
            status = self._parse_ufc_status(main_event.get("status", {}))

            logger.debug(
                "[ESPN_UFC] Event %s segments: %s",
                event_id,
                {k: v.isoformat() for k, v in segment_times.items()},
            )

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
                sport="mma",  # Lowercase code; display name from sports table
                main_card_start=main_card_start,
                segment_times=segment_times,
            )
        except Exception as e:
            logger.warning("[ESPN_UFC] Failed to parse event %s: %s", data.get('id', 'unknown'), e)
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

        display_name = athlete.get("displayName", "")
        short_name = athlete.get("shortName", "")

        # For fighters, use last name as abbreviation (e.g., "Allen", "Silva")
        # This works better with templates like "{away_team_abbrev} @ {home_team_abbrev}"
        last_name = display_name.split()[-1] if display_name else short_name

        return Team(
            id=str(athlete.get("id", "")),
            provider=self.name,
            name=display_name,
            short_name=short_name,
            abbreviation=last_name,
            league="ufc",
            sport="mma",  # Lowercase code; display name from sports table
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
