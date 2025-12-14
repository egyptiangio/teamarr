"""Template variables module.

Importing this module registers all template variables via decorators.
Each variable file defines extractors decorated with @register_variable.
"""

from templates_v2.variables import (  # noqa: F401 - side effect imports
    broadcast,
    conference,
    h2h,
    home_away,
    identity,
    odds,
    outcome,
    playoffs,
    rankings,
    records,
    scores,
    soccer,
    standings,
    statistics,
    streaks,
    venue,
)
from templates_v2.variables import datetime as datetime_vars  # noqa: F401
from templates_v2.variables.registry import (
    Category,
    SuffixRules,
    VariableDefinition,
    VariableRegistry,
    get_registry,
    register_variable,
)

__all__ = [
    "Category",
    "SuffixRules",
    "VariableDefinition",
    "VariableRegistry",
    "get_registry",
    "register_variable",
]
