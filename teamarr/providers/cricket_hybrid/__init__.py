"""Cricket hybrid provider - combines TSDB teams with Cricbuzz schedules.

Used when TSDB API key is not premium tier. Provides:
- Team info and logos from TSDB (via team_cache)
- Event schedules and scores from Cricbuzz

The service layer is unaware of this - it just sees a "cricket_hybrid" provider.
"""

from teamarr.providers.cricket_hybrid.provider import CricketHybridProvider

__all__ = ["CricketHybridProvider"]
