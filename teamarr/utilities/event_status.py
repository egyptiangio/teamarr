"""Event status utilities.

Single source of truth for determining event final status.
"""

from teamarr.core import Event


def is_event_final(event: Event) -> bool:
    """Check if an event is final/completed.

    This is the SINGLE SOURCE OF TRUTH for final status detection.
    Use this function everywhere final status needs to be checked.

    Checks multiple indicators because different providers use different values:
    - ESPN: "final", "post" (soccer uses STATUS_FULL_TIME -> "final")
    - TSDB: "final" (from "ft", "aet", "finished")
    - HockeyTech: "final" (from "Final", "Final OT", "Final SO")
    - Cricbuzz: "final" (from "complete", "finished")

    Args:
        event: Event to check

    Returns:
        True if event is final/completed, False otherwise
    """
    if not event or not event.status:
        return False

    status_state = event.status.state.lower() if event.status.state else ""
    status_detail = event.status.detail.lower() if event.status.detail else ""

    # Check state for common final indicators
    if status_state in ("final", "post", "completed"):
        return True

    # Check detail for "final" (e.g., "Final", "Final OT", "Final - 3OT")
    if "final" in status_detail:
        return True

    return False
