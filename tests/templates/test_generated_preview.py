"""Typed ESPN preview facts and opt-in generated prose."""

from dataclasses import fields
from datetime import UTC, datetime
from unittest.mock import patch

from teamarr.core import GENERATED_PREVIEW_FIELDS, Event, EventStatus, Team, Venue
from teamarr.database.provider_cache import dict_to_event, event_to_dict
from teamarr.providers.espn.preview import apply_generated_preview_fields
from teamarr.services.sports_data import SportsDataService
from teamarr.templates.conditions import ConditionEvaluator
from teamarr.templates.context import GameContext, TeamChannelContext, TemplateContext
from teamarr.templates.generated_preview import build_generated_preview
from teamarr.templates.variables.generated_preview import extract_generated_preview
from teamarr.templates.variables.registry import get_registry
from tests.fakes import FakeCache


def _team(name: str, team_id: str) -> Team:
    return Team(
        id=team_id,
        provider="espn",
        name=name,
        short_name=name.split()[-1],
        abbreviation=name[:3].upper(),
        league="test",
        sport="test",
    )


def _event(sport="baseball", league="mlb", **overrides) -> Event:
    values = {
        "id": "401",
        "provider": "espn",
        "name": "Away at Home",
        "short_name": "AWY @ HOM",
        "start_time": datetime(2026, 8, 25, 23, tzinfo=UTC),
        "home_team": _team("Miami Marlins", "28"),
        "away_team": _team("Boston Red Sox", "2"),
        "status": EventStatus(state="scheduled"),
        "league": league,
        "sport": sport,
        "venue": Venue(name="Example Park", city="Miami", state="FL"),
    }
    values.update(overrides)
    return Event(**values)


def _context(event: Event) -> tuple[TemplateContext, GameContext]:
    game = GameContext(
        event=event,
        is_home=True,
        team=event.home_team,
        opponent=event.away_team,
    )
    ctx = TemplateContext(
        game_context=game,
        team_config=TeamChannelContext(
            team_id=event.home_team.id,
            league=event.league,
            sport=event.sport,
            team_name=event.home_team.name,
        ),
        team_stats=None,
    )
    return ctx, game


def _competition(event: Event, extra_home=None, extra_away=None) -> dict:
    return {
        "competitors": [
            {
                "homeAway": "home",
                "team": {"id": event.home_team.id, "displayName": event.home_team.name},
                "record": [{"type": "total", "summary": "67-65"}],
                **(extra_home or {}),
            },
            {
                "homeAway": "away",
                "team": {"id": event.away_team.id, "displayName": event.away_team.name},
                "record": [{"type": "total", "summary": "72-59"}],
                **(extra_away or {}),
            },
        ]
    }


def _leader_block(team_id: str, categories: list[tuple[str, str, str]]) -> dict:
    return {
        "team": {"id": team_id},
        "leaders": [
            {
                "name": stat,
                "leaders": [
                    {
                        "displayValue": value,
                        "athlete": {"displayName": athlete},
                    }
                ],
            }
            for stat, athlete, value in categories
        ],
    }


def _probable(name: str, wins: str, losses: str, era: str) -> dict:
    return {
        "athlete": {"displayName": name},
        "statistics": {
            "splits": {
                "categories": [
                    {"name": "wins", "displayValue": wins},
                    {"name": "losses", "displayValue": losses},
                    {"name": "ERA", "displayValue": era},
                ]
            }
        },
    }


def test_baseball_parser_populates_exact_public_fields():
    event = _event()
    payload = {
        "header": {
            "competitions": [
                _competition(
                    event,
                    extra_home={"probables": [_probable("Tyler Phillips", "3", "6", "3.67")]},
                    extra_away={"probables": [_probable("Payton Tolle", "8", "6", "3.08")]},
                )
            ]
        },
        "leaders": [
            _leader_block(
                "28",
                [
                    ("homeRuns", "Heriberto Hernandez", "19"),
                    ("avg", "Otto Lopez", ".307"),
                    ("RBIs", "Otto Lopez", "59"),
                ],
            ),
            _leader_block(
                "2",
                [
                    ("homeRuns", "Willson Contreras", "26"),
                    ("avg", "Ceddanne Rafaela", ".289"),
                    ("RBIs", "Willson Contreras", "78"),
                ],
            ),
        ],
        "seasonseries": [{"type": "season", "summary": "BOS leads 2-1"}],
        "pickcenter": [{"spread": -1.5}],
    }

    apply_generated_preview_fields(payload, event)

    assert event.home_team_record == "67-65"
    assert event.away_team_record == "72-59"
    assert event.home_probable_starter == "Tyler Phillips (3-6, 3.67 ERA)"
    assert event.away_probable_starter == "Payton Tolle (8-6, 3.08 ERA)"
    assert event.away_home_runs_leader == "Willson Contreras — 26 home runs"
    assert event.home_batting_average_leader == "Otto Lopez — .307 batting average"
    assert event.away_rbi_leader == "Willson Contreras — 78 RBI"
    assert event.series_summary == "Boston Red Sox leads 2-1"
    assert not hasattr(event, "pickcenter")


def test_baseball_renderer_includes_starter_and_exact_leader_fallbacks():
    event = _event(
        away_team_record="72-59",
        home_team_record="67-65",
        away_last_five="3-2",
        home_last_five="2-3",
        away_probable_starter="Payton Tolle (8-6, 3.08 ERA)",
        away_home_runs_leader="Willson Contreras — 26 home runs",
        home_probable_starter="Tyler Phillips (3-6, 3.67 ERA)",
        home_home_runs_leader="Heriberto Hernandez — 19 home runs",
    )

    text = build_generated_preview(event)

    assert "led by Willson Contreras with 26 home runs" in text
    assert "led by Heriberto Hernandez with 19 home runs" in text
    assert "Probable starter Payton Tolle is 8-6 with a 3.08 ERA" in text
    assert "Probable starter Tyler Phillips is 3-6 with a 3.67 ERA" in text
    assert "72-59 after going 3-2 in its last 5 games" in text


def test_baseball_renderer_normalizes_series_summary_grammar():
    leading = _event(
        series_summary="Boston Red Sox leads series 1-0",
        away_probable_starter="Payton Tolle (8-6, 3.08 ERA)",
    )
    tied = _event(
        series_summary="Series tied 1-1",
        away_probable_starter="Payton Tolle (8-6, 3.08 ERA)",
    )

    assert (
        "with the Boston Red Sox leading the series 1-0."
        in build_generated_preview(leading)
    )
    assert "with the series tied 1-1." in build_generated_preview(tied)


def test_football_parser_and_renderer_include_all_secondary_stats():
    event = _event(
        sport="football",
        league="nfl",
        home_team=_team("Denver Broncos", "7"),
        away_team=_team("Green Bay Packers", "9"),
        season_type="preseason",
    )
    competition = _competition(event)
    payload = {
        "header": {"week": {"number": 3}, "competitions": [competition]},
        "leaders": [
            _leader_block(
                "9",
                [
                    ("passingYards", "Jordan Love", "221"),
                    ("rushingYards", "Josh Jacobs", "73"),
                    ("receivingYards", "Jayden Reed", "79"),
                ],
            ),
            _leader_block(
                "7",
                [
                    ("passingYards", "Bo Nix", "246"),
                    ("rushingYards", "J.K. Dobbins", "68"),
                    ("receivingYards", "Courtland Sutton", "84"),
                ],
            ),
        ],
        "boxscore": {
            "teams": [
                {
                    "team": {"id": "9"},
                    "statistics": [
                        {"name": "yardsPerGame", "displayValue": "204.0"},
                        {"name": "rushingYardsPerGame", "displayValue": "63.0"},
                    ],
                },
                {
                    "team": {"id": "7"},
                    "statistics": [
                        {"name": "yardsPerGame", "displayValue": "360.0"},
                        {"name": "rushingYardsPerGame", "displayValue": "162.0"},
                    ],
                },
            ]
        },
        "odds": [{"details": "DEN -6.5"}],
    }

    apply_generated_preview_fields(payload, event)
    text = build_generated_preview(event)

    assert event.week == 3
    assert event.home_passing_leader == "Bo Nix — 246 passing yards"
    assert event.away_rushing_leader == "Josh Jacobs — 73 rushing yards"
    assert "Week 3 preseason matchup" in text
    assert "360 total yards per game, including 162 rushing yards per game" in text
    assert "Bo Nix with 246 passing yards" in text
    assert "J.K. Dobbins with 68 rushing yards" in text
    assert "Courtland Sutton with 84 receiving yards" in text
    assert "Jordan Love with 221 passing yards" in text
    assert "Josh Jacobs with 73 rushing yards" in text
    assert "Jayden Reed with 79 receiving yards" in text
    assert "6.5" not in text


def test_malformed_week_shape_is_empty_not_raw_dict_text():
    event = _event(sport="football", league="nfl")
    payload = {
        "header": {
            "week": {"unexpected": "shape"},
            "competitions": [_competition(event)],
        }
    }

    apply_generated_preview_fields(payload, event)

    assert event.week is None
    assert "unexpected" not in build_generated_preview(event)


def test_secondary_leaders_render_when_primary_leader_is_missing():
    football = _event(
        sport="football",
        league="nfl",
        away_rushing_leader="Josh Jacobs — 73 rushing yards",
    )
    basketball = _event(
        sport="basketball",
        league="wnba",
        home_rebounds_leader="Kamilla Cardoso — 8.8 rebounds per game",
    )

    assert "Josh Jacobs leads the team with 73 rushing yards" in (
        build_generated_preview(football)
    )
    assert "Kamilla Cardoso leads the team with 8.8 rebounds per game" in (
        build_generated_preview(basketball)
    )


def test_basketball_parser_and_renderer_include_all_secondary_stats():
    event = _event(
        sport="basketball",
        league="wnba",
        home_team=_team("Connecticut Sun", "18"),
        away_team=_team("Chicago Sky", "19"),
    )
    payload = {
        "header": {"competitions": [_competition(event)]},
        "leaders": [
            _leader_block(
                "19",
                [
                    ("pointsPerGame", "Kamilla Cardoso", "14.7"),
                    ("reboundsPerGame", "Kamilla Cardoso", "8.8"),
                    ("assistsPerGame", "Natasha Cloud", "5.0"),
                ],
            ),
            _leader_block(
                "18",
                [
                    ("pointsPerGame", "Leila Lacan", "11.6"),
                    ("reboundsPerGame", "Olivia Nelson-Ododa", "5.9"),
                    ("assistsPerGame", "Leila Lacan", "4.6"),
                ],
            ),
        ],
        "boxscore": {
            "teams": [
                {
                    "team": {"id": "19"},
                    "statistics": [
                        {"name": "avgPoints", "displayValue": "87.4"},
                        {"name": "avgPointsAgainst", "displayValue": "89.9"},
                    ],
                },
                {
                    "team": {"id": "18"},
                    "statistics": [
                        {"name": "avgPoints", "displayValue": "79.0"},
                        {"name": "avgPointsAgainst", "displayValue": "87.0"},
                    ],
                },
            ]
        },
    }

    apply_generated_preview_fields(payload, event)
    text = build_generated_preview(event)

    assert event.away_rebounds_leader == "Kamilla Cardoso — 8.8 rebounds per game"
    assert event.home_points_leader == "Leila Lacan — 11.6 points per game"
    assert event.away_team_ppg == "87.4"
    assert "averaging 87.4 points while allowing 89.9 per game" in text
    assert "Kamilla Cardoso with 14.7 points per game and 8.8 rebounds per game" in text
    assert "Natasha Cloud with 5.0 assists per game" in text
    assert "Leila Lacan with 11.6 points per game and 4.6 assists per game" in text
    assert "Olivia Nelson-Ododa with 5.9 rebounds per game" in text


def test_named_variables_are_exact_and_generated_preview_is_opt_in():
    event = _event(
        away_home_runs_leader="Willson Contreras — 26 home runs",
        away_probable_starter="Payton Tolle (8-6, 3.08 ERA)",
    )
    ctx, game = _context(event)
    registry = get_registry()

    assert registry.get("away_home_runs_leader").extractor(ctx, game) == (
        "Willson Contreras — 26 home runs"
    )
    assert registry.get("away_probable_starter").extractor(ctx, game) == (
        "Payton Tolle (8-6, 3.08 ERA)"
    )
    assert extract_generated_preview(ctx, game) == build_generated_preview(event)
    assert ConditionEvaluator().evaluate("has_generated_preview", None, ctx, game)


def test_generated_preview_field_registry_cache_and_refresh_parity():
    """The canonical field tuple drives every persistence and public path."""
    assert len(GENERATED_PREVIEW_FIELDS) == len(set(GENERATED_PREVIEW_FIELDS))
    event_fields = {field.name for field in fields(Event)}
    assert set(GENERATED_PREVIEW_FIELDS) <= event_fields
    assert SportsDataService._PREVIEW_FIELDS == (
        "game_preview",
        *GENERATED_PREVIEW_FIELDS,
    )
    assert set(GENERATED_PREVIEW_FIELDS) <= set(SportsDataService._REFRESH_FIELDS)

    original_values = {
        field_name: 3 if field_name == "week" else f"original:{field_name}"
        for field_name in GENERATED_PREVIEW_FIELDS
    }
    live_values = {
        field_name: 4 if field_name == "week" else f"live:{field_name}"
        for field_name in GENERATED_PREVIEW_FIELDS
    }
    original = _event(**original_values)
    serialized = event_to_dict(original)
    restored = dict_to_event(serialized)
    legacy_restored = dict_to_event(
        {key: value for key, value in serialized.items() if key != "week"}
    )

    service = SportsDataService.__new__(SportsDataService)
    service._cache = FakeCache()
    live = _event(status=EventStatus(state="in_progress"), **live_values)
    with patch.object(SportsDataService, "get_event", return_value=live):
        refreshed = service.refresh_event_status(original)

    registry = get_registry()
    assert legacy_restored.week is None
    for field_name, expected in original_values.items():
        assert serialized[field_name] == expected
        assert getattr(restored, field_name) == expected
        assert getattr(refreshed, field_name) == expected
        assert registry.get(field_name) is not None


def test_formatted_football_leader_is_not_given_a_duplicate_label():
    event = _event(
        sport="football",
        league="nfl",
        home_team=_team("Denver Broncos", "7"),
        away_team=_team("Green Bay Packers", "9"),
    )
    payload = {
        "header": {"competitions": [_competition(event)]},
        "leaders": [
            _leader_block("7", [("passingLeader", "Bo Nix", "19/31, 181 YDS")])
        ],
    }

    apply_generated_preview_fields(payload, event)

    assert event.home_passing_leader == "Bo Nix — 19/31, 181 YDS"
    assert "Bo Nix leads the team with 19/31 completions for 181 passing yards" in (
        build_generated_preview(event)
    )


def test_nfl_stat_abbreviations_expand_in_generated_prose():
    event = _event(
        sport="football",
        league="nfl",
        season_type="preseason",
        week=4,
        away_team=_team("Pittsburgh Steelers", "23"),
        home_team=_team("Buffalo Bills", "2"),
        venue=Venue(name="Highmark Stadium", city="Orchard Park", state="NY"),
        away_last_five="2-3",
        home_last_five="4-1",
    )
    payload = {
        "header": {
            "week": {"number": 4},
            "competitions": [
                _competition(
                    event,
                    extra_home={"record": [{"type": "total", "summary": "2-0"}]},
                    extra_away={"record": [{"type": "total", "summary": "1-1"}]},
                )
            ],
        },
        "leaders": [
            _leader_block(
                "23",
                [
                    ("passingYards", "Drew Allar", "11/23, 110 YDS"),
                    ("rushingYards", "Kaleb Johnson", "10 CAR, 39 YDS, 1 TD"),
                    ("receivingYards", "Lake McRee", "2 REC, 46 YDS"),
                ],
            ),
            _leader_block(
                "2",
                [
                    ("passingYards", "Kyle Allen", "10/12, 128 YDS, 1 TD"),
                    ("rushingYards", "Ian Wheeler", "14 CAR, 56 YDS"),
                    ("receivingYards", "Ja'Mori Maclin", "2 REC, 108 YDS, 1 TD"),
                ],
            ),
        ],
        "boxscore": {
            "teams": [
                {
                    "team": {"id": "23"},
                    "statistics": [
                        {"name": "yardsPerGame", "displayValue": "343"},
                        {"name": "rushingYardsPerGame", "displayValue": "85"},
                    ],
                },
                {
                    "team": {"id": "2"},
                    "statistics": [
                        {"name": "yardsPerGame", "displayValue": "398.5"},
                        {"name": "rushingYardsPerGame", "displayValue": "144.5"},
                    ],
                },
            ]
        },
    }

    apply_generated_preview_fields(payload, event)

    assert build_generated_preview(event) == (
        "The Pittsburgh Steelers visit the Buffalo Bills at Highmark Stadium for a "
        "Week 4 preseason matchup. Pittsburgh enters at 1-1 after going 2-3 in its "
        "last 5 games, producing 343 total yards per game, including 85 rushing yards "
        "per game. Team leaders include Drew Allar with 11/23 completions for 110 "
        "passing yards; Kaleb Johnson with 10 carries for 39 rushing yards and 1 "
        "rushing touchdown; Lake McRee with 2 receptions for 46 receiving yards. "
        "Buffalo enters at 2-0 after going "
        "4-1 in its last 5 games, producing 398.5 total yards per game, including 144.5 "
        "rushing yards per game. Team leaders include Kyle Allen with 10/12 "
        "completions for 128 passing yards and 1 passing touchdown; Ian Wheeler with 14 "
        "carries for "
        "56 rushing yards; Ja'Mori Maclin with 2 receptions for 108 receiving yards "
        "and 1 receiving touchdown."
    )


def test_unsupported_sport_generates_only_generic_complete_matchup():
    event = _event(
        sport="hockey",
        league="nhl",
        away_team=_team("New York Rangers", "13"),
        home_team=_team("Boston Bruins", "1"),
        venue=Venue(name="TD Garden", city="Boston", state="MA"),
        away_team_record="44-22-8",
        home_team_record="46-24-5",
    )
    ctx, game = _context(event)

    assert build_generated_preview(event) == (
        "The New York Rangers visit the Boston Bruins at TD Garden."
    )
    assert ConditionEvaluator().evaluate("has_generated_preview", None, ctx, game)
    assert "44-22-8" not in build_generated_preview(event)
