"""Parse ESPN summary facts into typed, public ``Event`` fields.

Only source-grounded pregame facts are read. Pickcenter, odds and predictor
payloads are deliberately outside every code path in this module.
"""

import re
from typing import Any

from teamarr.core import Event

_LEADER_FIELDS = {
    "baseball": {
        "homeRuns": ("home_runs_leader", "home runs"),
        "avg": ("batting_average_leader", "batting average"),
        "RBIs": ("rbi_leader", "RBI"),
    },
    "football": {
        "passingYards": ("passing_leader", "passing yards"),
        # Some ESPN payloads provide a fully formatted stat line (for example
        # "19/31, 181 YDS") under the *Leader names.  Do not append another
        # label to those already self-describing values.
        "passingLeader": ("passing_leader", ""),
        "rushingYards": ("rushing_leader", "rushing yards"),
        "rushingLeader": ("rushing_leader", ""),
        "receivingYards": ("receiving_leader", "receiving yards"),
        "receivingLeader": ("receiving_leader", ""),
    },
    "basketball": {
        "pointsPerGame": ("points_leader", "points per game"),
        "reboundsPerGame": ("rebounds_leader", "rebounds per game"),
        "assistsPerGame": ("assists_leader", "assists per game"),
    },
}

_TEAM_STAT_FIELDS = {
    "football": {
        "yardsPerGame": "total_yards_per_game",
        "rushingYardsPerGame": "rushing_yards_per_game",
    },
    "basketball": {
        "avgPoints": "team_ppg",
        "avgPointsAgainst": "points_allowed_per_game",
    },
}


def _team_side(event: Event, team_id: str) -> str | None:
    if team_id == str(event.home_team.id):
        return "home"
    if team_id == str(event.away_team.id):
        return "away"
    return None


def _display_value(item: dict) -> str:
    value = item.get("displayValue")
    return "" if value in (None, "") else str(value)


def _stat_map(items: list[dict] | None) -> dict[str, str]:
    return {
        str(item.get("name")): _display_value(item)
        for item in items or []
        if item.get("name") and _display_value(item)
    }


def _flatten_team_stats(items: list[dict] | None) -> dict[str, str]:
    """Flatten both ESPN's grouped baseball and flat team-stat shapes."""
    stats: dict[str, str] = {}
    for item in items or []:
        nested = item.get("stats")
        if nested is not None:
            stats.update(_stat_map(nested))
        elif item.get("name") and _display_value(item):
            stats[str(item["name"])] = _display_value(item)
    return stats


def _format_leader(name: str, value: str, label: str) -> str:
    if not name or not value:
        return ""
    # Some football categories contain a complete ESPN stat line rather than
    # bare yardage. Keep that provider value intact for the public variable;
    # the generated-prose formatter expands its abbreviations for readability.
    if label.endswith("yards") and re.search(r"\b(?:YDS?|yards?)\b", value, re.IGNORECASE):
        label = ""
    detail = f"{value} {label}".strip()
    return f"{name} — {detail}"


def _format_probable(probable: dict) -> str:
    athlete = probable.get("athlete") or {}
    name = athlete.get("displayName") or ""
    if not name:
        return ""
    stats = _stat_map(
        ((probable.get("statistics") or {}).get("splits") or {}).get("categories")
    )
    details = []
    if stats.get("wins") and stats.get("losses"):
        details.append(f"{stats['wins']}-{stats['losses']}")
    if stats.get("ERA"):
        details.append(f"{stats['ERA']} ERA")
    return f"{name} ({', '.join(details)})" if details else name


def _parse_week(raw: Any) -> int | None:
    if isinstance(raw, dict):
        raw = raw.get("number") or raw.get("value")
    try:
        return int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _expand_team_abbreviations(summary: str, competition: dict, event: Event) -> str:
    """Replace ESPN team codes in matchup prose with full display names."""
    replacements: dict[str, str] = {}
    for competitor in competition.get("competitors") or []:
        team = competitor.get("team") or {}
        abbreviation = str(team.get("abbreviation") or "").strip()
        display_name = str(team.get("displayName") or "").strip()
        if abbreviation and display_name:
            replacements[abbreviation.casefold()] = display_name
    for team in (event.home_team, event.away_team):
        if team.abbreviation and team.name:
            replacements.setdefault(team.abbreviation.casefold(), team.name)
    if not replacements:
        return summary
    aliases = "|".join(
        re.escape(abbreviation)
        for abbreviation in sorted(replacements, key=len, reverse=True)
    )
    return re.sub(
        rf"(?<![\w])(?:{aliases})(?![\w])",
        lambda match: replacements[match.group(0).casefold()],
        summary,
        flags=re.IGNORECASE,
    )


def _leader_blocks(data: dict, competition: dict) -> list[dict]:
    """Return summary leaders, falling back to competitor-scoped leaders."""
    if data.get("leaders"):
        return data["leaders"]
    blocks = []
    for competitor in competition.get("competitors") or []:
        if competitor.get("leaders"):
            blocks.append(
                {
                    "team": competitor.get("team") or {},
                    "leaders": competitor["leaders"],
                }
            )
    return blocks


def apply_generated_preview_fields(data: dict[str, Any], event: Event) -> None:
    """Populate typed preview fields on ``event`` from an ESPN summary."""
    competition = ((data.get("header") or {}).get("competitions") or [{}])[0]
    header = data.get("header") or {}
    event.week = _parse_week(header.get("week") or competition.get("week"))

    for competitor in competition.get("competitors") or []:
        side = competitor.get("homeAway")
        if side not in {"home", "away"}:
            continue
        record = next(
            (
                item.get("summary") or item.get("displayValue")
                for item in competitor.get("record") or competitor.get("records") or []
                if item.get("type") in {"total", "overall"}
            ),
            "",
        )
        if record:
            setattr(event, f"{side}_team_record", str(record))
        probable = next(
            (item for item in competitor.get("probables") or [] if item.get("athlete")),
            None,
        )
        if event.sport == "baseball" and probable:
            setattr(event, f"{side}_probable_starter", _format_probable(probable))

    leader_contract = _LEADER_FIELDS.get(event.sport, {})
    for block in _leader_blocks(data, competition):
        side = _team_side(event, str((block.get("team") or {}).get("id") or ""))
        if not side:
            continue
        for category in block.get("leaders") or []:
            mapping = leader_contract.get(category.get("name"))
            leader = (category.get("leaders") or [{}])[0]
            athlete = leader.get("athlete") or {}
            if not mapping:
                continue
            suffix, label = mapping
            rendered = _format_leader(
                athlete.get("displayName") or "",
                _display_value(leader),
                label,
            )
            if rendered:
                setattr(event, f"{side}_{suffix}", rendered)

    stat_contract = _TEAM_STAT_FIELDS.get(event.sport, {})
    for block in (data.get("boxscore") or {}).get("teams") or []:
        side = _team_side(event, str((block.get("team") or {}).get("id") or ""))
        if not side:
            continue
        stats = _flatten_team_stats(block.get("statistics"))
        for espn_name, suffix in stat_contract.items():
            if stats.get(espn_name):
                setattr(event, f"{side}_{suffix}", stats[espn_name])

    series_options = [
        item for item in data.get("seasonseries") or [] if item.get("type") != "preseason"
    ]
    series = next((item for item in series_options if item.get("type") == "current"), None)
    series = series or next(
        (item for item in series_options if item.get("type") == "season"), None
    )
    if series and series.get("summary"):
        event.series_summary = _expand_team_abbreviations(
            str(series["summary"]), competition, event
        )
