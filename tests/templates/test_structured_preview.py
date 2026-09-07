"""Tier-2 structured previews (tvnk.15, #329).

Covers the lastFiveGames summary parse, the budgeted/cached/gated
enrich_event_preview service path, the recent-form variables, the
has_structured_preview condition, and the starter-set wiring.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from teamarr.core.types import Event, EventStatus, Team
from teamarr.database.default_templates import DEFAULT_TEMPLATE_SET
from teamarr.providers.espn.provider import ESPNProvider
from teamarr.services.sports_data import SportsDataService
from teamarr.templates.conditions import ConditionEvaluator
from teamarr.templates.context import (
    GameContext,
    TeamChannelContext,
    TemplateContext,
)
from teamarr.templates.variables.summary import (
    extract_away_last_five,
    extract_home_last_five,
    extract_last_five_summary,
)
from tests.fakes import FakeCache


def _team(name, id_):
    return Team(
        id=id_,
        provider="espn",
        name=name,
        short_name=name,
        abbreviation=name[:3].upper(),
        league="mlb",
        sport="baseball",
    )


def _event(start_in_hours=48.0, **kw):
    base = dict(
        id="401",
        provider="espn",
        name="TB @ BOS",
        short_name="TB @ BOS",
        start_time=datetime.now(UTC) + timedelta(hours=start_in_hours),
        home_team=_team("Boston Red Sox", "2"),
        away_team=_team("Tampa Bay Rays", "30"),
        status=EventStatus(state="pre"),
        league="mlb",
        sport="baseball",
    )
    base.update(kw)
    return Event(**base)


def _ctx(event):
    gc = GameContext(event=event, is_home=True, team=event.home_team, opponent=event.away_team)
    tc = TeamChannelContext(team_id="2", league="mlb", sport="baseball", team_name="Boston Red Sox")
    return TemplateContext(game_context=gc, team_config=tc, team_stats=None), gc


# --- provider parse ---


def test_parse_last_five():
    event = _event()
    payload = [
        {
            "team": {"id": "2"},
            "events": [{"gameResult": r} for r in ("W", "W", "L", "W", "W")],
        },
        {
            "team": {"id": "30"},
            "events": [{"gameResult": r} for r in ("L", "W", "L", "L", "W")],
        },
    ]
    home, away = ESPNProvider._parse_last_five(payload, event)
    assert home == "4-1"
    assert away == "2-3"


def test_parse_last_five_absent_or_malformed():
    event = _event()
    assert ESPNProvider._parse_last_five([], event) == ("", "")
    assert ESPNProvider._parse_last_five([{"team": {}, "events": []}], event) == ("", "")
    # Unknown team id doesn't attach anywhere
    assert ESPNProvider._parse_last_five(
        [{"team": {"id": "99"}, "events": [{"gameResult": "W"}]}], event
    ) == ("", "")


# --- service enrichment: gate / cache / budget ---


def _service():
    svc = SportsDataService.__new__(SportsDataService)
    svc._cache = FakeCache()
    return svc


def test_enrich_gates_past_and_far_events():
    svc = _service()
    with patch.object(SportsDataService, "get_event") as fetch:
        past = svc.enrich_event_preview(_event(start_in_hours=-2))
        far = svc.enrich_event_preview(_event(start_in_hours=24 * 10))
        assert fetch.call_count == 0
    assert past.home_last_five == "" and far.home_last_five == ""


def test_cached_preview_snapshot_survives_kickoff():
    svc = _service()
    cached_fields = {
        "game_preview": "Pregame copy",
        "series_summary": "",
        "home_last_five": "4-1",
        "away_last_five": "2-3",
        "home_probable_starter": "Starter One (8-2, 3.10 ERA)",
    }
    svc._cache.set("event_preview_v2:mlb:401", cached_fields, 3600)
    with patch.object(SportsDataService, "get_event") as fetch:
        event = svc.enrich_event_preview(_event(start_in_hours=-2))
    assert fetch.call_count == 0
    assert event.game_preview == "Pregame copy"
    assert event.home_probable_starter == "Starter One (8-2, 3.10 ERA)"


def test_status_refresh_cannot_replace_frozen_preview_with_live_stats():
    svc = _service()
    original = _event(
        home_team_ppg="117.2",
        home_points_leader="Player — 28.4 points per game",
    )
    fresh = _event(
        status=EventStatus(state="in_progress"),
        home_team_ppg="7",
        home_points_leader="Live Player — 5 points",
    )
    with patch.object(SportsDataService, "get_event", return_value=fresh):
        refreshed = svc.refresh_event_status(original)
    assert refreshed.status.state == "in_progress"
    assert refreshed.home_team_ppg == "117.2"
    assert refreshed.home_points_leader == "Player — 28.4 points per game"


def test_status_refresh_does_not_create_preview_from_live_summary():
    svc = _service()
    original = _event()
    fresh = _event(status=EventStatus(state="in_progress"), home_team_ppg="7")
    with patch.object(SportsDataService, "get_event", return_value=fresh):
        refreshed = svc.refresh_event_status(original)
    assert refreshed.status.state == "in_progress"
    assert refreshed.home_team_ppg == ""


def test_partial_last_five_does_not_block_richer_refresh():
    svc = _service()
    fresh = _event(home_last_five="4-1", home_probable_starter="Starter (8-2, 3.10 ERA)")
    with patch.object(SportsDataService, "get_event", return_value=fresh) as fetch:
        ev = svc.enrich_event_preview(_event(home_last_five="4-1"))
        assert fetch.call_count == 1
    assert ev.home_last_five == "4-1"
    assert ev.home_probable_starter == "Starter (8-2, 3.10 ERA)"


def test_enrich_fetches_caches_and_overlays():
    svc = _service()
    fresh = _event(
        home_last_five="4-1", away_last_five="2-3", series_summary="BOS leads series 3-2"
    )
    with patch.object(SportsDataService, "get_event", return_value=fresh) as fetch:
        first = svc.enrich_event_preview(_event())
        second = svc.enrich_event_preview(_event())  # served from preview cache
        assert fetch.call_count == 1
    assert first.home_last_five == "4-1"
    assert first.series_summary == "BOS leads series 3-2"
    assert second.away_last_five == "2-3"


def test_enrich_respects_budget():
    svc = _service()
    svc._cache.set("event_preview_budget:window", svc.PREVIEW_FETCH_BUDGET, 3600)
    with patch.object(SportsDataService, "get_event") as fetch:
        ev = svc.enrich_event_preview(_event())
        assert fetch.call_count == 0
    assert ev.home_last_five == ""


def test_enrich_caches_negative_results():
    svc = _service()
    with patch.object(SportsDataService, "get_event", return_value=_event()) as fetch:
        svc.enrich_event_preview(_event())
        svc.enrich_event_preview(_event())
        assert fetch.call_count == 1  # empty result cached, budget not re-spent


# --- variables + condition ---


def test_last_five_vars_and_summary():
    ctx, gc = _ctx(_event(home_last_five="4-1", away_last_five="2-3"))
    assert extract_home_last_five(ctx, gc) == "4-1"
    assert extract_away_last_five(ctx, gc) == "2-3"
    assert extract_last_five_summary(ctx, gc) == (
        "the Tampa Bay Rays have won 2 of their last five; "
        "the Boston Red Sox have won 4 of their last five."
    )


def test_last_five_summary_partial_and_empty():
    ctx, gc = _ctx(_event(home_last_five="4-1"))
    assert extract_last_five_summary(ctx, gc) == (
        "the Boston Red Sox have won 4 of their last five."
    )
    ctx, gc = _ctx(_event())
    assert extract_last_five_summary(ctx, gc) == ""


def test_has_structured_preview_condition():
    ev = ConditionEvaluator()
    ctx, gc = _ctx(_event(away_last_five="2-3"))
    assert ev.evaluate("has_structured_preview", None, ctx, gc) is True
    ctx, gc = _ctx(_event())
    assert ev.evaluate("has_structured_preview", None, ctx, gc) is False


# --- starter-set wiring ---


def test_starters_carry_structured_preview_tier():
    by_name = {s["name"]: s for s in DEFAULT_TEMPLATE_SET}
    for name in (
        "Default Team (Starter)",
        "Default Event (Starter)",
        "International Event (Starter)",
        "Soccer Team (Starter)",
        "Soccer Club Event (Starter)",
        "College Team (Starter)",
        "College Event (Starter)",
    ):
        conds = by_name[name]["conditional_descriptions"]
        tiers = [(c.get("condition"), c["priority"]) for c in conds]
        assert ("has_preview", 10) in tiers, name
        assert ("has_structured_preview", 20) in tiers, name
        assert tiers[-1][1] == 100, name
        structured = next(c for c in conds if c.get("condition") == "has_structured_preview")
        assert "{last_five_summary}" in structured["template"], name
