"""Constants for the matching module.

Algorithm tuning constants for stream-to-event matching.
For pattern/alias data, see teamarr/utilities/constants.py
"""

# How far back to search for events when matching streams.
# Allows matching streams for recently-finished events (for stats tracking).
# The lifecycle layer filters out past events after matching.
MATCH_WINDOW_DAYS = 30

# =============================================================================
# CONFIDENCE THRESHOLDS
# Fuzzy match score thresholds that control when matches are accepted.
# =============================================================================

# Accept match without requiring date/time validation
HIGH_CONFIDENCE_THRESHOLD = 85.0

# Accept match only if date/time in stream name validates against event
ACCEPT_WITH_DATE_THRESHOLD = 75.0

# Both-teams matching threshold - lower because min() of two scores is strict
# e.g., "William Jessup" vs "Jessup Warriors" scores ~62%, combined with
# "Sacred Heart" vs "Sacred Heart Pioneers" (~100%) gives min(62, 100) = 62
BOTH_TEAMS_THRESHOLD = 60.0
