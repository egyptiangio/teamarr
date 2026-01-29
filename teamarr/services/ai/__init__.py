"""AI services for stream parsing and pattern learning."""

from teamarr.services.ai.client import OllamaClient
from teamarr.services.ai.parser import AIStreamParser
from teamarr.services.ai.patterns import PatternLearner

__all__ = ["OllamaClient", "AIStreamParser", "PatternLearner"]
