"""Deterministic generated prose composed only from public typed fields."""

import re
from collections import OrderedDict

from teamarr.core import GENERATED_PREVIEW_FIELDS, Event, Team

SUPPORTED_SPORTS = frozenset({"baseball", "football", "basketball"})

_DETAIL_FIELDS = GENERATED_PREVIEW_FIELDS

_ENRICHED_FIELDS = tuple(
    field
    for field in _DETAIL_FIELDS
    if field
    not in {
        "home_team_record",
        "away_team_record",
        "home_last_five",
        "away_last_five",
        "week",
    }
)


def has_generated_preview_detail(event: Event | None) -> bool:
    """Return whether a supported event carries meaningful pregame facts."""
    if not event:
        return False
    if event.sport not in SUPPORTED_SPORTS:
        return bool(event.away_team.name and event.home_team.name)
    return bool(
        any(getattr(event, field, None) for field in _DETAIL_FIELDS)
    )


def has_generated_preview_enrichment(event: Event | None) -> bool:
    """Return whether the event has facts beyond basic record/form context."""
    return bool(
        event
        and event.sport in SUPPORTED_SPORTS
        and any(getattr(event, field, None) for field in _ENRICHED_FIELDS)
    )


def _clean_number(value: str) -> str:
    return value[:-2] if value.endswith(".0") else value


def _team_subject(team: Team) -> str:
    short = (team.short_name or "").strip()
    full = team.name.strip()
    if short and full.lower().endswith(f" {short.lower()}"):
        return full[: -(len(short) + 1)]
    return full


def _recent_phrase(value: str) -> str:
    if not value or "-" not in value:
        return ""
    wins, losses = value.split("-", 1)
    try:
        count = int(wins) + int(losses)
    except ValueError:
        return ""
    return f"after going {wins}-{losses} in its last {count} games"


def _record_intro(name: str, record: str, recent: str) -> str:
    pieces = []
    if record:
        pieces.append(f"enters at {record}")
    if recent:
        pieces.append(_recent_phrase(recent))
    pieces = [piece for piece in pieces if piece]
    return f"{name} {' '.join(pieces)}" if pieces else name


def _leader_clause(value: str) -> str:
    if not value:
        return ""
    if " — " not in value:
        return f"led by {value}"
    name, detail = value.split(" — ", 1)
    return f"led by {name} with {detail}"


def _join_phrases(values: list[str]) -> str:
    if len(values) < 2:
        return values[0] if values else ""
    if len(values) == 2:
        return " and ".join(values)
    return f"{', '.join(values[:-1])}, and {values[-1]}"


def _leaders_sentence(values: list[str]) -> str:
    """Render all exact leader fields, grouping repeated athlete names."""
    grouped: OrderedDict[str, list[str]] = OrderedDict()
    for value in values:
        if not value:
            continue
        if " — " in value:
            name, detail = value.split(" — ", 1)
        else:
            name, detail = value, ""
        grouped.setdefault(name, [])
        if detail and detail not in grouped[name]:
            grouped[name].append(detail)
    if not grouped:
        return ""
    athlete_facts = [
        f"{name} with {_join_phrases(details)}" if details else name
        for name, details in grouped.items()
    ]
    if len(grouped) == 1:
        name, details = next(iter(grouped.items()))
        suffix = f" with {_join_phrases(details)}" if details else ""
        return f"{name} leads the team{suffix}."
    return f"Team leaders include {'; '.join(athlete_facts)}."


def _count_phrase(count: str, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == '1' else (plural or singular + 's')}"


def _football_leader_prose(value: str, role: str) -> str:
    """Expand ESPN abbreviations while leaving the public field unchanged."""
    if not value:
        return ""
    if " — " in value:
        name, detail = value.split(" — ", 1)
    else:
        name, detail = value, ""

    parts = [part.strip() for part in detail.split(",") if part.strip()]
    expanded: list[str] = []
    for part in parts:
        passing = re.fullmatch(r"(\d+)/(\d+)", part)
        carries = re.fullmatch(r"(\d+)\s+CAR", part, flags=re.IGNORECASE)
        receptions = re.fullmatch(r"(\d+)\s+REC", part, flags=re.IGNORECASE)
        yards = re.fullmatch(r"([\d.]+)\s+YDS?", part, flags=re.IGNORECASE)
        touchdowns = re.fullmatch(r"(\d+)\s+TD", part, flags=re.IGNORECASE)
        if passing:
            completed, attempted = passing.groups()
            expanded.append(f"{completed}/{attempted} completions")
        elif carries:
            expanded.append(_count_phrase(carries.group(1), "carry", "carries"))
        elif receptions:
            expanded.append(_count_phrase(receptions.group(1), "reception"))
        elif yards:
            expanded.append(f"{yards.group(1)} {role} yards")
        elif touchdowns:
            expanded.append(
                _count_phrase(touchdowns.group(1), f"{role} touchdown")
            )
        else:
            expanded.append(part)

    if len(expanded) >= 2 and (
        expanded[0].endswith(" completion")
        or expanded[0].endswith(" completions")
        or expanded[0].endswith(" carry")
        or expanded[0].endswith(" carries")
        or expanded[0].endswith(" reception")
        or expanded[0].endswith(" receptions")
    ) and expanded[1].endswith(f"{role} yards"):
        detail = f"{expanded[0]} for {expanded[1]}"
        if expanded[2:]:
            detail += f" and {_join_phrases(expanded[2:])}"
    else:
        detail = _join_phrases(expanded)
    return f"{name} — {detail}" if detail else name


def _series_clause(value: str) -> str:
    """Turn ESPN's compact series summary into a grammatical clause."""
    summary = value.strip().rstrip(".")
    leading = re.fullmatch(
        r"(.+?)\s+leads?\s+(?:(?:the)\s+)?((?:season\s+)?series)\s+(.+)",
        summary,
        flags=re.IGNORECASE,
    )
    if leading:
        team, series_kind, result = leading.groups()
        return f"the {team} leading the {series_kind.lower()} {result}"
    if summary.lower().startswith("series "):
        return f"the {summary[0].lower() + summary[1:]}"
    return summary


def _probable_starter_sentence(value: str) -> str:
    """Render the typed probable-starter value as natural prose."""
    probable = value.strip()
    record_and_era = re.fullmatch(
        r"(.+?)\s+\(([^,()]+),\s*([0-9.]+)\s+ERA\)",
        probable,
        flags=re.IGNORECASE,
    )
    if record_and_era:
        name, record, era = record_and_era.groups()
        return f"Probable starter {name} is {record} with a {era} ERA."
    return f"Probable starter {probable}."


def _base_sentence(event: Event) -> str:
    away = event.away_team.name
    home = event.home_team.name
    venue = event.venue.name if event.venue else ""
    location = f" at {venue}" if venue else ""
    if event.sport == "football" and event.week:
        phase = "preseason " if event.season_type == "preseason" else ""
        return f"The {away} visit the {home}{location} for a Week {event.week} {phase}matchup."
    if event.sport == "baseball" and event.series_summary:
        summary = _series_clause(event.series_summary)
        return f"The {away} visit the {home}{location}, with {summary}."
    return f"The {away} visit the {home}{location}."


def _baseball_sentence(event: Event, side: str) -> str:
    team = getattr(event, f"{side}_team")
    text = _record_intro(
        _team_subject(team),
        getattr(event, f"{side}_team_record"),
        getattr(event, f"{side}_last_five"),
    )
    starter = getattr(event, f"{side}_probable_starter")
    leader = next(
        (
            getattr(event, f"{side}_{field}")
            for field in ("home_runs_leader", "batting_average_leader", "rbi_leader")
            if getattr(event, f"{side}_{field}")
        ),
        "",
    )
    clause = _leader_clause(leader)
    if clause:
        text += f", {clause}"
    sentences = [f"{text}."]
    if starter:
        sentences.append(_probable_starter_sentence(starter))
    return " ".join(sentences)


def _football_sentence(event: Event, side: str) -> str:
    team = getattr(event, f"{side}_team")
    text = _record_intro(
        _team_subject(team),
        getattr(event, f"{side}_team_record"),
        getattr(event, f"{side}_last_five"),
    )
    total = getattr(event, f"{side}_total_yards_per_game")
    rushing = getattr(event, f"{side}_rushing_yards_per_game")
    if total:
        text += f", producing {_clean_number(total)} total yards per game"
        if rushing:
            text += f", including {_clean_number(rushing)} rushing yards per game"
    leaders = [
        _football_leader_prose(getattr(event, f"{side}_{field}"), role)
        for field, role in (
            ("passing_leader", "passing"),
            ("rushing_leader", "rushing"),
            ("receiving_leader", "receiving"),
        )
    ]
    leader_sentence = _leaders_sentence(leaders)
    return " ".join(part for part in (text + ".", leader_sentence) if part)


def _basketball_sentence(event: Event, side: str) -> str:
    team = getattr(event, f"{side}_team")
    text = _record_intro(
        _team_subject(team),
        getattr(event, f"{side}_team_record"),
        getattr(event, f"{side}_last_five"),
    )
    scored = getattr(event, f"{side}_team_ppg")
    allowed = getattr(event, f"{side}_points_allowed_per_game")
    if scored:
        text += f", averaging {_clean_number(scored)} points"
        if allowed:
            text += f" while allowing {_clean_number(allowed)} per game"
        else:
            text += " per game"
    elif allowed:
        text += f", allowing {_clean_number(allowed)} points per game"
    leaders = [
        getattr(event, f"{side}_{field}")
        for field in ("points_leader", "rebounds_leader", "assists_leader")
    ]
    leader_sentence = _leaders_sentence(leaders)
    return " ".join(part for part in (text + ".", leader_sentence) if part)


def build_generated_preview(event: Event | None) -> str:
    """Build source-grounded prose for supported sports, or return empty."""
    if not has_generated_preview_detail(event):
        return ""
    assert event is not None
    if event.sport not in SUPPORTED_SPORTS:
        return _base_sentence(event)
    render = {
        "baseball": _baseball_sentence,
        "football": _football_sentence,
        "basketball": _basketball_sentence,
    }[event.sport]
    return " ".join(
        (_base_sentence(event), render(event, "away"), render(event, "home"))
    )
