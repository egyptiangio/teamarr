"""Template engine module.

This module provides template variable resolution for EPG generation.
Variables are substituted in title/description templates like:
    "{team_name} vs {opponent}" -> "Detroit Lions vs Chicago Bears"

Supports three suffix types:
    {var} - current game context
    {var.next} - next scheduled game
    {var.last} - last completed game
"""

from templates_v2.conditional import (
    Condition,
    ConditionalDescription,
    ConditionEvaluator,
    ConditionType,
    select_description,
)
from templates_v2.context import (
    GameContext,
    HeadToHead,
    PlayerLeaders,
    Streaks,
    TeamConfig,
    TemplateContext,
)
from templates_v2.context_builder import (
    ContextBuilder,
    build_context_for_event,
)
from templates_v2.resolver import TemplateResolver, resolve
from templates_v2.variables import (
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
