"""AI-powered pattern learning for stream parsing."""

import logging
import re
import hashlib
from dataclasses import dataclass, field
from typing import Any

from teamarr.services.ai.client import OllamaClient, OllamaConfig
from teamarr.services.ai.providers import AIProviderClient

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

CRITICAL RULES:
1. Each named group can ONLY appear ONCE in the entire pattern. Python regex does NOT allow duplicate group names.
   WRONG: (?P<team1>\\w+).*(?P<team1>\\w+)  <- ERROR: 'team1' used twice
   RIGHT: (?P<team1>\\w+).*(?P<team2>\\w+)  <- OK: different names

2. Use EXACTLY these group names: team1, team2, league, sport, date, time
   Do NOT use variations like team1_alt, team1_2, first_team, etc.

3. For alternatives, use alternation INSIDE a single group:
   RIGHT: (?P<team1>\\w+|TBD)
   WRONG: (?P<team1>\\w+)|(?P<team1_alt>TBD)

4. Make the pattern flexible with \\s* for whitespace
5. Use non-greedy quantifiers (.*?) where appropriate
6. Only include groups for fields actually present in the examples"""


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


def _sanitize_regex(regex: str) -> str:
    """Clean up AI-generated regex to make it usable.

    Removes common AI formatting artifacts like:
    - r"..." raw string notation
    - Code block markers (```python ... ```)
    - Extra whitespace
    - Python string quotes
    - Prose prefixes like "The Python regex pattern with..."
    - Duplicate named groups (Python doesn't allow redefining group names)
    """
    # Strip whitespace
    regex = regex.strip()

    # Remove code block markers
    if regex.startswith("```"):
        # Remove opening ```python or ```
        lines = regex.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        # Remove closing ```
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        regex = "\n".join(lines).strip()

    # Remove prose prefix - AI sometimes includes "The Python regex pattern with..."
    # Look for where the actual regex starts (typically (?P< or ^ or a literal)
    prose_patterns = [
        "The Python regex pattern with ",
        "The Python regex pattern is ",
        "The regex pattern with ",
        "The regex pattern is ",
        "The pattern is ",
        "The pattern: ",
        "Here is the regex: ",
        "Regex: ",
    ]
    for prefix in prose_patterns:
        if regex.lower().startswith(prefix.lower()):
            regex = regex[len(prefix):].strip()
            break

    # Also try to find where regex actually starts if prose wasn't caught
    # Regex typically starts with: (?P< or ^ or [ or ( or a literal char/escape
    if not regex.startswith(("(?P<", "^", "[", "(", "\\", ".")):
        # Look for first occurrence of (?P< which is our named group pattern
        named_group_start = regex.find("(?P<")
        if named_group_start > 0:
            # Extract just the regex part
            regex = regex[named_group_start:]
            logger.debug("[AI] Stripped prose prefix from regex")

    # Remove Python raw string notation (r"..." or r'...')
    if regex.startswith('r"') and regex.endswith('"'):
        regex = regex[2:-1]
    elif regex.startswith("r'") and regex.endswith("'"):
        regex = regex[2:-1]
    # Remove regular string quotes
    elif regex.startswith('"') and regex.endswith('"'):
        regex = regex[1:-1]
    elif regex.startswith("'") and regex.endswith("'"):
        regex = regex[1:-1]

    # Unescape double-escaped backslashes (common in JSON responses)
    # Only do this if it looks over-escaped
    if "\\\\d" in regex or "\\\\s" in regex or "\\\\w" in regex:
        regex = regex.replace("\\\\", "\\")

    # Fix duplicate named groups - Python doesn't allow redefining group names
    # Find all named groups and rename duplicates
    regex = _fix_duplicate_named_groups(regex)

    return regex


# Patterns that indicate a stream is a placeholder, not an actual event
PLACEHOLDER_PATTERNS = [
    r"NO EVENT",
    r"NO STREAM",
    r"OFF AIR",
    r"OFF-AIR",
    r"COMING SOON",
    r"TBA",
    r"TBD",
    r"PLACEHOLDER",
    r"TEST STREAM",
    r"CHANNEL OFF",
    r"NOT AVAILABLE",
    r"UNAVAILABLE",
]


def _filter_placeholder_streams(streams: list[str]) -> list[str]:
    """Filter out placeholder/non-event streams.

    Removes streams that contain patterns indicating they're not actual events,
    such as "NO EVENT STREAMING", "OFF AIR", etc.
    """
    import re

    # Compile patterns for efficiency
    placeholder_regex = re.compile(
        "|".join(PLACEHOLDER_PATTERNS),
        re.IGNORECASE
    )

    filtered = []
    for stream in streams:
        if not placeholder_regex.search(stream):
            filtered.append(stream)

    return filtered


def _fix_duplicate_named_groups(regex: str) -> str:
    """Rename duplicate named groups to make regex valid.

    Python regex doesn't allow the same group name to be used twice.
    This function finds duplicates and renames them with _alt suffix.

    Note: This is a fallback fix. The AI prompt should be generating
    patterns without duplicates in the first place.
    """
    # Pattern to find named groups: (?P<name>
    named_group_pattern = re.compile(r'\(\?P<([^>]+)>')

    # Track how many times each name has been seen
    name_counts: dict[str, int] = {}
    result_parts: list[str] = []
    last_end = 0
    had_duplicates = False

    for match in named_group_pattern.finditer(regex):
        group_name = match.group(1)
        name_counts[group_name] = name_counts.get(group_name, 0) + 1

        # Add text before this match
        result_parts.append(regex[last_end:match.start()])

        if name_counts[group_name] == 1:
            # First occurrence - keep as is
            result_parts.append(match.group(0))
        else:
            # Duplicate - rename it with _alt or _altN suffix
            had_duplicates = True
            suffix = "_alt" if name_counts[group_name] == 2 else f"_alt{name_counts[group_name]-1}"
            new_name = f"{group_name}{suffix}"
            result_parts.append(f"(?P<{new_name}>")
            logger.warning(
                "[AI] Renamed duplicate group '%s' to '%s' - AI should not generate duplicates",
                group_name, new_name
            )

        last_end = match.end()

    # Add remaining text
    result_parts.append(regex[last_end:])

    if had_duplicates:
        logger.warning("[AI] Pattern had duplicate group names - quality may be reduced")

    return "".join(result_parts)


class PatternLearner:
    """Learn regex patterns from stream examples using AI."""

    def __init__(
        self,
        config: OllamaConfig | None = None,
        client: AIProviderClient | None = None,
    ):
        """Initialize pattern learner.

        Args:
            config: OllamaConfig for backwards compatibility (creates OllamaClient)
            client: Any AIProviderClient - takes precedence over config if provided
        """
        if client is not None:
            self.client = client
        else:
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

        # Sanitize AI-generated regex (remove code blocks, Python string notation, etc.)
        regex = _sanitize_regex(regex)
        logger.debug("[AI] Sanitized regex: %s", regex[:100])

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

        1. Filter out placeholder/non-event streams
        2. Classify streams into format groups
        3. Learn a pattern for each format
        4. Return patterns that cover the streams

        Args:
            streams: All streams in the group
            min_examples: Minimum examples needed per format

        Returns:
            List of learned patterns
        """
        # Filter out placeholder streams that aren't actual events
        filtered_streams = _filter_placeholder_streams(streams)
        if len(filtered_streams) < len(streams):
            logger.info(
                "[AI] Filtered %d placeholder streams, %d remaining",
                len(streams) - len(filtered_streams),
                len(filtered_streams),
            )

        if len(filtered_streams) < min_examples:
            logger.warning("[AI] Not enough streams after filtering (%d)", len(filtered_streams))
            return []

        logger.info("[AI] Analyzing %d streams for patterns...", len(filtered_streams))

        # Step 1: Classify formats (use filtered streams)
        formats = self.classify_formats(filtered_streams)
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
            examples = [filtered_streams[i] for i in indices if i < len(filtered_streams)]

            pattern = self.learn_pattern(examples)
            if pattern:
                pattern.description = fmt.get("description", pattern.description)
                patterns.append(pattern)
                logger.info(
                    "[AI] Learned pattern: %s (confidence: %.0f%%)",
                    pattern.description[:50], pattern.confidence * 100
                )

        # Step 3: Test coverage (against filtered streams only)
        covered = 0
        for stream in filtered_streams:
            if any(p.matches(stream) for p in patterns):
                covered += 1

        logger.info(
            "[AI] Patterns cover %d/%d streams (%.0f%%)",
            covered, len(filtered_streams), (covered / len(filtered_streams)) * 100 if filtered_streams else 0
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
