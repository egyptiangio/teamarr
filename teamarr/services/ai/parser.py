"""AI-powered stream name parser."""

import logging
from dataclasses import dataclass, field
from typing import Any

from teamarr.services.ai.client import OllamaClient, OllamaConfig
from teamarr.services.ai.providers import AIProviderClient

logger = logging.getLogger(__name__)


@dataclass
class ParsedStream:
    """Result of AI stream parsing."""
    team1: str | None = None
    team2: str | None = None
    league: str | None = None
    sport: str | None = None
    date: str | None = None
    time: str | None = None
    event_type: str | None = None  # "team_vs_team", "event_card", "ppv", etc.
    confidence: float = 0.0
    raw_response: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "ParsedStream":
        """Create from AI response dict."""
        return cls(
            team1=data.get("team1"),
            team2=data.get("team2"),
            league=data.get("league"),
            sport=data.get("sport"),
            date=data.get("date"),
            time=data.get("time"),
            event_type=data.get("event_type"),
            confidence=data.get("confidence", 0.8),
            raw_response=data,
        )


PARSE_PROMPT = """You are a sports stream name parser. Extract structured data from the stream name.

Stream: "{stream}"

Extract and return JSON with these fields (use null if not found or unclear):
{{
  "team1": "First team/fighter name (full name if possible)",
  "team2": "Second team/fighter name (full name if possible)",
  "league": "League code like NHL, NBA, NFL, MLB, UFC, EPL, La Liga, etc.",
  "sport": "Sport type: Hockey, Basketball, Football, Baseball, MMA, Soccer, etc.",
  "date": "Date exactly as shown in stream, or null",
  "time": "Time exactly as shown in stream, or null",
  "event_type": "team_vs_team, event_card, ppv, or other",
  "confidence": 0.0-1.0 how confident you are in this parse
}}

Rules:
- For team sports: extract both team names
- For UFC/Boxing: team1=fighter1, team2=fighter2, or event name if card
- Normalize team abbreviations only if very confident (NYY=New York Yankees)
- Keep league codes standard (NHL not National Hockey League)
- Return exactly the JSON structure above, nothing else"""


BATCH_PARSE_PROMPT = """You are a sports stream name parser. Extract structured data from each stream.

Streams:
{streams}

For EACH stream, extract and return a JSON array with objects containing:
{{
  "stream_index": 0-based index,
  "team1": "First team/fighter name",
  "team2": "Second team/fighter name",
  "league": "League code (NHL, NBA, NFL, MLB, UFC, EPL, etc.)",
  "sport": "Sport type (Hockey, Basketball, Football, Baseball, MMA, Soccer)",
  "date": "Date as shown or null",
  "time": "Time as shown or null",
  "event_type": "team_vs_team, event_card, ppv, or other",
  "confidence": 0.0-1.0
}}

Return a JSON array with one object per stream, in order."""


class AIStreamParser:
    """Parse stream names using AI."""

    def __init__(
        self,
        config: OllamaConfig | None = None,
        client: AIProviderClient | None = None,
    ):
        """Initialize stream parser.

        Args:
            config: OllamaConfig for backwards compatibility (creates OllamaClient)
            client: Any AIProviderClient - takes precedence over config if provided
        """
        if client is not None:
            self.client = client
        else:
            self.client = OllamaClient(config)

    def parse_single(self, stream_name: str) -> ParsedStream | None:
        """Parse a single stream name.

        Args:
            stream_name: Raw stream name to parse

        Returns:
            ParsedStream with extracted data, or None on failure
        """
        prompt = PARSE_PROMPT.format(stream=stream_name)
        result = self.client.generate(prompt, json_format=True)

        if not result or not isinstance(result, dict):
            logger.debug("[AI] Failed to parse stream: %s", stream_name[:60])
            return None

        return ParsedStream.from_dict(result)

    def parse_batch(
        self,
        stream_names: list[str],
        batch_size: int = 10,
    ) -> list[ParsedStream | None]:
        """Parse multiple streams in batches.

        Args:
            stream_names: List of stream names to parse
            batch_size: Number of streams per AI call

        Returns:
            List of ParsedStream (or None for failures), same order as input
        """
        results: list[ParsedStream | None] = []

        for i in range(0, len(stream_names), batch_size):
            batch = stream_names[i:i + batch_size]
            batch_results = self._parse_batch_internal(batch)

            # Ensure we have the right number of results
            while len(batch_results) < len(batch):
                batch_results.append(None)

            results.extend(batch_results[:len(batch)])

        return results

    def _parse_batch_internal(self, streams: list[str]) -> list[ParsedStream | None]:
        """Internal batch parsing."""
        # Format streams with indices
        formatted = "\n".join(
            f"{i}. {stream}" for i, stream in enumerate(streams)
        )

        prompt = BATCH_PARSE_PROMPT.format(streams=formatted)
        result = self.client.generate(prompt, json_format=True)

        if not result:
            return [None] * len(streams)

        # Handle both array and single-object responses
        if isinstance(result, dict):
            # Single result or wrapped array
            if "results" in result:
                result = result["results"]
            else:
                result = [result]

        if not isinstance(result, list):
            logger.warning("[AI] Unexpected batch response type: %s", type(result))
            return [None] * len(streams)

        # Map results by index
        parsed: list[ParsedStream | None] = [None] * len(streams)
        for item in result:
            if isinstance(item, dict):
                idx = item.get("stream_index", 0)
                if 0 <= idx < len(streams):
                    parsed[idx] = ParsedStream.from_dict(item)

        return parsed

    def is_available(self) -> bool:
        """Check if AI service is available."""
        return self.client.is_available()

    def close(self):
        """Close the client."""
        self.client.close()
