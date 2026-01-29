"""AI-powered stream classification integration.

This module integrates AI pattern learning with the existing classifier,
providing a hybrid approach that:
1. Uses learned patterns (AI-generated regex) first for speed
2. Falls back to AI parsing for unmatched streams
3. Falls back to builtin regex if AI unavailable
"""

import logging
from dataclasses import dataclass
from datetime import date, time
from sqlite3 import Connection

from teamarr.consumers.matching.classifier import (
    ClassifiedStream,
    CustomRegexConfig,
    StreamCategory,
    classify_stream as builtin_classify_stream,
    detect_league_hint,
    detect_sport_hint,
)
from teamarr.consumers.matching.normalizer import normalize_stream
from teamarr.database.ai_patterns import (
    get_all_patterns,
    get_patterns_for_group,
    save_pattern,
    update_pattern_stats,
)
from teamarr.database.settings.types import AISettings
from teamarr.services.ai.client import OllamaConfig
from teamarr.services.ai.parser import AIStreamParser, ParsedStream
from teamarr.services.ai.patterns import LearnedPattern, PatternLearner

logger = logging.getLogger(__name__)


@dataclass
class AIClassifierConfig:
    """Configuration for AI-powered classification."""

    settings: AISettings
    db_conn: Connection | None = None
    group_id: int | None = None  # Optional: limit to patterns for this group


class AIClassifier:
    """AI-powered stream classifier with pattern learning."""

    def __init__(self, config: AIClassifierConfig):
        self.config = config
        self.settings = config.settings
        self._parser: AIStreamParser | None = None
        self._learner: PatternLearner | None = None
        self._patterns: list[dict] = []
        self._patterns_loaded = False

    @property
    def parser(self) -> AIStreamParser:
        """Lazy-load the AI parser."""
        if self._parser is None:
            ollama_config = OllamaConfig(
                base_url=self.settings.ollama_url,
                model=self.settings.model,
                timeout=float(self.settings.timeout),
            )
            self._parser = AIStreamParser(ollama_config)
        return self._parser

    @property
    def learner(self) -> PatternLearner:
        """Lazy-load the pattern learner."""
        if self._learner is None:
            ollama_config = OllamaConfig(
                base_url=self.settings.ollama_url,
                model=self.settings.model,
                timeout=float(self.settings.timeout),
            )
            self._learner = PatternLearner(ollama_config)
        return self._learner

    def _load_patterns(self) -> None:
        """Load learned patterns from database."""
        if self._patterns_loaded or not self.config.db_conn:
            return

        try:
            if self.config.group_id is not None:
                self._patterns = get_patterns_for_group(
                    self.config.db_conn,
                    self.config.group_id,
                )
            else:
                self._patterns = get_all_patterns(self.config.db_conn)

            self._patterns_loaded = True
            logger.debug("[AI] Loaded %d patterns from database", len(self._patterns))
        except Exception as e:
            logger.warning("[AI] Failed to load patterns: %s", e)
            self._patterns = []

    def _try_learned_patterns(
        self,
        stream_name: str,
    ) -> tuple[dict[str, str] | None, str | None]:
        """Try to match stream against learned patterns.

        Args:
            stream_name: Stream name to match

        Returns:
            Tuple of (extracted fields dict, pattern_id) or (None, None)
        """
        self._load_patterns()

        for pattern_data in self._patterns:
            pattern = LearnedPattern(
                pattern_id=pattern_data["pattern_id"],
                regex=pattern_data["regex"],
                description=pattern_data.get("description", ""),
                example_streams=pattern_data.get("example_streams", []),
                field_map=pattern_data.get("field_map", {}),
                confidence=pattern_data.get("confidence", 0.5),
                match_count=pattern_data.get("match_count", 0),
                fail_count=pattern_data.get("fail_count", 0),
            )

            result = pattern.matches(stream_name)
            if result:
                logger.debug(
                    "[AI] Pattern '%s' matched stream: %s",
                    pattern.pattern_id[:8],
                    stream_name[:50],
                )
                return result, pattern.pattern_id

        return None, None

    def _convert_parsed_to_classified(
        self,
        parsed: ParsedStream,
        stream_name: str,
        source: str = "ai",
    ) -> ClassifiedStream:
        """Convert AIStreamParser result to ClassifiedStream.

        Args:
            parsed: Parsed stream from AI
            stream_name: Original stream name
            source: Classification source (for logging)

        Returns:
            ClassifiedStream object
        """
        normalized = normalize_stream(stream_name)

        # Override extracted date/time from AI if present
        if parsed.date:
            try:
                from datetime import datetime
                parsed_date = datetime.strptime(parsed.date, "%Y-%m-%d").date()
                normalized.extracted_date = parsed_date
            except (ValueError, TypeError):
                pass

        if parsed.time:
            try:
                from datetime import datetime
                parsed_time = datetime.strptime(parsed.time, "%H:%M").time()
                normalized.extracted_time = parsed_time
            except (ValueError, TypeError):
                pass

        # Determine category
        if parsed.event_type == "event_card":
            category = StreamCategory.EVENT_CARD
        elif parsed.team1 and parsed.team2:
            category = StreamCategory.TEAM_VS_TEAM
        elif parsed.team1:
            # Single team - still usable for matching
            category = StreamCategory.TEAM_VS_TEAM
        else:
            category = StreamCategory.PLACEHOLDER

        # Use AI-extracted hints, fall back to builtin detection
        league_hint = parsed.league or detect_league_hint(normalized.normalized or "")
        sport_hint = parsed.sport or detect_sport_hint(normalized.normalized or "")

        result = ClassifiedStream(
            category=category,
            normalized=normalized,
            team1=parsed.team1,
            team2=parsed.team2,
            league_hint=league_hint,
            sport_hint=sport_hint,
            custom_regex_used=(source == "ai_pattern"),
            ai_classified=True,  # Mark as AI-classified
        )

        logger.debug(
            "[AI:%s] '%s' -> %s (teams=%s/%s, conf=%.0f%%)",
            source,
            stream_name[:40],
            category.value,
            parsed.team1,
            parsed.team2,
            parsed.confidence * 100,
        )

        return result

    def _convert_pattern_match_to_classified(
        self,
        fields: dict[str, str],
        stream_name: str,
        pattern_id: str,
    ) -> ClassifiedStream:
        """Convert learned pattern match to ClassifiedStream.

        Args:
            fields: Extracted fields from pattern match
            stream_name: Original stream name
            pattern_id: ID of matched pattern

        Returns:
            ClassifiedStream object
        """
        normalized = normalize_stream(stream_name)

        # Extract standard fields
        team1 = fields.get("team1")
        team2 = fields.get("team2")
        league = fields.get("league")
        sport = fields.get("sport")
        date_str = fields.get("date")
        time_str = fields.get("time")

        # Parse date if present
        if date_str:
            try:
                from datetime import datetime
                for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%b %d", "%d %b"]:
                    try:
                        parsed_date = datetime.strptime(date_str.strip(), fmt).date()
                        normalized.extracted_date = parsed_date
                        break
                    except ValueError:
                        continue
            except Exception:
                pass

        # Parse time if present
        if time_str:
            try:
                from datetime import datetime
                for fmt in ["%H:%M", "%I:%M%p", "%I:%M %p", "%I%p"]:
                    try:
                        time_normalized = time_str.strip().replace(" ", "")
                        parsed_time = datetime.strptime(time_normalized, fmt).time()
                        normalized.extracted_time = parsed_time
                        break
                    except ValueError:
                        continue
            except Exception:
                pass

        # Determine category
        if team1 and team2:
            category = StreamCategory.TEAM_VS_TEAM
        elif team1:
            category = StreamCategory.TEAM_VS_TEAM
        else:
            category = StreamCategory.PLACEHOLDER

        # Use pattern-extracted hints, fall back to builtin detection
        league_hint = league or detect_league_hint(normalized.normalized or "")
        sport_hint = sport or detect_sport_hint(normalized.normalized or "")

        result = ClassifiedStream(
            category=category,
            normalized=normalized,
            team1=team1,
            team2=team2,
            league_hint=league_hint,
            sport_hint=sport_hint,
            custom_regex_used=True,  # Mark as custom regex (AI-learned pattern)
            ai_classified=True,  # Mark as AI-classified
        )

        # Update pattern stats
        if self.config.db_conn:
            try:
                update_pattern_stats(self.config.db_conn, pattern_id, matched=True)
            except Exception as e:
                logger.debug("[AI] Failed to update pattern stats: %s", e)

        logger.debug(
            "[AI:pattern] '%s' -> %s (teams=%s/%s)",
            stream_name[:40],
            category.value,
            team1,
            team2,
        )

        return result

    def classify_stream(
        self,
        stream_name: str,
        league_event_type: str | None = None,
        custom_regex: CustomRegexConfig | None = None,
    ) -> ClassifiedStream:
        """Classify a stream using AI with fallbacks.

        Classification order:
        1. Try learned patterns (AI-generated regex) - fastest
        2. Try AI parsing if patterns fail and AI enabled
        3. Fall back to builtin classifier

        Args:
            stream_name: Raw stream name to classify
            league_event_type: Optional event_type from leagues table
            custom_regex: Optional custom regex (takes priority over AI)

        Returns:
            ClassifiedStream with category and extracted info
        """
        # If custom regex is configured and enabled, use builtin classifier
        # Custom regex takes priority over AI
        if custom_regex and (
            custom_regex.teams_enabled or
            custom_regex.date_enabled or
            custom_regex.time_enabled
        ):
            return builtin_classify_stream(stream_name, league_event_type, custom_regex)

        # Step 1: Try learned patterns first (fast regex lookup)
        if self.settings.learn_patterns and self.config.db_conn:
            fields, pattern_id = self._try_learned_patterns(stream_name)
            if fields and pattern_id:
                return self._convert_pattern_match_to_classified(
                    fields, stream_name, pattern_id
                )

        # Step 2: Try AI parsing if enabled
        if self.settings.use_for_parsing:
            try:
                if self.parser.is_available():
                    parsed = self.parser.parse_single(stream_name)
                    if parsed and parsed.confidence >= 0.5:
                        return self._convert_parsed_to_classified(
                            parsed, stream_name, "ai"
                        )
            except Exception as e:
                logger.warning("[AI] Parsing failed for '%s': %s", stream_name[:50], e)

        # Step 3: Fall back to builtin classifier
        if self.settings.fallback_to_regex:
            return builtin_classify_stream(stream_name, league_event_type, custom_regex)

        # No fallback - return placeholder
        normalized = normalize_stream(stream_name)
        return ClassifiedStream(
            category=StreamCategory.PLACEHOLDER,
            normalized=normalized,
        )

    def classify_streams_batch(
        self,
        stream_names: list[str],
        league_event_type: str | None = None,
        custom_regex: CustomRegexConfig | None = None,
    ) -> list[ClassifiedStream]:
        """Classify multiple streams with batched AI calls.

        For efficiency:
        1. Try learned patterns for all streams first
        2. Batch remaining streams for AI parsing
        3. Fall back to builtin for any that fail

        Args:
            stream_names: List of stream names
            league_event_type: Optional event_type from leagues table
            custom_regex: Optional custom regex configuration

        Returns:
            List of ClassifiedStream objects
        """
        results: list[ClassifiedStream | None] = [None] * len(stream_names)
        pending_indices: list[int] = []

        # Step 1: Try learned patterns for all streams
        if self.settings.learn_patterns and self.config.db_conn:
            for i, name in enumerate(stream_names):
                fields, pattern_id = self._try_learned_patterns(name)
                if fields and pattern_id:
                    results[i] = self._convert_pattern_match_to_classified(
                        fields, name, pattern_id
                    )
                else:
                    pending_indices.append(i)
        else:
            pending_indices = list(range(len(stream_names)))

        # Step 2: Batch AI parsing for pending streams
        if pending_indices and self.settings.use_for_parsing:
            pending_names = [stream_names[i] for i in pending_indices]

            try:
                if self.parser.is_available():
                    batch_size = self.settings.batch_size
                    for batch_start in range(0, len(pending_names), batch_size):
                        batch_end = min(batch_start + batch_size, len(pending_names))
                        batch_names = pending_names[batch_start:batch_end]
                        batch_indices = pending_indices[batch_start:batch_end]

                        parsed_list = self.parser.parse_batch(batch_names)

                        for j, (idx, name) in enumerate(zip(batch_indices, batch_names)):
                            if j < len(parsed_list):
                                parsed = parsed_list[j]
                                if parsed and parsed.confidence >= 0.5:
                                    results[idx] = self._convert_parsed_to_classified(
                                        parsed, name, "ai_batch"
                                    )
            except Exception as e:
                logger.warning("[AI] Batch parsing failed: %s", e)

        # Step 3: Fall back to builtin for any remaining
        if self.settings.fallback_to_regex:
            for i, result in enumerate(results):
                if result is None:
                    results[i] = builtin_classify_stream(
                        stream_names[i],
                        league_event_type,
                        custom_regex,
                    )
        else:
            # Fill with placeholders
            for i, result in enumerate(results):
                if result is None:
                    normalized = normalize_stream(stream_names[i])
                    results[i] = ClassifiedStream(
                        category=StreamCategory.PLACEHOLDER,
                        normalized=normalized,
                    )

        return results  # type: ignore

    def learn_patterns_for_group(
        self,
        streams: list[str],
        group_id: int,
    ) -> list[LearnedPattern]:
        """Learn patterns from example streams and save to database.

        Args:
            streams: List of stream names to learn from
            group_id: Event group ID to associate patterns with

        Returns:
            List of learned patterns
        """
        if not self.settings.learn_patterns:
            logger.warning("[AI] Pattern learning is disabled in settings")
            return []

        if not streams:
            logger.warning("[AI] No streams provided for pattern learning")
            return []

        logger.info("[AI] Learning patterns for group %d from %d streams", group_id, len(streams))

        try:
            if not self.learner.is_available():
                logger.warning("[AI] Ollama is not available for pattern learning")
                return []

            patterns = self.learner.learn_patterns_for_group(streams)

            # Save patterns to database
            if self.config.db_conn and patterns:
                for pattern in patterns:
                    save_pattern(
                        self.config.db_conn,
                        pattern_id=pattern.pattern_id,
                        regex=pattern.regex,
                        description=pattern.description,
                        example_streams=pattern.example_streams,
                        field_map=pattern.field_map,
                        confidence=pattern.confidence,
                        group_id=group_id,
                    )
                logger.info(
                    "[AI] Saved %d learned patterns for group %d",
                    len(patterns), group_id
                )

            # Reload patterns cache
            self._patterns_loaded = False
            self._load_patterns()

            return patterns

        except Exception as e:
            logger.error("[AI] Pattern learning failed: %s", e)
            return []

    def is_available(self) -> bool:
        """Check if AI services are available."""
        if not self.settings.enabled:
            return False

        try:
            return self.parser.is_available()
        except Exception:
            return False

    def close(self):
        """Clean up resources."""
        if self._parser:
            self._parser.close()
        if self._learner:
            self._learner.close()


def classify_stream_with_ai(
    stream_name: str,
    ai_settings: AISettings,
    db_conn: Connection | None = None,
    group_id: int | None = None,
    league_event_type: str | None = None,
    custom_regex: CustomRegexConfig | None = None,
) -> ClassifiedStream:
    """Convenience function for single stream classification with AI.

    Args:
        stream_name: Stream name to classify
        ai_settings: AI settings from database
        db_conn: Database connection for pattern lookup
        group_id: Optional event group ID
        league_event_type: Optional event type
        custom_regex: Optional custom regex config

    Returns:
        ClassifiedStream object
    """
    config = AIClassifierConfig(
        settings=ai_settings,
        db_conn=db_conn,
        group_id=group_id,
    )
    classifier = AIClassifier(config)
    try:
        return classifier.classify_stream(stream_name, league_event_type, custom_regex)
    finally:
        classifier.close()
