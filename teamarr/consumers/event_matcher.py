"""Event matching utilities.

Matches queries (team IDs, team names) to events from a list.
Used for event-based EPG where streams need to be linked to specific events.
"""

import unicodedata

from teamarr.core import Event


class EventMatcher:
    """Match queries to sporting events.

    Unlike v1, this matcher works on pre-fetched events.
    The service layer handles fetching; this handles matching.
    """

    def find_by_team_ids(
        self,
        events: list[Event],
        team1_id: str,
        team2_id: str,
    ) -> Event | None:
        """Find event involving both teams by their IDs.

        Args:
            events: List of events to search
            team1_id: First team's ID
            team2_id: Second team's ID

        Returns:
            Matching event or None
        """
        for event in events:
            event_team_ids = {event.home_team.id, event.away_team.id}
            if team1_id in event_team_ids and team2_id in event_team_ids:
                return event
        return None

    def find_by_team_names(
        self,
        events: list[Event],
        team1_name: str,
        team2_name: str,
    ) -> Event | None:
        """Find event by team name matching.

        Uses normalized matching (lowercase, no accents) for flexibility.
        Searches event name, team names, and abbreviations.

        Args:
            events: List of events to search
            team1_name: First team name (from stream, etc.)
            team2_name: Second team name

        Returns:
            Matching event or None
        """
        team1_norm = self._normalize(team1_name)
        team2_norm = self._normalize(team2_name)

        for event in events:
            if self._matches_event(event, team1_norm, team2_norm):
                return event
        return None

    def find_all_by_team_id(
        self,
        events: list[Event],
        team_id: str,
    ) -> list[Event]:
        """Find all events involving a specific team.

        Args:
            events: List of events to search
            team_id: Team ID to find

        Returns:
            List of matching events
        """
        return [
            event for event in events
            if event.home_team.id == team_id or event.away_team.id == team_id
        ]

    def _matches_event(
        self,
        event: Event,
        team1_norm: str,
        team2_norm: str,
    ) -> bool:
        """Check if event matches both team names."""
        # Build searchable text from event
        searchable = self._build_searchable(event)

        # Both teams must appear somewhere in searchable text
        return team1_norm in searchable and team2_norm in searchable

    def _build_searchable(self, event: Event) -> str:
        """Build normalized searchable string from event."""
        parts = [
            event.name,
            event.short_name,
            event.home_team.name,
            event.home_team.short_name,
            event.home_team.abbreviation,
            event.away_team.name,
            event.away_team.short_name,
            event.away_team.abbreviation,
        ]
        combined = " ".join(p for p in parts if p)
        return self._normalize(combined)

    def _normalize(self, text: str) -> str:
        """Normalize text for matching.

        - Lowercase
        - Strip whitespace
        - Remove accents (é → e)
        - Common abbreviations
        """
        if not text:
            return ""

        text = text.lower().strip()

        # Remove accents
        text = unicodedata.normalize("NFD", text)
        text = "".join(c for c in text if unicodedata.category(c) != "Mn")

        # Common abbreviations
        text = text.replace("st.", "saint").replace("st ", "saint ")

        return text
