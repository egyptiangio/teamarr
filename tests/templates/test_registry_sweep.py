"""Registry-driven variable/condition sweep (#437).

Complements the starter goldens (#436): goldens pin composed output for the
10 starters, but only exercise the variables and conditions those starters
use. This suite proves every REGISTERED variable and condition evaluator is
wired, so a regression in one no starter uses cannot ship silently
(extractors are empty-safe by design, and unknown condition names silently
evaluate False via the ``getattr(self, f"_eval_{condition}")`` dispatch).

Four guarantees:

1. Every registered variable, in every legal suffix form per its
   SuffixRules, resolves without crashing and never leaves an unresolved
   ``{token}`` — across every curated context, including event-less ones.
2. Every registered variable renders non-empty in at least one curated rich
   context (catches "variable went dark"). UNPOPULATABLE documents the only
   allowed exemptions.
3. Picker/evaluator parity both ways: every condition served by
   ``GET /variables/conditions`` (both template_types) has a matching
   ``ConditionEvaluator._eval_*`` method, and every ``_eval_*`` method is
   exposed in the picker. HIDDEN_CONDITIONS documents deliberate orphans.
4. Every condition evaluates True in >=1 context and False in >=1
   event-bearing context — proving real wiring rather than the dispatch's
   default-False fallthrough. The case matrix is completeness-checked, so a
   newly added evaluator without matrix coverage fails the suite.
"""

from datetime import UTC, datetime, timedelta

import pytest

from teamarr.api.routes.variables import get_conditions
from teamarr.core import SEASON_POSTSEASON, SEASON_PRESEASON
from teamarr.core.types import (
    Bout,
    Event,
    EventStatus,
    RacingResult,
    RacingSession,
    Team,
    TeamStats,
    Venue,
)
from teamarr.services import league_mappings as lm
from teamarr.templates.conditions import ConditionEvaluator
from teamarr.templates.context import GameContext, Odds, TeamChannelContext, TemplateContext
from teamarr.templates.resolver import VARIABLE_PATTERN, TemplateResolver
from teamarr.templates.variables import SuffixRules, get_registry


@pytest.fixture(autouse=True)
def real_league_service(db_factory):
    """The real LeagueMappingService over the seeded temp DB, so league
    display names, gracenote categories, and soccer league mappings resolve
    exactly as in production."""
    prior = lm._league_mapping_service
    lm.init_league_mapping_service(db_factory)
    yield
    lm._league_mapping_service = prior


@pytest.fixture
def resolver():
    return TemplateResolver()


# --- context builders -------------------------------------------------------
#
# Curated rich contexts. Together they must populate every registered
# variable (guarantee 2) and drive every condition both True and False
# (guarantee 4). Future dates are relative to now so datetime variables
# (days_until, relative_day) populate.

NOW = datetime.now(UTC)
_VENUE = Venue(name="The Palace", city="Auburn Hills", state="MI", country="USA")


def _team(name, abbrev, league, sport, id_="1", short=None, record=None):
    return Team(
        id=id_,
        provider="espn",
        name=name,
        short_name=short or name,
        abbreviation=abbrev,
        league=league,
        sport=sport,
        logo_url=f"https://img.example/{abbrev.lower()}.png",
        record_summary=record,
    )


def _stats(**kw):
    """Kitchen-sink TeamStats — every stats-backed variable has a source."""
    base = dict(
        record="10-2",
        wins=10,
        losses=2,
        ties=1,
        home_record="6-1",
        away_record="4-1",
        streak="W3",
        streak_count=3,
        playoff_seed=2,
        games_back=1.5,
        conference="Eastern Conference",
        conference_abbrev="East",
        division="Atlantic Division",
        ppg=112.4,
        papg=104.9,
    )
    base.update(kw)
    return TeamStats(**base)


def _odds():
    return Odds(
        provider="ESPN BET",
        spread=3.5,
        over_under=221.5,
        details="BOS -3.5",
        team_moneyline=-150,
        opponent_moneyline=130,
    )


def _event(home, away, league, sport, *, state="pre", detail=None, start=None, venue=_VENUE, **kw):
    return Event(
        id="e1",
        provider="espn",
        name=f"{away.name} at {home.name}",
        short_name=f"{away.abbreviation} @ {home.abbreviation}",
        start_time=start or (NOW + timedelta(days=3)),
        home_team=home,
        away_team=away,
        status=EventStatus(state=state, detail=detail),
        league=league,
        sport=sport,
        venue=venue,
        **kw,
    )


def _wrap(
    game_ctx,
    team,
    league,
    sport,
    *,
    team_stats=None,
    next_game=None,
    last_game=None,
    feed_team=None,
    extra_vars=None,
    soccer_primary=None,
):
    return TemplateContext(
        game_context=game_ctx,
        team_config=TeamChannelContext(
            team_id=team.id,
            league=league,
            sport=sport,
            team_name=team.name,
            team_abbrev=team.abbreviation,
            team_short_name=team.short_name,
            team_logo_url=team.logo_url,
            league_name=None,
            soccer_primary_league=(soccer_primary or [None, None])[0],
            soccer_primary_league_id=(soccer_primary or [None, None])[1],
        ),
        team_stats=team_stats,
        team=team,
        next_game=next_game,
        last_game=last_game,
        feed_team=feed_team,
        extra_vars=extra_vars or {},
    )


_NBA_HOME = _team("Boston Celtics", "BOS", "nba", "basketball", "1", short="Celtics")
_NBA_AWAY = _team("Detroit Pistons", "DET", "nba", "basketball", "2", short="Pistons")


def _nba_event(**kw):
    defaults = dict(
        broadcasts=["ESPN", "NBA TV"],
        broadcast_markets={"ESPN": "national"},
        season_year=2026,
        season_type="regular",
        game_recap="Celtics hold off Pistons late",
        game_preview="Pistons look to even the series in Boston.",
        game_event_note="NBA Finals - Game 5",
        series_summary="Series tied 1-1",
        home_last_five="4-1",
        away_last_five="2-3",
        home_team_record="10-2",
        away_team_record="8-4",
        week=3,
        home_probable_starter="Home Starter (8-2, 3.10 ERA)",
        away_probable_starter="Away Starter (7-3, 3.40 ERA)",
        home_home_runs_leader="Home Slugger — 30 home runs",
        away_home_runs_leader="Away Slugger — 28 home runs",
        home_batting_average_leader="Home Hitter — .310 batting average",
        away_batting_average_leader="Away Hitter — .305 batting average",
        home_rbi_leader="Home Hitter — 80 RBI",
        away_rbi_leader="Away Hitter — 77 RBI",
        home_passing_leader="Home QB — 18/25, 246 YDS",
        away_passing_leader="Away QB — 17/24, 221 YDS",
        home_rushing_leader="Home RB — 12 CAR, 68 YDS",
        away_rushing_leader="Away RB — 14 CAR, 73 YDS",
        home_receiving_leader="Home WR — 6 REC, 84 YDS",
        away_receiving_leader="Away WR — 5 REC, 79 YDS",
        home_total_yards_per_game="360",
        away_total_yards_per_game="340",
        home_rushing_yards_per_game="162",
        away_rushing_yards_per_game="145",
        home_points_leader="J. Tatum — 28.1 points per game",
        away_points_leader="C. Cunningham — 25.4 points per game",
        home_rebounds_leader="K. Porzingis — 8.1 rebounds per game",
        away_rebounds_leader="J. Duren — 10.2 rebounds per game",
        home_assists_leader="J. Holiday — 6.0 assists per game",
        away_assists_leader="C. Cunningham — 9.1 assists per game",
        home_points_allowed_per_game="108.2",
        away_points_allowed_per_game="111.4",
    )
    defaults.update(kw)
    return _event(_NBA_HOME, _NBA_AWAY, "nba", "basketball", **defaults)


def us_pro_rich():
    """US pro kitchen sink: pregame base with odds and every copy field,
    a next game with odds, and a final overtime last game we won."""
    pre = GameContext(
        event=_nba_event(),
        is_home=True,
        team=_NBA_HOME,
        opponent=_NBA_AWAY,
        opponent_stats=_stats(
            record="8-4",
            streak="L2",
            streak_count=-2,
            playoff_seed=5,
            games_back=3.0,
            conference="Western Conference",
            conference_abbrev="West",
            division="Pacific Division",
        ),
        odds=_odds(),
    )
    final_ev = _nba_event(
        state="final",
        detail="Final/OT",
        start=NOW - timedelta(days=2),
        home_score=112,
        away_score=104,
    )
    last = GameContext(
        event=final_ev,
        is_home=True,
        team=_NBA_HOME,
        opponent=_NBA_AWAY,
        opponent_stats=_stats(record="8-4"),
    )
    return _wrap(
        pre,
        _NBA_HOME,
        "nba",
        "basketball",
        team_stats=_stats(),
        next_game=pre,
        last_game=last,
        extra_vars={"exception_keyword": "NBA Primetime"},
    )


def us_pro_final():
    """Base game IS final (is_final True; score/outcome vars in base form)."""
    ev = _nba_event(
        state="final",
        detail="Final/OT",
        start=NOW - timedelta(hours=3),
        home_score=112,
        away_score=104,
    )
    game = GameContext(
        event=ev,
        is_home=True,
        team=_NBA_HOME,
        opponent=_NBA_AWAY,
        opponent_stats=_stats(record="8-4"),
    )
    return _wrap(game, _NBA_HOME, "nba", "basketball", team_stats=_stats(), last_game=game)


def us_pro_away_today():
    """Away perspective, playing today (is_away, today_tonight), on a loss
    streak, preseason, neutral site, no odds/copy — the False side for most
    US-pro conditions."""
    ev = _event(
        _NBA_HOME,
        _NBA_AWAY,
        "nba",
        "basketball",
        start=NOW + timedelta(hours=2),
        season_type=SEASON_PRESEASON,
        neutral_site=True,
    )
    game = GameContext(
        event=ev,
        is_home=False,
        team=_NBA_AWAY,
        opponent=_NBA_HOME,
        opponent_stats=_stats(streak="L1", streak_count=-1),
    )
    return _wrap(
        game,
        _NBA_AWAY,
        "nba",
        "basketball",
        team_stats=_stats(record="2-10", streak="L4", streak_count=-4),
        next_game=game,
    )


def us_pro_playoff():
    ev = _nba_event(season_type=SEASON_POSTSEASON)
    game = GameContext(
        event=ev,
        is_home=True,
        team=_NBA_HOME,
        opponent=_NBA_AWAY,
        opponent_stats=_stats(record="8-4"),
    )
    return _wrap(
        game, _NBA_HOME, "nba", "basketball", team_stats=_stats(), next_game=game, last_game=game
    )


def college():
    """Ranked conference matchup — rankings/conference variables and the
    college condition family."""
    home = _team("Arkansas Razorbacks", "ARK", "mens-college-basketball", "basketball", "1")
    away = _team("Texas A&M Aggies", "TAMU", "mens-college-basketball", "basketball", "2")
    ev = _event(
        home,
        away,
        "mens-college-basketball",
        "basketball",
        venue=Venue(name="Bud Walton Arena", city="Fayetteville", state="AR"),
    )
    game = GameContext(
        event=ev,
        is_home=True,
        team=home,
        opponent=away,
        opponent_stats=_stats(
            record="19-8",
            rank=9,
            conference="Southeastern Conference",
            conference_abbrev="SEC",
            streak="L1",
            streak_count=-1,
        ),
    )
    return _wrap(
        game,
        home,
        "mens-college-basketball",
        "basketball",
        team_stats=_stats(
            record="20-7", rank=7, conference="Southeastern Conference", conference_abbrev="SEC"
        ),
        next_game=game,
        last_game=game,
    )


def soccer():
    home = _team("Chelsea", "CHE", "eng.1", "soccer", "1")
    away = _team("Arsenal", "ARS", "eng.1", "soccer", "2")
    ev = _event(
        home,
        away,
        "eng.1",
        "soccer",
        venue=Venue(name="Stamford Bridge", city="London", country="England"),
        soccer_match_note="FA Cup, Semifinal",
        broadcasts=["Peacock"],
    )
    game = GameContext(
        event=ev, is_home=True, team=home, opponent=away, opponent_stats=_stats(record="12-3-2")
    )
    return _wrap(
        game,
        home,
        "eng.1",
        "soccer",
        team_stats=_stats(record="10-2-5"),
        next_game=game,
        last_game=game,
        soccer_primary=["Premier League", "eng.1"],
    )


def feed():
    """Event-template feed separation: the channel carries the away feed."""
    ev = _nba_event()
    game = GameContext(event=ev, is_home=True, team=_NBA_HOME, opponent=_NBA_AWAY)
    return _wrap(game, _NBA_HOME, "nba", "basketball", team_stats=_stats(), feed_team=_NBA_AWAY)


def _combat_event(*, state="pre", start=None, **kw):
    f1 = _team("Alexander Volkanovski", "VOL", "ufc", "mma", "1", record="26-4-0")
    f2 = _team("Diego Lopes", "LOP", "ufc", "mma", "2", record="26-7-0")
    defaults = dict(
        main_card_start=NOW + timedelta(days=1, hours=4),
        segment_times={
            "early_prelims": NOW + timedelta(days=1),
            "prelims": NOW + timedelta(days=1, hours=2),
            "main_card": NOW + timedelta(days=1, hours=4),
        },
        bouts=[
            Bout(fighter1="A. Volkanovski", fighter2="D. Lopes", segment="main_card", order=4),
            Bout(fighter1="B. Ortega", fighter2="C. Jung", segment="main_card", order=3),
            Bout(fighter1="E. Fighter", fighter2="F. Fighter", segment="prelims", order=1),
            Bout(fighter1="G. Fighter", fighter2="H. Fighter", segment="early_prelims", order=0),
        ],
        weight_class="Featherweight",
    )
    defaults.update(kw)
    return Event(
        id="e1",
        provider="espn",
        name="UFC 325: Volkanovski vs Lopes",
        short_name="UFC 325",
        start_time=start or (NOW + timedelta(days=1)),
        home_team=f1,
        away_team=f2,
        status=EventStatus(state=state),
        league="ufc",
        sport="mma",
        venue=Venue(name="T-Mobile Arena", city="Las Vegas", state="NV"),
        **defaults,
    )


def _combat_ctx(ev, segment="main_card"):
    f1 = ev.home_team
    game = GameContext(event=ev, is_home=True, team=f1, opponent=ev.away_team, card_segment=segment)
    return _wrap(game, f1, "ufc", "mma", next_game=game, last_game=game)


def combat_pre():
    return _combat_ctx(_combat_event())


def combat_ko():
    ev = _combat_event(
        state="final",
        start=NOW - timedelta(days=1),
        fight_result_method="ko",
        finish_round=2,
        finish_time="3:48",
    )
    return _combat_ctx(ev)


def combat_submission():
    ev = _combat_event(
        state="final",
        start=NOW - timedelta(days=1),
        fight_result_method="submission",
        finish_round=3,
        finish_time="1:12",
    )
    return _combat_ctx(ev)


def combat_decision():
    ev = _combat_event(
        state="final",
        start=NOW - timedelta(days=1),
        fight_result_method="unanimous decision",
        fighter1_scores=[48, 49, 48],
        fighter2_scores=[47, 46, 47],
    )
    return _combat_ctx(ev)


def _racing_event(*, results=False):
    car = _team("Navy 250", "N250", "nascar-cup", "racing", "1")
    quali_results = [
        RacingResult(
            driver_name="Kyle Larson",
            team_name="Hendrick Motorsports",
            position=1,
            grid_position=1,
            points=0.0,
        ),
        RacingResult(
            driver_name="Ryan Blaney",
            team_name="Team Penske",
            position=2,
            grid_position=2,
            points=0.0,
        ),
    ]
    race_results = []
    if results:
        race_results = [
            RacingResult(
                driver_name="Kyle Larson",
                team_name="Hendrick Motorsports",
                position=1,
                grid_position=1,
                points=55.0,
                status="Finished",
            ),
            RacingResult(
                driver_name="Ryan Blaney",
                team_name="Team Penske",
                position=2,
                grid_position=2,
                points=44.0,
                fastest_lap=True,
                status="Finished",
            ),
            RacingResult(
                driver_name="Chase Elliott",
                team_name="Hendrick Motorsports",
                position=3,
                grid_position=5,
                points=41.0,
                status="Finished",
            ),
        ]
    base = NOW + timedelta(days=1) if not results else NOW - timedelta(days=1)
    return Event(
        id="e1",
        provider="nascar",
        name="Navy 250",
        short_name="Navy 250",
        start_time=base,
        home_team=car,
        away_team=car,
        status=EventStatus(state="final" if results else "pre"),
        league="nascar-cup",
        sport="racing",
        venue=Venue(name="Nashville Superspeedway", city="Lebanon", state="TN"),
        circuit_name="Nashville Superspeedway",
        race_laps=250,
        race_distance_miles=325.5,
        stage_laps=[60, 60, 130],
        sessions=[
            RacingSession(
                code="qualifying", name="Qualifying", start_time=base, results=quali_results
            ),
            RacingSession(
                code="race", name="Race", start_time=base + timedelta(days=1), results=race_results
            ),
        ],
    )


def _racing_ctx(ev, segment):
    car = ev.home_team
    game = GameContext(event=ev, is_home=True, team=car, opponent=car, card_segment=segment)
    return _wrap(game, car, "nascar-cup", "racing", next_game=game, last_game=game)


def racing_qualifying():
    return _racing_ctx(_racing_event(), "qualifying")


def racing_race_pre():
    """Race-session channel before the race runs — no results yet."""
    return _racing_ctx(_racing_event(), "race")


def racing_race_final():
    return _racing_ctx(_racing_event(results=True), "race")


def tennis():
    p1 = _team("Carlos Alcaraz", "ALC", "atp", "tennis", "1")
    p2 = _team("Jannik Sinner", "SIN", "atp", "tennis", "2")
    ev = _event(
        p1,
        p2,
        "atp",
        "tennis",
        venue=Venue(name="Centre Court", city="London"),
        tournament_name="Wimbledon",
        round_name="Final",
        court="Centre Court",
        draw_type="Men's Singles",
        is_major=True,
        game_recap="Sinner (ITA) bt Alcaraz (ESP) 6-2 6-2",
    )
    game = GameContext(event=ev, is_home=True, team=p1, opponent=p2)
    return _wrap(game, p1, "atp", "tennis", next_game=game, last_game=game)


def sparse():
    """Minimal event, no stats/odds/copy — the False side for data-gated
    conditions, and empty-safety coverage for every extractor."""
    home = _team("Toledo Walleye", "TOL", "echl", "hockey", "1")
    away = _team("Fort Wayne Komets", "FW", "echl", "hockey", "2")
    ev = Event(
        id="e1",
        provider="espn",
        name="Komets at Walleye",
        short_name="FW @ TOL",
        start_time=NOW + timedelta(days=1),
        home_team=home,
        away_team=away,
        status=EventStatus(state="pre"),
        league="echl",
        sport="hockey",
    )
    game = GameContext(event=ev, is_home=True, team=home, opponent=away)
    return _wrap(game, home, "echl", "hockey")


def incomplete_matchup():
    """Unsupported event missing a team name cannot form generic prose."""
    home = _team("", "TOL", "echl", "hockey", "1")
    away = _team("Fort Wayne Komets", "FW", "echl", "hockey", "2")
    ev = Event(
        id="e2",
        provider="espn",
        name="Incomplete matchup",
        short_name="FW @ TOL",
        start_time=NOW + timedelta(days=1),
        home_team=home,
        away_team=away,
        status=EventStatus(state="pre"),
        league="echl",
        sport="hockey",
    )
    game = GameContext(event=ev, is_home=True, team=home, opponent=away)
    return _wrap(game, home, "echl", "hockey")


def no_event():
    """No games at all (offseason): contextless-suffix and guard coverage."""
    return _wrap(GameContext(event=None), _NBA_HOME, "nba", "basketball")


CONTEXTS = {
    "us_pro_rich": us_pro_rich,
    "us_pro_final": us_pro_final,
    "us_pro_away_today": us_pro_away_today,
    "us_pro_playoff": us_pro_playoff,
    "college": college,
    "soccer": soccer,
    "feed": feed,
    "combat_pre": combat_pre,
    "combat_ko": combat_ko,
    "combat_submission": combat_submission,
    "combat_decision": combat_decision,
    "racing_qualifying": racing_qualifying,
    "racing_race_pre": racing_race_pre,
    "racing_race_final": racing_race_final,
    "tennis": tennis,
    "sparse": sparse,
    "incomplete_matchup": incomplete_matchup,
    "no_event": no_event,
}


def _legal_forms(var_def):
    """Every template token form the variable's SuffixRules allow."""
    rules = var_def.suffix_rules
    forms = []
    if rules != SuffixRules.LAST_ONLY:
        forms.append(var_def.name)
    if rules in (SuffixRules.ALL, SuffixRules.BASE_NEXT_ONLY):
        forms.append(f"{var_def.name}.next")
    if rules in (SuffixRules.ALL, SuffixRules.LAST_ONLY):
        forms.append(f"{var_def.name}.last")
    return forms


# --- guarantee 1: every form resolves clean in every context ----------------


@pytest.mark.parametrize("ctx_name", sorted(CONTEXTS))
def test_every_variable_form_resolves_clean(ctx_name, resolver):
    """No registered variable, in any legal suffix form, may crash or leave
    an unresolved {token} — even with missing game context (#418)."""
    ctx = CONTEXTS[ctx_name]()
    forms = [f for v in get_registry().all_variables() for f in _legal_forms(v)]
    template = " | ".join("{" + f + "}" for f in forms)
    out = resolver.resolve(template, ctx)
    leftover = VARIABLE_PATTERN.findall(out)
    assert not leftover, f"context {ctx_name}: unresolved tokens {sorted(set(leftover))}"


# --- guarantee 2: every variable goes non-empty somewhere -------------------

# Documented exemptions: variables genuinely unpopulatable from curated test
# data. Keep this list empty unless there is no honest way to populate the
# variable — each entry is a hole the sweep cannot see into.
UNPOPULATABLE: dict[str, str] = {}


def test_every_variable_renders_non_empty_somewhere(resolver):
    """A variable that is empty in EVERY curated context has gone dark —
    either enrich a context or (rarely) document it in UNPOPULATABLE."""
    populated: set[str] = set()
    for factory in CONTEXTS.values():
        var_map = resolver.build_variable_map(factory())
        populated |= {name.split(".")[0] for name, value in var_map.items() if value}

    all_names = {v.name for v in get_registry().all_variables()}
    dark = all_names - populated - set(UNPOPULATABLE)
    assert not dark, f"variables empty in every curated context: {sorted(dark)}"

    stale_exemptions = set(UNPOPULATABLE) & populated
    assert not stale_exemptions, (
        f"UNPOPULATABLE entries now populate — remove them: {sorted(stale_exemptions)}"
    )


# --- guarantee 3: picker <-> evaluator parity -------------------------------

# _eval_* methods deliberately NOT exposed in the condition picker.
HIDDEN_CONDITIONS = {
    # Legacy row compatibility only — new templates use priority-100 defaults.
    "always",
}


def _picker_conditions() -> set[str]:
    names: set[str] = set()
    for template_type in ("team", "event"):
        payload = get_conditions(template_type)
        names |= {c["name"] for c in payload["conditions"]}
    return names


def _evaluator_conditions() -> set[str]:
    return {
        name.removeprefix("_eval_") for name in dir(ConditionEvaluator) if name.startswith("_eval_")
    }


def test_condition_parity_picker_vs_evaluator():
    """Every picker entry must dispatch to a real evaluator (else it silently
    evaluates False forever), and every evaluator must be reachable from the
    picker (else it is dead code) unless deliberately hidden."""
    picker = _picker_conditions()
    evaluators = _evaluator_conditions()

    dead_picker_entries = picker - evaluators
    assert not dead_picker_entries, (
        f"picker exposes conditions with no _eval_* method: {sorted(dead_picker_entries)}"
    )

    orphans = evaluators - picker - HIDDEN_CONDITIONS
    assert not orphans, f"_eval_* methods not exposed in the picker: {sorted(orphans)}"

    stale_hidden = HIDDEN_CONDITIONS & picker
    assert not stale_hidden, (
        f"HIDDEN_CONDITIONS entries are now in the picker — remove them: {sorted(stale_hidden)}"
    )


# Conditions that need the "our team" perspective (a subscribed team plus its
# opponent). Event channels are positional, so these are the ONLY conditions
# the event picker may withhold — see #521, where the event branch silently
# dropped the whole common game-state group as well.
TEAM_ONLY_CONDITIONS = {
    "is_home",
    "is_away",
    "opponent_name_contains",
    "win_streak",
    "loss_streak",
    "is_ranked",
    "is_ranked_opponent",
}


def test_event_picker_withholds_only_team_perspective_conditions():
    """The event list must be the team list minus TEAM_ONLY_CONDITIONS exactly.

    Regression guard for #521: event contexts are built home-team-first, so
    every non-perspective condition evaluates on event templates and belongs
    in their picker. Dropping a group makes shipped starter templates use
    conditions users cannot select.
    """
    team = {c["name"] for c in get_conditions("team")["conditions"]}
    event = {c["name"] for c in get_conditions("event")["conditions"]}

    assert TEAM_ONLY_CONDITIONS <= team, (
        f"team picker lost perspective conditions: {sorted(TEAM_ONLY_CONDITIONS - team)}"
    )
    assert event == team - TEAM_ONLY_CONDITIONS, (
        f"event picker missing: {sorted((team - TEAM_ONLY_CONDITIONS) - event)} · "
        f"unexpectedly present: {sorted(event & TEAM_ONLY_CONDITIONS)}"
    )
    # The group that went missing in #521, pinned by name.
    assert {"is_final", "is_not_final", "is_playoff", "has_odds"} <= event


# --- guarantee 4: every condition proves True and False ---------------------

# condition -> (value, context where it must be True, context where it must
# be False). False contexts all carry an event, so a False result exercises
# the evaluator method itself, never the no-event guard in evaluate().
CONDITION_CASES: dict[str, tuple[str | None, str, str]] = {
    "always": (None, "us_pro_rich", "no_event"),  # False only without an event
    "is_home": (None, "us_pro_rich", "us_pro_away_today"),
    "is_away": (None, "us_pro_away_today", "us_pro_rich"),
    "win_streak": ("2", "us_pro_rich", "us_pro_away_today"),
    "loss_streak": ("2", "us_pro_away_today", "us_pro_rich"),
    "is_ranked": (None, "college", "us_pro_rich"),
    "is_ranked_opponent": (None, "college", "us_pro_rich"),
    "is_ranked_matchup": (None, "college", "us_pro_rich"),
    "is_top_ten_matchup": (None, "college", "us_pro_rich"),
    "is_conference_game": (None, "college", "us_pro_rich"),
    "is_playoff": (None, "us_pro_playoff", "us_pro_rich"),
    "is_preseason": (None, "us_pro_away_today", "us_pro_rich"),
    "is_national_broadcast": (None, "us_pro_rich", "soccer"),
    "has_odds": (None, "us_pro_rich", "sparse"),
    "is_final": (None, "us_pro_final", "us_pro_rich"),
    "is_not_final": (None, "us_pro_rich", "us_pro_final"),
    "has_recap": (None, "us_pro_rich", "sparse"),
    "has_preview": (None, "us_pro_rich", "sparse"),
    "has_generated_preview": (None, "us_pro_rich", "incomplete_matchup"),
    "has_structured_preview": (None, "us_pro_rich", "sparse"),
    "has_event_note": (None, "us_pro_rich", "sparse"),
    "has_match_note": (None, "soccer", "us_pro_rich"),
    "is_neutral_site": (None, "us_pro_away_today", "us_pro_rich"),
    "opponent_name_contains": ("Pistons", "us_pro_rich", "college"),
    "league_is": ("nba", "us_pro_rich", "soccer"),
    "sport_is": ("basketball", "us_pro_rich", "combat_pre"),
    "is_knockout": (None, "combat_ko", "combat_decision"),
    "is_submission": (None, "combat_submission", "combat_ko"),
    "is_decision": (None, "combat_decision", "combat_ko"),
    "is_finish": (None, "combat_ko", "combat_decision"),
    "went_distance": (None, "combat_decision", "combat_submission"),
    "is_race_session": (None, "racing_race_final", "racing_qualifying"),
    "is_qualifying_session": (None, "racing_qualifying", "racing_race_final"),
    "has_results": (None, "racing_race_final", "racing_race_pre"),
}


def test_condition_case_matrix_is_complete():
    """A new evaluator without a True/False case fails here — coverage of the
    matrix itself is what makes guarantee 4 registry-driven."""
    missing = _evaluator_conditions() - set(CONDITION_CASES)
    assert not missing, f"conditions missing from CONDITION_CASES: {sorted(missing)}"
    unknown = set(CONDITION_CASES) - _evaluator_conditions()
    assert not unknown, f"CONDITION_CASES has unknown conditions: {sorted(unknown)}"


@pytest.mark.parametrize("condition", sorted(CONDITION_CASES))
def test_condition_evaluates_true_and_false(condition):
    """True in the curated True-context and False in the curated
    False-context — an unknown/renamed condition (default-False dispatch)
    fails the True half; an always-True stub fails the False half."""
    value, true_ctx_name, false_ctx_name = CONDITION_CASES[condition]
    evaluator = ConditionEvaluator()

    true_ctx = CONTEXTS[true_ctx_name]()
    assert evaluator.evaluate(condition, value, true_ctx, true_ctx.game_context), (
        f"{condition!r} should be True in context {true_ctx_name}"
    )

    false_ctx = CONTEXTS[false_ctx_name]()
    assert not evaluator.evaluate(condition, value, false_ctx, false_ctx.game_context), (
        f"{condition!r} should be False in context {false_ctx_name}"
    )
