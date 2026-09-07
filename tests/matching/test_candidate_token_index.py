"""Candidate narrowing by inverted token index (#747, flag-gated).

The index only ever removes candidates that share no word with the stream, and
such an event cannot clear a `token_set_ratio` floor — so narrowing must not
change a single verdict. Measured against a live install: 11 sources, 1,879
streams, identical result signatures with the flag off and on, 13.76s -> 2.07s.

These tests pin the parts that make that true, and the two design choices the
measurement forced:

  * `short_name` and `abbreviation` MUST be indexed. Full names alone lost 8.98%
    of real matches (abbreviation streams like "MORG vs. ASU").
  * The index must never narrow a category the safety measurement did not cover.
"""

from datetime import UTC, datetime

import pytest

from teamarr.consumers.matching.candidate_index import (
    CandidateTokenIndex,
    event_tokens,
    tokenize,
)
from teamarr.consumers.matching.classifier import classify_stream
from teamarr.consumers.matching.result import ResultCategory
from teamarr.consumers.matching.team_matcher import MatchContext
from teamarr.core.types import Event, EventStatus, Team
from tests.fakes import make_team_matcher

TODAY = datetime.now(UTC).date()


def _team(name, short, abbr, league="mlb", sport="baseball"):
    return Team(
        id=name.lower().replace(" ", "-"), provider="espn", name=name,
        short_name=short, abbreviation=abbr, league=league, sport=sport,
    )


def _event(home, away, eid="1", league=None):
    start = datetime.combine(TODAY, datetime.min.time(), tzinfo=UTC).replace(hour=23)
    return Event(
        id=eid, provider="espn", name=f"{away.name} at {home.name}",
        short_name=f"{away.short_name} at {home.short_name}", start_time=start,
        home_team=home, away_team=away, status=EventStatus(state="scheduled"),
        league=league or home.league, sport=home.sport,
    )


RAYS = _team("Tampa Bay Rays", "Rays", "TB")
TIGERS = _team("Detroit Tigers", "Tigers", "DET")
METS = _team("New York Mets", "Mets", "NYM")
YANKEES = _team("New York Yankees", "Yankees", "NYY")
ASU = _team("Arizona State Sun Devils", "Arizona St", "ASU", "college-football", "football")
MORGAN = _team("Morgan State Bears", "Morgan St", "MORG", "college-football", "football")


class TestTokenize:
    def test_drops_short_tokens_and_stopwords(self):
        assert tokenize("Rays vs TB at the Stadium") == {"rays", "stadium"}

    def test_empty_text_is_empty(self):
        assert tokenize("") == set()
        assert tokenize(None) == set()

    def test_normalizes_like_the_scorer_does(self):
        assert "montreal" in tokenize("Montréal Canadiens")


class TestEventTokens:
    def test_indexes_full_names(self):
        toks = event_tokens(_event(RAYS, TIGERS))
        assert {"tampa", "rays", "detroit", "tigers"} <= toks

    def test_indexes_abbreviations(self):
        """Load-bearing: full names alone lost 8.98% of real matches."""
        toks = event_tokens(_event(ASU, MORGAN))
        assert "asu" in toks
        assert "morg" in toks

    def test_indexes_short_names(self):
        assert "arizona" in event_tokens(_event(ASU, MORGAN))


class TestIndex:
    def _candidates(self):
        return (
            ("mlb", _event(RAYS, TIGERS, "e1")),
            ("mlb", _event(METS, YANKEES, "e2")),
            ("college-football", _event(ASU, MORGAN, "e3")),
        )

    def test_narrows_to_events_sharing_a_word(self):
        cands = self._candidates()
        got = CandidateTokenIndex(cands).narrow({"rays", "tigers"}, cands)
        assert [e.id for _lg, e in got] == ["e1"]

    def test_abbreviation_stream_still_finds_its_event(self):
        cands = self._candidates()
        got = CandidateTokenIndex(cands).narrow(tokenize("MORG vs. ASU"), cands)
        assert [e.id for _lg, e in got] == ["e3"]

    def test_no_shared_word_yields_an_empty_list_not_none(self):
        """An empty list is a real answer; None means 'cannot speak'."""
        cands = self._candidates()
        assert CandidateTokenIndex(cands).narrow({"liverpool"}, cands) == []

    def test_no_tokens_defers(self):
        cands = self._candidates()
        assert CandidateTokenIndex(cands).narrow(set(), cands) is None

    def test_refuses_a_sequence_it_was_not_built_over(self):
        """Positions are meaningless against another list — must not narrow it."""
        cands = self._candidates()
        other = (cands[2],)
        assert CandidateTokenIndex(cands).narrow({"rays"}, other) is None

    def test_preserves_candidate_order(self):
        """Equal-score ranking reads sequence order, so a reshuffle can flip ties."""
        cands = self._candidates()
        got = CandidateTokenIndex(cands).narrow({"new", "york", "tampa"}, cands)
        assert [e.id for _lg, e in got] == ["e1", "e2"]


def _ctx(name, category_source=None):
    classified = classify_stream(category_source or name)
    from zoneinfo import ZoneInfo

    return MatchContext(
        stream_name=name, stream_id=1, group_id=1, target_date=TODAY, generation=1,
        user_tz=ZoneInfo("UTC"), classified=classified,
        team1=classified.team1, team2=classified.team2,
    )


class TestFlagAndScope:
    @pytest.fixture
    def matcher(self):
        return make_team_matcher()

    def _cands(self):
        return (
            ("mlb", _event(RAYS, TIGERS, "e1")),
            ("mlb", _event(METS, YANKEES, "e2")),
        )

    def test_disabled_by_default(self, matcher, monkeypatch):
        monkeypatch.delenv("TEAMARR_TOKEN_INDEX", raising=False)
        cands = self._cands()
        assert matcher._narrow_candidates(_ctx("Tampa Bay Rays vs Detroit Tigers"), cands) is cands

    def test_enabled_by_the_flag(self, matcher, monkeypatch):
        monkeypatch.setenv("TEAMARR_TOKEN_INDEX", "1")
        cands = self._cands()
        got = matcher._narrow_candidates(_ctx("Tampa Bay Rays vs Detroit Tigers"), cands)
        assert [e.id for _lg, e in got] == ["e1"]

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
    def test_falsey_flag_values_stay_off(self, matcher, monkeypatch, value):
        monkeypatch.setenv("TEAMARR_TOKEN_INDEX", value)
        cands = self._cands()
        assert matcher._narrow_candidates(_ctx("Tampa Bay Rays vs Detroit Tigers"), cands) is cands

    def test_a_plain_list_is_never_narrowed(self, matcher, monkeypatch):
        """Per-stream single-league lists have no stable identity to index on."""
        monkeypatch.setenv("TEAMARR_TOKEN_INDEX", "1")
        cands = list(self._cands())
        assert matcher._narrow_candidates(_ctx("Tampa Bay Rays vs Detroit Tigers"), cands) is cands

    def test_index_is_built_once_per_candidate_tuple(self, matcher, monkeypatch):
        """Rebuilding per stream would cost more than the scan it replaces."""
        monkeypatch.setenv("TEAMARR_TOKEN_INDEX", "1")
        cands = self._cands()
        for _ in range(5):
            matcher._narrow_candidates(_ctx("Tampa Bay Rays vs Detroit Tigers"), cands)
        assert len(matcher._token_index_memo) == 1


class TestVerdictsUnchanged:
    """Narrowing must not change a match, only how fast it is reached."""

    @pytest.fixture
    def matcher(self):
        return make_team_matcher()

    def _run(self, matcher, name):
        cands = (
            ("mlb", _event(RAYS, TIGERS, "e1")),
            ("mlb", _event(METS, YANKEES, "e2")),
            ("college-football", _event(ASU, MORGAN, "e3")),
        )
        return matcher._match_against_candidates(_ctx(name), cands)

    @pytest.mark.parametrize(
        "name",
        [
            "Tampa Bay Rays vs Detroit Tigers",
            "New York Mets vs New York Yankees",
            "Liverpool vs Everton",           # matches nothing either way
        ],
    )
    def test_same_verdict_with_flag_on_and_off(self, matcher, monkeypatch, name):
        monkeypatch.delenv("TEAMARR_TOKEN_INDEX", raising=False)
        off = self._run(matcher, name)
        monkeypatch.setenv("TEAMARR_TOKEN_INDEX", "1")
        on = self._run(matcher, name)

        assert off.category is on.category
        assert (off.event and off.event.id) == (on.event and on.event.id)
        assert off.failed_reason is on.failed_reason

    def test_a_match_is_still_found_through_the_index(self, matcher, monkeypatch):
        monkeypatch.setenv("TEAMARR_TOKEN_INDEX", "1")
        out = self._run(matcher, "Tampa Bay Rays vs Detroit Tigers")
        assert out.category is ResultCategory.MATCHED
        assert out.event.id == "e1"
