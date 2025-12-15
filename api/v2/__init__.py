"""V2 API - Pure dataclass EPG generation.

This module exposes V2 consumers via REST endpoints.
Can run in parallel with V1 for gradual migration.
"""

from api.v2.routes import bp as v2_bp

__all__ = ["v2_bp"]
