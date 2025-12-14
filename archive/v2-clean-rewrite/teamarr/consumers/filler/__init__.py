"""Filler generation package for team-based EPG.

Generates pregame, postgame, and idle programmes to fill gaps between events.
"""

from .generator import FillerGenerator
from .types import (
    ConditionalFillerTemplate,
    FillerConfig,
    FillerOptions,
    FillerTemplate,
    FillerType,
    OffseasonFillerTemplate,
)

__all__ = [
    "FillerGenerator",
    "FillerConfig",
    "FillerOptions",
    "FillerTemplate",
    "FillerType",
    "ConditionalFillerTemplate",
    "OffseasonFillerTemplate",
]
