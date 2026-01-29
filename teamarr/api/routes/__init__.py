"""API route modules."""

import re


def natural_sort_key(name: str) -> list:
    """Generate sort key for natural/human sorting.

    Handles embedded numbers correctly:
    - "ESPN+ 2" comes before "ESPN+ 10"
    - "Sportsnet+ 01" comes before "Sportsnet+ 02"
    """
    parts = []
    for part in re.split(r"(\d+)", name.lower()):
        if part.isdigit():
            parts.append(int(part))
        else:
            parts.append(part)
    return parts
