"""Serialization helpers for caching dataclass objects.

Used by SportsDataService to serialize Event, Team, TeamStats to/from
JSON for storage in PersistentTTLCache (SQLite-backed).
"""

from datetime import datetime

from teamarr.core import GENERATED_PREVIEW_FIELDS, Event, EventStatus, Team, TeamStats, Venue
from teamarr.core.types import Bout, RacingResult, RacingSession


def event_to_dict(event: Event) -> dict:
    """Serialize Event to dict for JSON storage."""
    # Serialize segment_times (datetime values to ISO strings)
    segment_times_dict = None
    if event.segment_times:
        segment_times_dict = {seg: dt.isoformat() for seg, dt in event.segment_times.items()}

    return {
        "id": event.id,
        "provider": event.provider,
        "name": event.name,
        "short_name": event.short_name,
        "start_time": event.start_time.isoformat(),
        "home_team": team_to_dict(event.home_team),
        "away_team": team_to_dict(event.away_team),
        "status": {
            "state": event.status.state,
            "detail": event.status.detail,
            "period": event.status.period,
            "clock": event.status.clock,
        },
        "league": event.league,
        "sport": event.sport,
        "home_score": event.home_score,
        "away_score": event.away_score,
        "venue": venue_to_dict(event.venue) if event.venue else None,
        "broadcasts": event.broadcasts,
        "season_year": event.season_year,
        "season_type": event.season_type,
        "neutral_site": event.neutral_site,
        "broadcast_markets": event.broadcast_markets,
        # Editorial/context copy — the has_recap/has_preview/has_event_note/
        # has_match_note conditions and their template vars read these; a
        # cache hit must not silently drop them (#363, #365).
        "game_recap": event.game_recap,
        "game_event_note": event.game_event_note,
        "soccer_match_note": event.soccer_match_note,
        "game_preview": event.game_preview,
        **{field_name: getattr(event, field_name) for field_name in GENERATED_PREVIEW_FIELDS},
        # Betting odds — the has_odds condition and odds vars read this; a
        # cache hit must not silently drop it (#366).
        "odds_data": event.odds_data,
        # UFC-specific fields
        "segment_times": segment_times_dict,
        "main_card_start": event.main_card_start.isoformat() if event.main_card_start else None,
        # Combat result fields — the is_knockout/is_submission/is_decision/
        # is_finish/went_distance conditions and result vars read these (#366).
        "bouts": [bout_to_dict(b) for b in event.bouts],
        "fight_result_method": event.fight_result_method,
        "finish_round": event.finish_round,
        "finish_time": event.finish_time,
        "weight_class": event.weight_class,
        "fighter1_scores": event.fighter1_scores,
        "fighter2_scores": event.fighter2_scores,
        # Racing-specific fields
        "circuit_name": event.circuit_name,
        "sessions": [racing_session_to_dict(s) for s in event.sessions],
        "race_laps": event.race_laps,
        "race_distance_miles": event.race_distance_miles,
        "stage_laps": event.stage_laps,
        # Tennis-specific fields
        "tournament_id": event.tournament_id,
        "tournament_name": event.tournament_name,
        "round_name": event.round_name,
        "court": event.court,
        "draw_type": event.draw_type,
        "is_major": event.is_major,
    }


def bout_to_dict(bout: Bout) -> dict:
    """Serialize Bout to dict."""
    return {
        "fighter1": bout.fighter1,
        "fighter2": bout.fighter2,
        "segment": bout.segment,
        "order": bout.order,
    }


def dict_to_bout(data: dict) -> Bout:
    """Deserialize dict to Bout."""
    return Bout(
        fighter1=data["fighter1"],
        fighter2=data["fighter2"],
        segment=data.get("segment", "main_card"),
        order=data.get("order", 0),
    )


def racing_session_to_dict(session: RacingSession) -> dict:
    """Serialize RacingSession to dict."""
    return {
        "code": session.code,
        "name": session.name,
        "start_time": session.start_time.isoformat(),
        "results": [racing_result_to_dict(r) for r in session.results],
    }


def racing_result_to_dict(result: RacingResult) -> dict:
    """Serialize RacingResult to dict."""
    return {
        "driver_name": result.driver_name,
        "team_name": result.team_name,
        "position": result.position,
        "grid_position": result.grid_position,
        "points": result.points,
        "fastest_lap": result.fastest_lap,
        "status": result.status,
    }


def team_to_dict(team: Team) -> dict:
    """Serialize Team to dict."""
    return {
        "id": team.id,
        "provider": team.provider,
        "name": team.name,
        "short_name": team.short_name,
        "abbreviation": team.abbreviation,
        "league": team.league,
        "sport": team.sport,
        "logo_url": team.logo_url,
        "color": team.color,
    }


def venue_to_dict(venue: Venue) -> dict:
    """Serialize Venue to dict."""
    return {
        "name": venue.name,
        "city": venue.city,
        "state": venue.state,
        "country": venue.country,
    }


def dict_to_event(data: dict) -> Event:
    """Deserialize dict to Event."""
    # Deserialize segment_times (ISO strings to datetime)
    segment_times = None
    if data.get("segment_times"):
        segment_times = {
            seg: datetime.fromisoformat(dt_str) for seg, dt_str in data["segment_times"].items()
        }

    # Deserialize main_card_start
    main_card_start = None
    if data.get("main_card_start"):
        main_card_start = datetime.fromisoformat(data["main_card_start"])

    return Event(
        id=data["id"],
        provider=data["provider"],
        name=data["name"],
        short_name=data["short_name"],
        start_time=datetime.fromisoformat(data["start_time"]),
        home_team=dict_to_team(data["home_team"]),
        away_team=dict_to_team(data["away_team"]),
        status=EventStatus(
            state=data["status"]["state"],
            detail=data["status"].get("detail"),
            period=data["status"].get("period"),
            clock=data["status"].get("clock"),
        ),
        league=data["league"],
        sport=data["sport"],
        home_score=data.get("home_score"),
        away_score=data.get("away_score"),
        venue=dict_to_venue(data["venue"]) if data.get("venue") else None,
        broadcasts=data.get("broadcasts", []),
        season_year=data.get("season_year"),
        season_type=data.get("season_type"),
        neutral_site=bool(data.get("neutral_site", False)),
        broadcast_markets=data.get("broadcast_markets") or {},
        # Editorial/context copy (#363, #365) — absent in pre-upgrade cache
        # entries, so default to empty.
        game_recap=data.get("game_recap", ""),
        game_event_note=data.get("game_event_note", ""),
        soccer_match_note=data.get("soccer_match_note", ""),
        game_preview=data.get("game_preview", ""),
        **{
            field_name: data.get(
                field_name, Event.__dataclass_fields__[field_name].default
            )
            for field_name in GENERATED_PREVIEW_FIELDS
        },
        # Betting odds (#366) — absent in pre-upgrade cache entries
        odds_data=data.get("odds_data"),
        # UFC-specific fields
        segment_times=segment_times or {},
        main_card_start=main_card_start,
        # Combat result fields (#366) — absent in pre-upgrade cache entries
        bouts=[dict_to_bout(b) for b in data.get("bouts", [])],
        fight_result_method=data.get("fight_result_method"),
        finish_round=data.get("finish_round"),
        finish_time=data.get("finish_time"),
        weight_class=data.get("weight_class"),
        fighter1_scores=data.get("fighter1_scores"),
        fighter2_scores=data.get("fighter2_scores"),
        # Racing-specific fields
        circuit_name=data.get("circuit_name"),
        sessions=[dict_to_racing_session(s) for s in data.get("sessions", [])],
        race_laps=data.get("race_laps"),
        race_distance_miles=data.get("race_distance_miles"),
        stage_laps=data.get("stage_laps") or [],
        # Tennis-specific fields
        tournament_id=data.get("tournament_id"),
        tournament_name=data.get("tournament_name"),
        round_name=data.get("round_name"),
        court=data.get("court"),
        draw_type=data.get("draw_type"),
        is_major=bool(data.get("is_major", False)),
    )


def dict_to_racing_session(data: dict) -> RacingSession:
    """Deserialize dict to RacingSession."""
    return RacingSession(
        code=data["code"],
        name=data["name"],
        start_time=datetime.fromisoformat(data["start_time"]),
        results=[dict_to_racing_result(r) for r in data.get("results", [])],
    )


def dict_to_racing_result(data: dict) -> RacingResult:
    """Deserialize dict to RacingResult."""
    return RacingResult(
        driver_name=data["driver_name"],
        team_name=data.get("team_name"),
        position=data.get("position"),
        grid_position=data.get("grid_position"),
        points=data.get("points"),
        fastest_lap=data.get("fastest_lap", False),
        status=data.get("status"),
    )


def dict_to_team(data: dict) -> Team:
    """Deserialize dict to Team."""
    return Team(
        id=data["id"],
        provider=data["provider"],
        name=data["name"],
        short_name=data["short_name"],
        abbreviation=data["abbreviation"],
        league=data["league"],
        sport=data["sport"],
        logo_url=data.get("logo_url"),
        color=data.get("color"),
    )


def dict_to_venue(data: dict) -> Venue:
    """Deserialize dict to Venue."""
    return Venue(
        name=data["name"],
        city=data.get("city"),
        state=data.get("state"),
        country=data.get("country"),
    )


def stats_to_dict(stats: TeamStats) -> dict:
    """Serialize TeamStats to dict."""
    return {
        "record": stats.record,
        "wins": stats.wins,
        "losses": stats.losses,
        "ties": stats.ties,
        "home_record": stats.home_record,
        "away_record": stats.away_record,
        "streak": stats.streak,
        "streak_count": stats.streak_count,
        "rank": stats.rank,
        "playoff_seed": stats.playoff_seed,
        "games_back": stats.games_back,
        "conference": stats.conference,
        "conference_abbrev": stats.conference_abbrev,
        "division": stats.division,
        "ppg": stats.ppg,
        "papg": stats.papg,
    }


def dict_to_stats(data: dict) -> TeamStats:
    """Deserialize dict to TeamStats."""
    return TeamStats(
        record=data["record"],
        wins=data.get("wins", 0),
        losses=data.get("losses", 0),
        ties=data.get("ties", 0),
        home_record=data.get("home_record"),
        away_record=data.get("away_record"),
        streak=data.get("streak"),
        streak_count=data.get("streak_count", 0),
        rank=data.get("rank"),
        playoff_seed=data.get("playoff_seed"),
        games_back=data.get("games_back"),
        conference=data.get("conference"),
        conference_abbrev=data.get("conference_abbrev"),
        division=data.get("division"),
        ppg=data.get("ppg"),
        papg=data.get("papg"),
    )
