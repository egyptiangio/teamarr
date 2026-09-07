"""Self-healing for stale stream_match_cache rows missing short_name.

Reproduces issue #201: a user's stream_match_cache held cached_event_data
written before short_name flowed end-to-end (Team has name='Philadelphia
Phillies' but short_name=''). Variables like {home_team_short} resolved
blank for those events.

The fix: TeamMatcher._reconstruct_event treats a row with name set but
short_name empty as stale and returns None, forcing the matcher to
re-fetch and re-cache with proper data on the next pass.
"""

from teamarr.consumers.matching.team_matcher import TeamMatcher


def _make_cached(home_short: str, away_short: str) -> dict:
    """Build a cached_event_data dict like asdict(event) would."""
    return {
        "id": "401815214",
        "provider": "espn",
        "name": "Athletics at Philadelphia Phillies",
        "short_name": "ATH @ PHI",
        "start_time": "2026-05-05T22:40:00+00:00",
        "league": "mlb",
        "sport": "baseball",
        "status": {"state": "scheduled"},
        "home_team": {
            "id": "22",
            "provider": "espn",
            "name": "Philadelphia Phillies",
            "short_name": home_short,
            "abbreviation": "PHI",
            "league": "mlb",
            "sport": "baseball",
        },
        "away_team": {
            "id": "11",
            "provider": "espn",
            "name": "Athletics",
            "short_name": away_short,
            "abbreviation": "ATH",
            "league": "mlb",
            "sport": "baseball",
        },
        "broadcasts": [],
    }


def _matcher() -> TeamMatcher:
    """Build a bare TeamMatcher to call _reconstruct_event in isolation."""
    return TeamMatcher.__new__(TeamMatcher)


class TestReconstructInvalidatesStaleShortName:
    def test_fresh_row_is_reconstructed(self):
        """Properly-populated row reconstructs cleanly."""
        event = _matcher()._reconstruct_event(_make_cached("Phillies", "Athletics"))
        assert event is not None
        assert event.home_team.short_name == "Phillies"
        assert event.away_team.short_name == "Athletics"

    def test_stale_home_short_name_returns_none(self):
        """Home team with name but no short_name → cache miss."""
        event = _matcher()._reconstruct_event(_make_cached("", "Athletics"))
        assert event is None

    def test_stale_away_short_name_returns_none(self):
        """Away team with name but no short_name → cache miss."""
        event = _matcher()._reconstruct_event(_make_cached("Phillies", ""))
        assert event is None

    def test_both_empty_name_and_short_name_is_kept(self):
        """A team with no name AND no short_name is a placeholder, not stale."""
        cached = _make_cached("", "Athletics")
        cached["home_team"]["name"] = ""  # full placeholder team
        event = _matcher()._reconstruct_event(cached)
        assert event is not None
        assert event.home_team.name == ""
        assert event.home_team.short_name == ""

    def test_generated_preview_fields_survive_match_cache(self):
        """A cached match must retain the facts used by generated prose."""
        cached = _make_cached("Phillies", "Athletics")
        cached.update(
            {
                "away_team_record": "72-59",
                "home_probable_starter": "Tyler Phillips (3-6, 3.67 ERA)",
                "away_home_runs_leader": "Willson Contreras — 26 home runs",
            }
        )

        event = _matcher()._reconstruct_event(cached)

        assert event is not None
        assert event.away_team_record == "72-59"
        assert event.home_probable_starter == "Tyler Phillips (3-6, 3.67 ERA)"
        assert event.away_home_runs_leader == "Willson Contreras — 26 home runs"
