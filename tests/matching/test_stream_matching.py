"""Tests for stream-to-event matching: abbreviation token matching.

Validates that tournament/international streams using 3-letter country codes
(IOC codes) match correctly via exact abbreviation token matching, without
introducing false positives from similar abbreviations.
"""

from datetime import UTC, datetime

import pytest

from teamarr.consumers.matching.classifier import classify_stream
from teamarr.consumers.matching.result import MatchMethod
from teamarr.core.types import Event, EventStatus, Team

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_team(
    name: str,
    abbreviation: str,
    short_name: str = "",
) -> Team:
    """Create a minimal Team for testing."""
    return Team(
        id="t-" + abbreviation.lower(),
        provider="espn",
        name=name,
        short_name=short_name or name,
        abbreviation=abbreviation,
        league="test",
        sport="hockey",
    )


def _make_event(home: Team, away: Team) -> Event:
    """Create a minimal Event for testing."""
    return Event(
        id="evt-1",
        provider="espn",
        name=f"{home.name} vs {away.name}",
        short_name=f"{home.short_name} vs {away.short_name}",
        start_time=datetime(2026, 2, 11, 19, 0, tzinfo=UTC),
        home_team=home,
        away_team=away,
        status=EventStatus(state="scheduled"),
        league="test",
        sport="hockey",
    )


# ---------------------------------------------------------------------------
# Abbreviation token matching: _check_abbreviation_match
# ---------------------------------------------------------------------------


class TestCheckAbbreviationMatch:
    """Tests for _check_abbreviation_match via a lightweight TeamMatcher."""

    @pytest.fixture
    def matcher(self):
        """Create a TeamMatcher with no service/cache (only need the method)."""
        from tests.fakes import make_team_matcher

        m = make_team_matcher()
        m._fuzzy = None  # any accidental fuzzy fallback raises instead of matching
        return m

    # -- Positive cases: should match --

    def test_basic_ioc_codes(self, matcher):
        """SWE vs ITA → Sweden(SWE) vs Italy(ITA) → 100%."""
        home = _make_team("Sweden", "SWE")
        away = _make_team("Italy", "ITA")
        event = _make_event(home, away)

        result = matcher._check_abbreviation_match("SWE", "ITA", event)
        assert result is not None
        assert result == (MatchMethod.FUZZY, 100.0)

    def test_ioc_codes_with_parenthetical(self, matcher):
        """SWE vs ITA (M Group B) → Sweden(SWE) vs Italy(ITA) → 100%."""
        home = _make_team("Sweden", "SWE")
        away = _make_team("Italy", "ITA")
        event = _make_event(home, away)

        result = matcher._check_abbreviation_match("SWE", "ITA (M Group B)", event)
        assert result is not None
        assert result == (MatchMethod.FUZZY, 100.0)

    def test_can_usa(self, matcher):
        """CAN vs USA → Canada(CAN) vs United States(USA) → 100%."""
        home = _make_team("Canada", "CAN")
        away = _make_team("United States", "USA")
        event = _make_event(home, away)

        result = matcher._check_abbreviation_match("CAN", "USA", event)
        assert result is not None
        assert result == (MatchMethod.FUZZY, 100.0)

    def test_us_sport_abbreviations(self, matcher):
        """DEN vs PHI → Denver(DEN) vs Philadelphia(PHI) → 100%."""
        home = _make_team("Denver Nuggets", "DEN")
        away = _make_team("Philadelphia 76ers", "PHI")
        event = _make_event(home, away)

        result = matcher._check_abbreviation_match("DEN", "PHI", event)
        assert result is not None
        assert result == (MatchMethod.FUZZY, 100.0)

    def test_reversed_order(self, matcher):
        """Stream teams in opposite order to event teams still match."""
        home = _make_team("Sweden", "SWE")
        away = _make_team("Italy", "ITA")
        event = _make_event(home, away)

        # Stream has away team first
        result = matcher._check_abbreviation_match("ITA", "SWE", event)
        assert result is not None
        assert result == (MatchMethod.FUZZY, 100.0)

    def test_single_team_match(self, matcher):
        """Single team with abbreviation token should match."""
        home = _make_team("Sweden", "SWE")
        away = _make_team("Italy", "ITA")
        event = _make_event(home, away)

        result = matcher._check_abbreviation_match("SWE", None, event)
        assert result is not None
        assert result == (MatchMethod.FUZZY, 100.0)

    def test_single_team2_match(self, matcher):
        """Single team2 with abbreviation token should match."""
        home = _make_team("Sweden", "SWE")
        away = _make_team("Italy", "ITA")
        event = _make_event(home, away)

        result = matcher._check_abbreviation_match(None, "ITA", event)
        assert result is not None
        assert result == (MatchMethod.FUZZY, 100.0)

    # -- Negative cases: should NOT match --

    def test_no_match_similar_abbreviations(self, matcher):
        """DEN vs PHI should NOT match Detroit(DET) vs Chicago(CHI)."""
        home = _make_team("Detroit Pistons", "DET")
        away = _make_team("Chicago Bulls", "CHI")
        event = _make_event(home, away)

        result = matcher._check_abbreviation_match("DEN", "PHI", event)
        assert result is None

    def test_full_names_dont_trigger_abbr_match(self, matcher):
        """Boston Celtics vs LA Lakers should NOT match via abbreviation tokens."""
        home = _make_team("Boston Celtics", "BOS")
        away = _make_team("Los Angeles Lakers", "LAL")
        event = _make_event(home, away)

        # "BOS" is not a token in "Boston Celtics", "LAL" is not in "LA Lakers"
        result = matcher._check_abbreviation_match("Boston Celtics", "LA Lakers", event)
        assert result is None

    def test_two_letter_abbreviations_match_when_both_present(self, matcher):
        """2-letter codes match in the both-teams branch (#472) — MLB's
        official codes (SF, SD, KC, TB) are 2 letters, and requiring both
        stream teams to hit different event abbreviations kills the noise
        the old >=3 guard existed for."""
        home = _make_team("San Francisco 49ers", "SF")
        away = _make_team("New England Patriots", "NE")
        event = _make_event(home, away)

        result = matcher._check_abbreviation_match("SF", "NE", event)
        assert result == (MatchMethod.FUZZY, 100.0)

    def test_mixed_length_abbreviations_match(self, matcher):
        home = _make_team("Kansas City Chiefs", "KC")
        away = _make_team("Denver Broncos", "DEN")
        event = _make_event(home, away)

        result = matcher._check_abbreviation_match("KC", "DEN", event)
        assert result == (MatchMethod.FUZZY, 100.0)

    def test_single_team_two_letter_code_still_skipped(self, matcher):
        """A LONE 2-letter token stays unmatchable — genuinely noise-prone (#472)."""
        home = _make_team("Kansas City Chiefs", "KC")
        away = _make_team("Denver Broncos", "DEN")
        event = _make_event(home, away)

        result = matcher._check_abbreviation_match("KC", None, event)
        assert result is None

    def test_empty_abbreviations(self, matcher):
        """Events with empty abbreviations should return None."""
        home = _make_team("Some Team", "")
        away = _make_team("Other Team", "")
        event = _make_event(home, away)

        result = matcher._check_abbreviation_match("SWE", "ITA", event)
        assert result is None

    def test_no_teams_provided(self, matcher):
        """Both team1 and team2 are None → None."""
        home = _make_team("Sweden", "SWE")
        away = _make_team("Italy", "ITA")
        event = _make_event(home, away)

        result = matcher._check_abbreviation_match(None, None, event)
        assert result is None

    def test_only_one_team_matches_with_both_provided(self, matcher):
        """When both teams are provided, BOTH must match — partial match returns None."""
        home = _make_team("Sweden", "SWE")
        away = _make_team("Italy", "ITA")
        event = _make_event(home, away)

        # "SWE" matches but "FIN" doesn't match either abbreviation
        result = matcher._check_abbreviation_match("SWE", "FIN", event)
        assert result is None

    def test_stopword_abbreviation_single_team_does_not_match(self, matcher):
        """Stop words like 'THE', 'FOR', 'AND' must never match as single-team abbreviations (#705).
        """
        home = _make_team("Brockport Golden Eagles", "THE")
        away = _make_team("Buffalo State Bengals", "BUF")
        event = _make_event(home, away)

        # Lone stop word code
        assert matcher._check_abbreviation_match("THE", None, event) is None
        assert matcher._check_abbreviation_match(None, "THE", event) is None

        # Stop word in multi-token stream names
        assert matcher._check_abbreviation_match("The Golics", None, event) is None
        assert matcher._check_abbreviation_match("RedZone at the US Open", None, event) is None
        assert matcher._check_abbreviation_match(None, "the movies", event) is None

        # Fordham (FOR) and Anderson (AND)
        for_event = _make_event(
            _make_team("Fordham Rams", "FOR"),
            _make_team("Colgate Raiders", "COLG"),
        )
        assert matcher._check_abbreviation_match("NFL Live for Thursday", None, for_event) is None
        assert matcher._check_abbreviation_match("FOR", None, for_event) is None

        and_event = _make_event(
            _make_team("Anderson Trojans", "AND"),
            _make_team("Furman Paladins", "FUR"),
        )
        assert matcher._check_abbreviation_match("Highlights and Analysis", None, and_event) is None
        assert matcher._check_abbreviation_match("AND", None, and_event) is None

    def test_stopword_abbreviation_in_multitoken_matchup_does_not_match(self, matcher):
        """In two-team matchups, stop word abbreviations cannot match in multi-word phrases (#705).
        """
        home = _make_team("Brockport Golden Eagles", "THE")
        away = _make_team("Buffalo State Bengals", "BUF")
        event = _make_event(home, away)

        # 'BUF' matches Buffalo State, but 'THE MOVIES' contains 'the' as a stop word, not Brockport
        assert matcher._check_abbreviation_match("BUF", "THE MOVIES", event) is None
        assert matcher._check_abbreviation_match("THE MOVIES", "BUF", event) is None

    def test_case_insensitive(self, matcher):
        """Matching should be case-insensitive (normalize_text lowercases)."""
        home = _make_team("Sweden", "SWE")
        away = _make_team("Italy", "ITA")
        event = _make_event(home, away)

        result = matcher._check_abbreviation_match("swe", "ita", event)
        assert result is not None
        assert result == (MatchMethod.FUZZY, 100.0)


# ---------------------------------------------------------------------------
# Integration: _match_teams_to_event calls abbreviation check first
# ---------------------------------------------------------------------------


class TestMatchTeamsToEventAbbreviationIntegration:
    """Verify _match_teams_to_event tries abbreviation match before fuzzy."""

    @pytest.fixture
    def matcher(self):
        from tests.fakes import make_team_matcher

        return make_team_matcher()

    def test_abbreviation_beats_fuzzy_for_tournament_stream(self, matcher):
        """Tournament stream with IOC codes should get 100% via abbreviation path."""
        home = _make_team("Sweden", "SWE")
        away = _make_team("Italy", "ITA")
        event = _make_event(home, away)

        result = matcher._match_teams_to_event("SWE", "ITA", event)
        assert result is not None
        method, score = result
        assert score == 100.0

    def test_full_name_matching_still_works(self, matcher):
        """Full team names still match via the fuzzy fallback path."""
        home = _make_team("Boston Celtics", "BOS")
        away = _make_team("Los Angeles Lakers", "LAL")
        event = _make_event(home, away)

        result = matcher._match_teams_to_event("Boston Celtics", "Los Angeles Lakers", event)
        assert result is not None
        _method, score = result
        assert score >= 60.0  # Should pass BOTH_TEAMS_THRESHOLD

    def test_similar_abbrevs_no_false_positive(self, matcher):
        """DEN/PHI stream should NOT match DET/CHI event (no abbreviation or fuzzy match)."""
        home = _make_team("Detroit Pistons", "DET")
        away = _make_team("Chicago Bulls", "CHI")
        event = _make_event(home, away)

        result = matcher._match_teams_to_event("DEN", "PHI", event)
        assert result is None

    def test_rejects_low_score_side_with_no_shared_team_token(self, matcher):
        """Barrie Colts must not bind to Erie Otters on character similarity alone."""
        classified = classify_stream(
            "OHL  01 : 6:00 PM Kitchener Rangers @ Barrie Colts [1080p]"
        )
        event = _make_event(
            _make_team("Kitchener Rangers", "KIT"),
            _make_team("Erie Otters", "ER"),
        )

        assert classified.team1 == "OHL 01 :  Kitchener Rangers"
        assert classified.team2 == "Barrie Colts"
        assert matcher._match_teams_to_event(classified.team1, classified.team2, event) is None

    def test_brockport_the_stopword_does_not_match_random_streams(self, matcher):
        """ESPN abbreviation 'THE' for Brockport Golden Eagles must not match random streams (#705).
        """
        home = _make_team("Brockport Golden Eagles", "THE")
        away = _make_team("Buffalo State Bengals", "BUF")
        event = _make_event(home, away)

        # Single-team or unseparated streams containing "the"
        assert matcher._match_teams_to_event("The Golics", None, event) is None
        assert (
            matcher._match_teams_to_event(
                "RedZone at the US Open Presented by American Express", None, event
            )
            is None
        )
        assert matcher._match_teams_to_event(None, "the movies", event) is None
        assert matcher._match_teams_to_event("THE", None, event) is None

        # Two-team streams with a stop word in a phrase
        assert matcher._match_teams_to_event("BUF", "THE MOVIES", event) is None
