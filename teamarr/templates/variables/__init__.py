"""Template variables module.

Importing this module registers all template variables via decorators.
Each variable file defines extractors decorated with @register_variable.
"""

from teamarr.templates.variables import (  # noqa: F401 - side effect imports
    broadcast,
    combat,
    conference,
    generated_preview,
    home_away,
    identity,
    motorsports,
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
    summary,
    tennis,
    venue,
)
from teamarr.templates.variables import datetime as datetime_vars  # noqa: F401
from teamarr.templates.variables.registry import (
    Category,
    SuffixRules,
    TemplateScope,
    VariableDefinition,
    VariableRegistry,
    get_registry,
    register_variable,
)

__all__ = [
    "Category",
    "SuffixRules",
    "TemplateScope",
    "VariableDefinition",
    "VariableRegistry",
    "get_registry",
    "register_variable",
]
