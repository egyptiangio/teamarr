"""Template engine module.

This module provides template variable resolution for EPG generation.
Variables are substituted in title/description templates like:
    "{team_name} vs {opponent}" -> "Detroit Lions vs Chicago Bears"

Supports three suffix types:
    {var} - current game context
    {var.next} - next scheduled game
    {var.last} - last completed game
"""

from teamarr.templates.conditional import (
    Condition,
    ConditionalDescription,
    ConditionEvaluator,
    ConditionType,
    select_description,
)
from teamarr.templates.context import (
    GameContext,
    HeadToHead,
    PlayerLeaders,
    Streaks,
    TeamConfig,
    TemplateContext,
)
from teamarr.templates.context_builder import (
    ContextBuilder,
    build_context_for_event,
)
from teamarr.templates.resolver import TemplateResolver, resolve
from teamarr.templates.variables import (
    Category,
    SuffixRules,
    VariableRegistry,
    get_registry,
)

__all__ = [
    # Conditional system
    "Condition",
    "ConditionalDescription",
    "ConditionEvaluator",
    "ConditionType",
    "select_description",
    # Context builder
    "ContextBuilder",
    "build_context_for_event",
    # Context types
    "GameContext",
    "HeadToHead",
    "PlayerLeaders",
    "Streaks",
    "TeamConfig",
    "TemplateContext",
    # Resolver
    "TemplateResolver",
    "resolve",
    # Registry
    "Category",
    "SuffixRules",
    "VariableRegistry",
    "get_registry",
]
