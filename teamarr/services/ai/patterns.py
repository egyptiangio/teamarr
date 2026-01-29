"""AI-powered pattern learning for stream parsing."""

import logging
import re
import hashlib
from dataclasses import dataclass, field
from typing import Any

from teamarr.services.ai.client import OllamaClient, OllamaConfig

logger = logging.getLogger(__name__)


@dataclass
class LearnedPattern:
    """A regex pattern learned from stream examples."""
    pattern_id: str  # Hash of the pattern
    regex: str  # The regex pattern with named groups
    description: str  # Human-readable description
    example_streams: list[str]  # Examples used to learn this
    field_map: dict[str, str]  # Maps regex groups to fields (team1, team2, etc.)
    confidence: float = 0.0
    match_count: int = 0  # How many streams this has matched
    fail_count: int = 0  # How many streams this failed on

    def matches(self, stream: str) -> dict[str, str] | None:
        """Try to match stream against this pattern.

        Returns:
            Dict of extracted fields if match, None otherwise
        """
        try:
            match = re.search(self.regex, stream, re.IGNORECASE)
            if match:
                return match.groupdict()
        except re.error:
            pass
        return None


LEARN_PATTERN_PROMPT = """You are a regex pattern expert. Analyze these stream names and create a regex pattern that can extract sports event information.

Example streams from the same source:
{examples}

Create a Python regex pattern with NAMED GROUPS that extracts:
- team1: First team or fighter
- team2: Second team or fighter
- league: League code (NHL, NBA, etc.)
- sport: Sport type
- date: Date portion
- time: Time portion

Return JSON:
{{
  "regex": "The Python regex pattern with (?P<name>...) groups",
  "description": "Human-readable description of the pattern format",
  "field_map": {{"group_name": "field_name"}} for any non-standard group names,
  "confidence": 0.0-1.0 how well this pattern captures the format
}}

Rules:
- Use (?P<team1>...), (?P<team2>...), etc. for named groups
- Make the pattern flexible enough to handle variations
- Use \\s* for flexible whitespace
- Use non-greedy quantifiers where appropriate
- Escape special regex characters
- Only include groups for fields actually present in the examples"""


CLASSIFY_FORMAT_PROMPT = """Analyze these stream names and identify distinct FORMAT PATTERNS. Group streams that follow the same naming convention.

Streams:
{streams}

Return JSON:
{{
  "formats": [
    {{
      "format_id": "unique identifier for this format",
      "description": "Description of the naming pattern",
      "example_indices": [0, 3, 7],  // Which streams follow this format
      "characteristics": ["has date", "has time", "uses vs separator", etc.]
    }}
  ]
}}

Focus on STRUCTURE, not content. Two streams have the same format if:
- Same separator style (vs, @, -, |)
- Same field order (league first vs team first)
- Same date/time format
- Same prefix/suffix patterns"""


class PatternLearner:
    """Learn regex patterns from stream examples using AI."""

    def __init__(self, config: OllamaConfig | None = None):
        self.client = OllamaClient(config)
        self._pattern_cache: dict[str, LearnedPattern] = {}

    def learn_pattern(self, example_streams: list[str]) -> LearnedPattern | None:
        """Learn a regex pattern from example streams.

        Args:
            example_streams: 3-10 example streams with similar format

        Returns:
            LearnedPattern if successful, None otherwise
        """
        if len(example_streams) < 2:
            logger.warning("[AI] Need at least 2 examples to learn pattern")
            return None

        # Format examples
        formatted = "\n".join(f"{i+1}. {s}" for i, s in enumerate(example_streams[:10]))
        prompt = LEARN_PATTERN_PROMPT.format(examples=formatted)

        result = self.client.generate(prompt, json_format=True)
        if not result or not isinstance(result, dict):
            logger.warning("[AI] Failed to learn pattern from examples")
            return None

        regex = result.get("regex")
        if not regex:
            logger.warning("[AI] No regex in response")
            return None

        # Validate the regex
        try:
            re.compile(regex)
        except re.error as e:
            logger.warning("[AI] Invalid regex generated: %s", e)
            return None

        # Generate pattern ID from the regex
        pattern_id = hashlib.sha256(regex.encode()).hexdigest()[:12]

        pattern = LearnedPattern(
            pattern_id=pattern_id,
            regex=regex,
            description=result.get("description", ""),
            example_streams=example_streams[:5],
            field_map=result.get("field_map", {}),
            confidence=result.get("confidence", 0.5),
        )

        # Test pattern against examples
        match_count = sum(1 for s in example_streams if pattern.matches(s))
        if match_count < len(example_streams) * 0.5:
            logger.warning(
                "[AI] Pattern only matched %d/%d examples",
                match_count, len(example_streams)
            )
            # Still return it, but with lower confidence
            pattern.confidence *= (match_count / len(example_streams))

        self._pattern_cache[pattern_id] = pattern
        return pattern

    def classify_formats(
        self,
        streams: list[str],
        sample_size: int = 50,
    ) -> list[dict[str, Any]]:
        """Identify distinct format patterns in a list of streams.

        Args:
            streams: List of stream names to analyze
            sample_size: Max streams to send to AI

        Returns:
            List of format descriptions with example indices
        """
        # Sample streams if too many
        if len(streams) > sample_size:
            # Take evenly distributed samples
            step = len(streams) // sample_size
            sampled = [streams[i] for i in range(0, len(streams), step)][:sample_size]
            index_map = {i: i * step for i in range(len(sampled))}
        else:
            sampled = streams
            index_map = {i: i for i in range(len(sampled))}

        formatted = "\n".join(f"{i}. {s}" for i, s in enumerate(sampled))
        prompt = CLASSIFY_FORMAT_PROMPT.format(streams=formatted)

        result = self.client.generate(prompt, json_format=True)
        if not result or not isinstance(result, dict):
            return []

        formats = result.get("formats", [])

        # Map indices back to original
        for fmt in formats:
            if "example_indices" in fmt:
                fmt["example_indices"] = [
                    index_map.get(i, i) for i in fmt["example_indices"]
                ]

        return formats

    def learn_patterns_for_group(
        self,
        streams: list[str],
        min_examples: int = 3,
    ) -> list[LearnedPattern]:
        """Learn all patterns needed for a group of streams.

        1. Classify streams into format groups
        2. Learn a pattern for each format
        3. Return patterns that cover the streams

        Args:
            streams: All streams in the group
            min_examples: Minimum examples needed per format

        Returns:
            List of learned patterns
        """
        logger.info("[AI] Analyzing %d streams for patterns...", len(streams))

        # Step 1: Classify formats
        formats = self.classify_formats(streams)
        logger.info("[AI] Identified %d distinct formats", len(formats))

        patterns = []

        # Step 2: Learn pattern for each format
        for fmt in formats:
            indices = fmt.get("example_indices", [])
            if len(indices) < min_examples:
                logger.debug(
                    "[AI] Skipping format '%s' - only %d examples",
                    fmt.get("description", "unknown"), len(indices)
                )
                continue

            # Get example streams for this format
            examples = [streams[i] for i in indices if i < len(streams)]

            pattern = self.learn_pattern(examples)
            if pattern:
                pattern.description = fmt.get("description", pattern.description)
                patterns.append(pattern)
                logger.info(
                    "[AI] Learned pattern: %s (confidence: %.0f%%)",
                    pattern.description[:50], pattern.confidence * 100
                )

        # Step 3: Test coverage
        covered = 0
        for stream in streams:
            if any(p.matches(stream) for p in patterns):
                covered += 1

        logger.info(
            "[AI] Patterns cover %d/%d streams (%.0f%%)",
            covered, len(streams), (covered / len(streams)) * 100 if streams else 0
        )

        return patterns

    def get_cached_pattern(self, pattern_id: str) -> LearnedPattern | None:
        """Get a pattern from cache by ID."""
        return self._pattern_cache.get(pattern_id)

    def is_available(self) -> bool:
        """Check if AI service is available."""
        return self.client.is_available()

    def close(self):
        """Close the client."""
        self.client.close()
