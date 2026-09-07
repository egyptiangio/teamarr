"""The candidate loop skips work it can prove is pointless (#742).

Profiling one 451-stream source showed 743,000 scored (stream, event) pairs, and
most of the per-pair cost going to candidates that could never match:

  * ``_score_teams_against_event`` computed all four side scores before the
    ``min()``/``max()`` could reject the pair, even when team1 matched neither
    side of the event and no value of team2 could rescue it.
  * ``is_event_in_search_window`` was re-decided per stream, though it depends
    only on the event and the group's target_date.

Both are pure work-avoidance. These tests pin the part that matters — that the
verdicts are unchanged — rather than the speed.
"""

from datetime import UTC, datetime, timedelta

import pytest

from teamarr.consumers.matching.constants import BOTH_TEAMS_THRESHOLD, MATCH_WINDOW_DAYS
from teamarr.consumers.matching.result import FailedReason, ResultCategory
from teamarr.core.types import Event, EventStatus, Team
from tests.fakes import make_team_matcher

TODAY = datetime.now(UTC).date()


def _team(name, short, abbr, league="mlb", sport="baseball"):
    return Team(
        id=name.lower().replace(" ", "-"), provider="espn", name=name,
        short_name=short, abbreviation=abbr, league=league, sport=sport,
    )


def _event(home, away, eid="1", days_offset=0):
    start = datetime.combine(TODAY, datetime.min.time(), tzinfo=UTC).replace(hour=23)
    return Event(
        id=eid, provider="espn", name=f"{away.name} at {home.name}",
        short_name=f"{away.short_name} at {home.short_name}",
        start_time=start + timedelta(days=days_offset),
        home_team=home, away_team=away, status=EventStatus(state="scheduled"),
        league=home.league, sport=home.sport,
    )


RAYS = _team("Tampa Bay Rays", "Rays", "TB")
TIGERS = _team("Detroit Tigers", "Tigers", "DET")
METS = _team("New York Mets", "Mets", "NYM")
YANKEES = _team("New York Yankees", "Yankees", "NYY")


class TestShortCircuitEquivalence:
    """best = max(min(t1h,t2a), min(t1a,t2h)) <= max(t1h,t1a) — so team1 gates."""

    @pytest.fixture
    def matcher(self):
        return make_team_matcher()

    def test_both_teams_matching_still_scores(self, matcher):
        got = matcher._score_teams_against_event(
            "Detroit Tigers", "Tampa Bay Rays", _event(RAYS, TIGERS)
        )
        assert got is not None
        assert got[1] >= BOTH_TEAMS_THRESHOLD

    def test_team1_matching_neither_side_returns_none(self, matcher):
        """The short-circuit's own path: team2 is a perfect match, team1 is not."""
        got = matcher._score_teams_against_event(
            "Manchester United", "Tampa Bay Rays", _event(RAYS, TIGERS)
        )
        assert got is None

    def test_team2_matching_neither_side_still_returns_none(self, matcher):
        """The symmetric case must be unaffected — it is decided after the gate."""
        got = matcher._score_teams_against_event(
            "Tampa Bay Rays", "Manchester United", _event(RAYS, TIGERS)
        )
        assert got is None

    def test_neither_team_matches(self, matcher):
        got = matcher._score_teams_against_event(
            "Manchester United", "Real Madrid", _event(RAYS, TIGERS)
        )
        assert got is None

    def test_both_teams_on_the_same_side_is_still_rejected(self, matcher):
        """min() requires the two stream sides to land on DIFFERENT event teams."""
        got = matcher._score_teams_against_event(
            "Tampa Bay Rays", "Tampa Bay Rays", _event(RAYS, TIGERS)
        )
        assert got is None

    def test_swapped_home_away_scores_the_same(self, matcher):
        """Option 1 vs option 2 — the gate must not privilege one assignment."""
        a = matcher._score_teams_against_event("Detroit Tigers", "Tampa Bay Rays",
                                               _event(RAYS, TIGERS))
        b = matcher._score_teams_against_event("Tampa Bay Rays", "Detroit Tigers",
                                               _event(RAYS, TIGERS))
        assert a is not None and b is not None
        assert a[1] == b[1]

    @pytest.mark.parametrize(
        "t1,t2,expect_match",
        [
            ("Tampa Bay Rays", "Detroit Tigers", True),
            ("Rays", "Tigers", True),
            ("New York Mets", "New York Yankees", False),   # neither plays here
            ("Tampa Bay Rays", "New York Mets", False),      # only one side lands
            ("New York Mets", "Detroit Tigers", False),      # only one side lands
        ],
    )
    def test_verdicts_across_the_gate(self, matcher, t1, t2, expect_match):
        got = matcher._score_teams_against_event(t1, t2, _event(RAYS, TIGERS))
        assert (got is not None) is expect_match

    def test_a_pair_needing_both_sides_is_not_lost(self, matcher):
        """A genuine match where team1 clears only ONE side must still bind.

        This is the case a careless short-circuit breaks: gating on "team1 beat
        both sides" instead of "team1 beat either side" would reject it.
        """
        got = matcher._score_teams_against_event(
            "Mets", "Yankees", _event(METS, YANKEES)
        )
        assert got is not None


class TestWindowGateHoist:
    """Hoisting the window check must not change which candidates are gated."""

    @pytest.fixture
    def matcher(self):
        return make_team_matcher()

    def _ctx(self, matcher):
        from zoneinfo import ZoneInfo

        from teamarr.consumers.matching.classifier import classify_stream
        from teamarr.consumers.matching.team_matcher import MatchContext

        name = "Tampa Bay Rays vs Detroit Tigers"
        classified = classify_stream(name)
        return MatchContext(
            stream_name=name,
            stream_id=1,
            group_id=1,
            target_date=TODAY,
            generation=1,
            user_tz=ZoneInfo("UTC"),
            classified=classified,
            team1=classified.team1,
            team2=classified.team2,
        )

    def test_event_inside_the_window_still_matches(self, matcher):
        ctx = self._ctx(matcher)
        out = matcher._match_against_events(ctx, [_event(RAYS, TIGERS)], "mlb")
        assert out.category is ResultCategory.MATCHED

    def test_event_older_than_the_window_is_gated(self, matcher):
        ctx = self._ctx(matcher)
        stale = _event(RAYS, TIGERS, days_offset=-(MATCH_WINDOW_DAYS + 5))
        out = matcher._match_against_events(ctx, [stale], "mlb")
        assert out.category is ResultCategory.FAILED
        # Specifically gated, not merely unmatched — otherwise this test would
        # keep passing if the window filter disappeared entirely.
        assert out.failed_reason is FailedReason.CANDIDATES_GATED

    def test_a_gated_candidate_does_not_hide_a_good_one(self, matcher):
        """Mixed list: the hoisted filter must drop only the out-of-window event."""
        ctx = self._ctx(matcher)
        stale = _event(RAYS, TIGERS, eid="old", days_offset=-(MATCH_WINDOW_DAYS + 5))
        fresh = _event(RAYS, TIGERS, eid="new")
        out = matcher._match_against_events(ctx, [stale, fresh], "mlb")
        assert out.category is ResultCategory.MATCHED
        assert out.event.id == "new"
