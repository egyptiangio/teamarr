"""Tests for feed team separation feature.

Covers:
1. Literal HOME/AWAY detection + stripping in classifier
2. Team name detection post-match
3. Channel discrimination (feed_team_id in lookup)
4. Feed label generation (team_name, short_name, home_away styles)
5. Settings disabled = no detection
"""

from dataclasses import dataclass

import pytest

from teamarr.consumers.matching.classifier import (
    StreamCategory,
    classify_stream,
    detect_and_strip_feed_hint,
)

# ===========================================================================
# Phase 1: Literal token detection in classifier
# ===========================================================================


class TestDetectAndStripFeedHint:
    """detect_and_strip_feed_hint() strips HOME/AWAY tokens."""

    def test_home_detected(self):
        text, hint = detect_and_strip_feed_hint("NHL HOME", ["HOME"], ["AWAY"])
        assert hint == "home"
        assert "HOME" not in text

    def test_away_detected(self):
        text, hint = detect_and_strip_feed_hint("NHL AWAY", ["HOME"], ["AWAY"])
        assert hint == "away"
        assert "AWAY" not in text

    def test_no_match(self):
        text, hint = detect_and_strip_feed_hint("NHL Regular", ["HOME"], ["AWAY"])
        assert hint is None
        assert text == "NHL Regular"

    def test_case_insensitive(self):
        text, hint = detect_and_strip_feed_hint("nhl home feed", ["HOME"], ["AWAY"])
        assert hint == "home"
        assert "home" not in text.lower() or "home" not in text

    def test_custom_terms(self):
        text, hint = detect_and_strip_feed_hint("MLB LOCAL", ["LOCAL", "HOME"], ["VISITOR", "AWAY"])
        assert hint == "home"

    def test_word_boundary(self):
        """HOMER shouldn't match HOME."""
        text, hint = detect_and_strip_feed_hint("HOMER Simpson", ["HOME"], ["AWAY"])
        assert hint is None

    def test_empty_terms(self):
        text, hint = detect_and_strip_feed_hint("NHL HOME", [], [])
        assert hint is None

    def test_cleaned_text_no_double_spaces(self):
        text, hint = detect_and_strip_feed_hint("NHL HOME Feed", ["HOME"], ["AWAY"])
        assert hint == "home"
        assert "  " not in text


class TestClassifyStreamFeedHint:
    """classify_stream() propagates feed_hint to ClassifiedStream."""

    def test_feed_hint_on_team_vs_team(self):
        result = classify_stream(
            "Rangers vs Devils HOME",
            feed_home_terms=["HOME"],
            feed_away_terms=["AWAY"],
        )
        assert result.feed_hint == "home"
        assert result.category == StreamCategory.TEAM_VS_TEAM

    def test_feed_hint_away(self):
        result = classify_stream(
            "Rangers vs Devils AWAY",
            feed_home_terms=["HOME"],
            feed_away_terms=["AWAY"],
        )
        assert result.feed_hint == "away"

    def test_no_feed_hint_when_no_terms(self):
        result = classify_stream("Rangers vs Devils HOME")
        assert result.feed_hint is None

    def test_no_feed_hint_when_disabled(self):
        """No feed terms = feature disabled."""
        result = classify_stream(
            "Rangers vs Devils HOME",
            feed_home_terms=None,
            feed_away_terms=None,
        )
        assert result.feed_hint is None

    def test_feed_hint_on_placeholder(self):
        result = classify_stream(
            "HOME Feed",
            feed_home_terms=["HOME"],
            feed_away_terms=["AWAY"],
        )
        # Even if it becomes placeholder, feed_hint should be set
        assert result.feed_hint == "home"

    def test_home_stripped_before_team_matching(self):
        """HOME token should be stripped so it doesn't interfere with matching."""
        result = classify_stream(
            "Rangers vs Devils HOME",
            feed_home_terms=["HOME"],
            feed_away_terms=["AWAY"],
        )
        # Should still parse teams correctly with HOME stripped
        assert result.team1 is not None
        assert result.team2 is not None


# ===========================================================================
# Phase 2: Team name detection
# ===========================================================================


@dataclass(frozen=True)
class MockTeam:
    id: str
    provider: str
    name: str
    short_name: str
    abbreviation: str
    league: str
    sport: str
    logo_url: str | None = None
    color: str | None = None
    record_summary: str | None = None


class TestDetectTeamInStreamName:
    """_detect_team_in_stream_name() matches team identity."""

    @pytest.fixture
    def home_team(self):
        return MockTeam(
            id="1",
            provider="espn",
            name="Baltimore Orioles",
            short_name="Orioles",
            abbreviation="BAL",
            league="mlb",
            sport="baseball",
        )

    @pytest.fixture
    def away_team(self):
        return MockTeam(
            id="2",
            provider="espn",
            name="New York Yankees",
            short_name="Yankees",
            abbreviation="NYY",
            league="mlb",
            sport="baseball",
        )

    def test_full_name_match(self, home_team, away_team):
        from teamarr.consumers.event_group_processor import EventGroupProcessor

        result = EventGroupProcessor._detect_team_in_stream_name(
            "baltimore orioles feed", home_team, away_team
        )
        assert result == home_team

    def test_short_name_match(self, home_team, away_team):
        from teamarr.consumers.event_group_processor import EventGroupProcessor

        result = EventGroupProcessor._detect_team_in_stream_name(
            "orioles feed", home_team, away_team
        )
        assert result == home_team

    def test_abbreviation_match(self, home_team, away_team):
        from teamarr.consumers.event_group_processor import EventGroupProcessor

        result = EventGroupProcessor._detect_team_in_stream_name("bal feed", home_team, away_team)
        assert result == home_team

    def test_away_team_match(self, home_team, away_team):
        from teamarr.consumers.event_group_processor import EventGroupProcessor

        result = EventGroupProcessor._detect_team_in_stream_name(
            "yankees broadcast", home_team, away_team
        )
        assert result == away_team

    def test_no_match(self, home_team, away_team):
        from teamarr.consumers.event_group_processor import EventGroupProcessor

        result = EventGroupProcessor._detect_team_in_stream_name(
            "generic sports feed", home_team, away_team
        )
        assert result is None

    def test_short_abbreviation_skipped(self):
        """Abbreviations < 3 chars should be skipped (too many false positives)."""
        from teamarr.consumers.event_group_processor import EventGroupProcessor

        team = MockTeam(
            id="1",
            provider="espn",
            name="Golden State Warriors",
            short_name="Warriors",
            abbreviation="GS",
            league="nba",
            sport="basketball",
        )
        other = MockTeam(
            id="2",
            provider="espn",
            name="Los Angeles Lakers",
            short_name="Lakers",
            abbreviation="LAL",
            league="nba",
            sport="basketball",
        )
        # "GS" is only 2 chars, should not match
        result = EventGroupProcessor._detect_team_in_stream_name("gs broadcast", team, other)
        assert result is None

    def test_feed_keyword_before_matchup_not_team_specific(self, home_team, away_team):
        """A feed keyword preceding BOTH teams is a shared matchup feed (#234).

        "feed orioles yankees" must not be attributed to the Orioles just
        because "feed orioles" matches — the opposing team appears after.
        """
        from teamarr.consumers.event_group_processor import EventGroupProcessor

        result = EventGroupProcessor._detect_team_in_stream_name(
            "feed orioles yankees", home_team, away_team
        )
        assert result is None

    def test_team_specific_feed_still_matches_with_opponent_before(self, home_team, away_team):
        """Opponent BEFORE the keyword is fine — still the home team's feed (#234)."""
        from teamarr.consumers.event_group_processor import EventGroupProcessor

        result = EventGroupProcessor._detect_team_in_stream_name(
            "yankees at orioles feed", home_team, away_team
        )
        assert result == home_team

    def test_team_branded_channel_token(self, home_team, away_team):
        """'Yankees.TV' is a team-specific feed even with no feed keyword (#343)."""
        from teamarr.consumers.event_group_processor import EventGroupProcessor

        result = EventGroupProcessor._detect_team_in_stream_name(
            "mlb 04: yankees @ orioles (yankees.tv)", home_team, away_team
        )
        assert result == away_team

    def test_team_branded_channel_token_variants(self, home_team, away_team):
        """Spaced and run-together forms count too: 'Yankees TV', 'YankeesTV'."""
        from teamarr.consumers.event_group_processor import EventGroupProcessor

        for variant in ("yankees @ orioles yankees tv", "yankees @ orioles (yankeestv)"):
            result = EventGroupProcessor._detect_team_in_stream_name(
                variant, home_team, away_team
            )
            assert result == away_team, variant

    def test_team_branded_domain_suffix(self, home_team, away_team):
        """Whitelisted domain-style tokens count: 'Yankees.US', 'Yankees.Live'."""
        from teamarr.consumers.event_group_processor import EventGroupProcessor

        for variant in (
            "yankees @ orioles (yankees.us)",
            "yankees @ orioles yankees.live",
        ):
            result = EventGroupProcessor._detect_team_in_stream_name(
                variant, home_team, away_team
            )
            assert result == away_team, variant

    def test_dot_separated_stream_name_not_a_feed(self, home_team, away_team):
        """Dot-separated provider naming ('MLB.Yankees.Orioles.720p') must not
        read 'Yankees.Orioles' as a domain token — TLDs are whitelisted."""
        from teamarr.consumers.event_group_processor import EventGroupProcessor

        result = EventGroupProcessor._detect_team_in_stream_name(
            "mlb.yankees.orioles.720p", home_team, away_team
        )
        assert result is None


# ===========================================================================
# Broadcast-market feed detection (#343)
# ===========================================================================


class TestDetectFeedFromBroadcastMarkets:
    """_detect_feed_from_broadcast_markets() maps ESPN broadcasts[].market
    names found in the stream name to that side's team."""

    @pytest.fixture
    def event(self):
        @dataclass
        class MockEvent:
            home_team: object
            away_team: object
            broadcast_markets: dict

        return MockEvent(
            home_team=MockTeam(
                id="1", provider="espn", name="Chicago Cubs", short_name="Cubs",
                abbreviation="CHC", league="mlb", sport="baseball",
            ),
            away_team=MockTeam(
                id="2", provider="espn", name="Milwaukee Brewers", short_name="Brewers",
                abbreviation="MIL", league="mlb", sport="baseball",
            ),
            broadcast_markets={
                "MLB.TV": "national",
                "Brewers.TV": "away",
                "Marquee Sports Network": "home",
            },
        )

    def _detect(self, stream_name, event):
        from teamarr.consumers.event_group_processor import EventGroupProcessor

        return EventGroupProcessor._detect_feed_from_broadcast_markets(
            stream_name.lower(), event
        )

    def test_away_market_name_matches(self, event):
        """The #343 report: 'Brewers.TV' resolves to the away feed."""
        assert self._detect("MLB 04: MIL @ CHC (Brewers.TV)", event) == event.away_team

    def test_home_market_name_matches(self, event):
        """Regional networks with no team token resolve via market too."""
        assert self._detect("MIL @ CHC Marquee Sports Network", event) == event.home_team

    def test_national_market_never_makes_a_feed(self, event):
        assert self._detect("MIL @ CHC MLB.TV", event) is None

    def test_both_sides_matching_is_ambiguous(self, event):
        assert self._detect("Brewers.TV / Marquee Sports Network combo", event) is None

    def test_no_broadcast_data(self, event):
        event.broadcast_markets = {}
        assert self._detect("MIL @ CHC (Brewers.TV)", event) is None

    def test_short_names_skipped(self, event):
        """Names under 3 chars are skipped (false-positive guard)."""
        event.broadcast_markets = {"TV": "away"}
        assert self._detect("some tv stream", event) is None

    # -- fuzzy tiers (#343 follow-up): streams rarely quote the listing --

    def test_punctuation_variant_matches(self, event):
        """'Brewers.TV' listed, stream says 'BREWERS TV'."""
        assert self._detect("MLB 04: MIL @ CHC BREWERS TV", event) == event.away_team

    def test_run_together_variant_matches(self, event):
        """'Brewers.TV' listed, stream says 'BrewersTV'."""
        assert self._detect("MLB 04: MIL @ CHC (BrewersTV)", event) == event.away_team

    def test_abbreviated_multiword_name_fuzzy_matches(self, event):
        """'Bally Sports Wisconsin' listed, stream says 'Bally Sports WI'."""
        event.broadcast_markets = {"Bally Sports Wisconsin": "away"}
        assert self._detect("MIL @ CHC | Bally Sports WI", event) == event.away_team

    def test_short_single_token_stays_exact(self, event):
        """'YES' must not fuzzy-match into unrelated words."""
        event.broadcast_markets = {"YES": "home"}
        assert self._detect("yesterday replay: MIL @ CHC", event) is None
        assert self._detect("MIL @ CHC on YES", event) == event.home_team

    def test_matchup_team_name_alone_does_not_match(self, event):
        """'Brewers.TV' listed: a plain matchup title mentioning the Brewers
        (no channel token) must not become a team feed."""
        assert self._detect("Milwaukee Brewers @ Chicago Cubs", event) is None


# ===========================================================================
# Feed label generation
# ===========================================================================


class TestBuildFeedLabel:
    """_build_feed_label() generates correct labels per style."""

    @pytest.fixture
    def home_team(self):
        return MockTeam(
            id="1",
            provider="espn",
            name="Baltimore Orioles",
            short_name="Orioles",
            abbreviation="BAL",
            league="mlb",
            sport="baseball",
        )

    @pytest.fixture
    def event(self, home_team):
        """Minimal event-like object."""

        @dataclass
        class MockEvent:
            home_team: object
            away_team: object

        return MockEvent(
            home_team=home_team,
            away_team=MockTeam(
                id="2",
                provider="espn",
                name="New York Yankees",
                short_name="Yankees",
                abbreviation="NYY",
                league="mlb",
                sport="baseball",
            ),
        )

    def test_team_name_style(self, home_team, event):
        from teamarr.consumers.lifecycle.service import ChannelLifecycleService

        label = ChannelLifecycleService._build_feed_label(home_team, event, "team_name")
        assert label == "Orioles Feed"

    def test_short_name_style(self, home_team, event):
        from teamarr.consumers.lifecycle.service import ChannelLifecycleService

        label = ChannelLifecycleService._build_feed_label(home_team, event, "short_name")
        assert label == "BAL Feed"

    def test_home_away_style_home(self, home_team, event):
        from teamarr.consumers.lifecycle.service import ChannelLifecycleService

        label = ChannelLifecycleService._build_feed_label(home_team, event, "home_away")
        assert label == "Home Feed"

    def test_home_away_style_away(self, event):
        from teamarr.consumers.lifecycle.service import ChannelLifecycleService

        label = ChannelLifecycleService._build_feed_label(event.away_team, event, "home_away")
        assert label == "Away Feed"


# ===========================================================================
# Auto-append gating (Bug 2: don't double up when template uses feed vars)
# ===========================================================================


class TestTemplateUsesFeedVar:
    """_template_uses_feed_var() detects feed variables to suppress auto-append."""

    def test_no_feed_var_returns_false(self):
        from teamarr.consumers.lifecycle.service import ChannelLifecycleService

        assert ChannelLifecycleService._template_uses_feed_var("{away_team} @ {home_team}") is False

    def test_feed_team_detected(self):
        from teamarr.consumers.lifecycle.service import ChannelLifecycleService

        assert (
            ChannelLifecycleService._template_uses_feed_var(
                "{away_team} @ {home_team} - {feed_team}"
            )
            is True
        )

    def test_feed_team_short_detected(self):
        from teamarr.consumers.lifecycle.service import ChannelLifecycleService

        assert (
            ChannelLifecycleService._template_uses_feed_var(
                "{home_team_abbrev} | {feed_team_short}"
            )
            is True
        )

    def test_feed_team_abbrev_detected(self):
        from teamarr.consumers.lifecycle.service import ChannelLifecycleService

        assert (
            ChannelLifecycleService._template_uses_feed_var("{home_team} ({feed_team_abbrev})")
            is True
        )

    def test_feed_home_away_detected(self):
        from teamarr.consumers.lifecycle.service import ChannelLifecycleService

        assert (
            ChannelLifecycleService._template_uses_feed_var("{home_team} - {feed_home_away}")
            is True
        )

    def test_feed_team_logo_not_a_naming_var(self):
        # logo URLs aren't naming output — auto-append should still fire so
        # a template that only uses {feed_team_logo} for icons still gets
        # the suffix in the channel name.
        from teamarr.consumers.lifecycle.service import ChannelLifecycleService

        assert (
            ChannelLifecycleService._template_uses_feed_var(
                "{away_team} @ {home_team}"  # no feed naming var
            )
            is False
        )

    def test_substring_does_not_match(self):
        # "{feed_teamish}" should not trigger — exact-token match only.
        from teamarr.consumers.lifecycle.service import ChannelLifecycleService

        assert ChannelLifecycleService._template_uses_feed_var("{feed_teamish}") is False


class TestFeedTemplateVarsConstant:
    """FEED_TEMPLATE_VARS is the source of truth for the gating list."""

    def test_constant_contents(self):
        from teamarr.consumers.lifecycle.naming import FEED_TEMPLATE_VARS

        # Naming-relevant feed vars only — logo URL and directional booleans
        # are deliberately excluded. feed_team_abbrev_lower is a retired
        # alias (#484) kept for templates that never got migrated.
        assert FEED_TEMPLATE_VARS == frozenset(
            {
                "feed_team",
                "feed_team_short",
                "feed_team_abbrev",
                "feed_team_abbrev_lower",
                "feed_home_away",
                "broadcast_feed",
                "broadcast_feed_team",
                "broadcast_feed_team_short",
                "broadcast_feed_team_abbrev",
            }
        )


# ===========================================================================
# Channel discrimination
# ===========================================================================


class TestFindExistingChannelFeedTeam:
    """find_existing_channel discriminates by feed_team_id."""

    @pytest.fixture
    def db(self):
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE managed_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_epg_group_id INTEGER,
                event_id TEXT NOT NULL,
                event_provider TEXT NOT NULL,
                tvg_id TEXT NOT NULL,
                channel_name TEXT NOT NULL,
                channel_number TEXT,
                logo_url TEXT,
                dispatcharr_channel_id INTEGER,
                dispatcharr_uuid TEXT,
                dispatcharr_logo_id INTEGER,
                channel_group_id INTEGER,
                channel_profile_ids TEXT,
                primary_stream_id INTEGER,
                exception_keyword TEXT,
                feed_team_id TEXT,
                home_team TEXT,
                home_team_abbrev TEXT,
                home_team_logo TEXT,
                away_team TEXT,
                away_team_abbrev TEXT,
                away_team_logo TEXT,
                event_date TIMESTAMP,
                event_name TEXT,
                league TEXT,
                sport TEXT,
                venue TEXT,
                broadcast TEXT,
                scheduled_delete_at TIMESTAMP,
                deleted_at TIMESTAMP,
                delete_reason TEXT,
                sync_status TEXT DEFAULT 'pending',
                sync_message TEXT,
                last_verified_at TIMESTAMP,
                expires_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        yield conn
        conn.close()

    def test_same_event_different_feed_teams(self, db):
        """Two channels for same event with different feed_team_id."""
        from teamarr.database.channels.crud import (
            create_managed_channel,
            find_existing_channel,
        )

        # Channel 1: home feed
        create_managed_channel(
            db,
            event_epg_group_id=1,
            event_id="evt1",
            event_provider="espn",
            tvg_id="t1",
            channel_name="Game (Home)",
            feed_team_id="team_home",
            primary_stream_id=100,
        )
        # Channel 2: away feed
        create_managed_channel(
            db,
            event_epg_group_id=1,
            event_id="evt1",
            event_provider="espn",
            tvg_id="t2",
            channel_name="Game (Away)",
            feed_team_id="team_away",
            primary_stream_id=200,
        )
        db.commit()

        # Look up home feed
        home = find_existing_channel(db, "evt1", "espn", feed_team_id="team_home")
        assert home is not None
        assert home.feed_team_id == "team_home"

        # Look up away feed
        away = find_existing_channel(db, "evt1", "espn", feed_team_id="team_away")
        assert away is not None
        assert away.feed_team_id == "team_away"

        # They should be different channels
        assert home.id != away.id

    def test_null_feed_team_separate_from_specific(self, db):
        """NULL feed_team_id (unlabeled) should not match specific feed_team_id."""
        from teamarr.database.channels.crud import (
            create_managed_channel,
            find_existing_channel,
        )

        # Channel without feed team (normal channel)
        create_managed_channel(
            db,
            event_epg_group_id=1,
            event_id="evt1",
            event_provider="espn",
            tvg_id="t1",
            channel_name="Game",
            primary_stream_id=100,
        )
        db.commit()

        # Looking up with specific feed_team_id should NOT find it
        result = find_existing_channel(db, "evt1", "espn", feed_team_id="team_home")
        assert result is None

        # Looking up with NULL feed_team_id should find it
        result = find_existing_channel(db, "evt1", "espn")
        assert result is not None


# ===========================================================================
# Settings integration
# ===========================================================================


class TestFeedSeparationSettings:
    """Feed separation settings read/write."""

    @pytest.fixture
    def db(self):
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE settings (
                id INTEGER PRIMARY KEY,
                schema_version INTEGER DEFAULT 71,
                feed_separation_enabled BOOLEAN DEFAULT 0,
                feed_home_terms JSON DEFAULT '["HOME"]',
                feed_away_terms JSON DEFAULT '["AWAY"]',
                feed_detect_team_names BOOLEAN DEFAULT 1,
                feed_label_style TEXT DEFAULT 'team_name'
            );
            INSERT INTO settings (id) VALUES (1);
        """)
        conn.commit()
        yield conn
        conn.close()

    def test_defaults(self, db):
        from teamarr.database.settings import get_feed_separation_settings

        settings = get_feed_separation_settings(db)
        assert settings.enabled is False
        assert settings.home_terms == ["HOME"]
        assert settings.away_terms == ["AWAY"]
        assert settings.detect_team_names is True
        assert settings.label_style == "team_name"

    def test_update_and_read(self, db):
        from teamarr.database.settings import (
            get_feed_separation_settings,
            update_feed_separation_settings,
        )

        update_feed_separation_settings(
            db,
            enabled=True,
            home_terms=["LOCAL", "HOME"],
            away_terms=["VISITOR"],
            detect_team_names=False,
            label_style="short_name",
        )
        db.commit()

        settings = get_feed_separation_settings(db)
        assert settings.enabled is True
        assert settings.home_terms == ["LOCAL", "HOME"]
        assert settings.away_terms == ["VISITOR"]
        assert settings.detect_team_names is False
        assert settings.label_style == "short_name"


# ===========================================================================
# Cross-cutting invariant: every consumer caller of generate_event_tvg_id
# must read feed_team from the matched_stream and pass its id, so live EPG
# programmes and filler programmes land on the same per-feed XMLTV channel.
# Regressed in v2.4.4 (filler path missed the new feed_team_id arg) — bead
# teamarrv2-eg9.
# ===========================================================================


class TestFillerChannelIdMatchesLiveProgramme:
    """Filler must emit to the same channel_id as live programmes for the
    same matched_stream — including the -feed-{id} suffix when feed
    separation resolved a feed_team."""

    def _make_processor(self):
        from unittest.mock import MagicMock, patch

        from teamarr.consumers.event_group_processor import EventGroupProcessor

        with patch("teamarr.consumers.event_group_processor.processor.create_default_service"):
            return EventGroupProcessor(db_factory=MagicMock())

    def _make_event(self, event_id="401815159", provider="espn"):
        from datetime import UTC, datetime

        from teamarr.core import Event, EventStatus, Team

        team = Team(
            id="t1",
            provider=provider,
            name="Pittsburgh Pirates",
            short_name="Pirates",
            abbreviation="PIT",
            league="mlb",
            sport="baseball",
        )
        return Event(
            id=event_id,
            provider=provider,
            name="CIN @ PIT",
            short_name="CIN @ PIT",
            start_time=datetime(2026, 5, 1, 23, 0, tzinfo=UTC),
            home_team=team,
            away_team=team,
            status=EventStatus(state="scheduled"),
            league="mlb",
            sport="baseball",
        )

    def test_filler_channel_id_includes_feed_team_id(self):
        """Pre-fix this test fails: filler emits to teamarr-event-401815159
        while live emits to teamarr-event-401815159-feed-18."""
        from unittest.mock import MagicMock, patch

        from teamarr.consumers.filler.event_filler import (
            EventFillerConfig,
            EventFillerResult,
        )

        processor = self._make_processor()
        event = self._make_event()
        feed_team = MagicMock()
        feed_team.id = "18"

        matched_streams = [
            {
                "event": event,
                "feed_team": feed_team,
                "_exception_keyword": None,
                "segment": None,
                "segment_start": None,
                "segment_end": None,
            }
        ]

        captured_channel_ids: list[str] = []

        def fake_generate_with_counts(**kwargs):
            captured_channel_ids.append(kwargs.get("channel_id"))
            return EventFillerResult(programmes=[], pregame_count=0, postgame_count=0)

        with patch("teamarr.consumers.event_group_processor.xmltv.EventFillerGenerator") as MockGen:
            MockGen.return_value.generate_with_counts = fake_generate_with_counts
            processor._generate_filler_for_streams(
                matched_streams=matched_streams,
                filler_config=EventFillerConfig(),
                sport_durations={"baseball": 3.0},
            )

        assert captured_channel_ids == ["teamarr-event-401815159-feed-18"]

    def test_filler_channel_id_no_feed_team(self):
        """Without feed separation (national broadcast), filler still emits
        to the base channel — matches live programme path."""
        from unittest.mock import patch

        from teamarr.consumers.filler.event_filler import (
            EventFillerConfig,
            EventFillerResult,
        )

        processor = self._make_processor()
        event = self._make_event(event_id="401815158")

        matched_streams = [
            {
                "event": event,
                "feed_team": None,
                "_exception_keyword": None,
                "segment": None,
                "segment_start": None,
                "segment_end": None,
            }
        ]

        captured_channel_ids: list[str] = []

        def fake_generate_with_counts(**kwargs):
            captured_channel_ids.append(kwargs.get("channel_id"))
            return EventFillerResult(programmes=[], pregame_count=0, postgame_count=0)

        with patch("teamarr.consumers.event_group_processor.xmltv.EventFillerGenerator") as MockGen:
            MockGen.return_value.generate_with_counts = fake_generate_with_counts
            processor._generate_filler_for_streams(
                matched_streams=matched_streams,
                filler_config=EventFillerConfig(),
                sport_durations={"baseball": 3.0},
            )

        assert captured_channel_ids == ["teamarr-event-401815158"]

    def test_filler_channel_id_matches_live_epg_path(self):
        """Cross-caller invariant: the channel_id the filler path computes
        for a matched_stream must equal what the live-EPG path computes for
        the same stream. This is the actual user-facing invariant and the
        one that broke in v2.4.4."""
        from teamarr.consumers.lifecycle import generate_event_tvg_id

        # Same stream, computed from both call sites' input shapes.
        feed_team_id = "18"
        live_channel_id = generate_event_tvg_id("401815159", "espn", None, None, feed_team_id)

        # Filler path: same args, must produce same id.
        filler_feed_team = type("FT", (), {"id": "18"})()
        filler_channel_id = generate_event_tvg_id(
            "401815159",
            "espn",
            None,
            None,
            filler_feed_team.id if filler_feed_team else None,
        )

        assert filler_channel_id == live_channel_id
        assert filler_channel_id == "teamarr-event-401815159-feed-18"


# ===========================================================================
# Identifier candidates for feed resolution: name, tvg-id, tvg-name (#489)
# ===========================================================================


class TestResolveFeedTeamsIdentifiers:
    """_resolve_feed_teams checks tvg-id/tvg-name when the display name
    gives no signal — a stream whose tvg-id is 'Brewers.TV' is the Brewers
    feed regardless of the name (#489)."""

    @pytest.fixture
    def event(self):
        @dataclass
        class MockEvent:
            home_team: object
            away_team: object
            broadcast_markets: dict

        return MockEvent(
            home_team=MockTeam(
                id="16",
                provider="espn",
                name="Chicago Cubs",
                short_name="Cubs",
                abbreviation="CHC",
                league="mlb",
                sport="baseball",
            ),
            away_team=MockTeam(
                id="158",
                provider="espn",
                name="Milwaukee Brewers",
                short_name="Brewers",
                abbreviation="MIL",
                league="mlb",
                sport="baseball",
            ),
            broadcast_markets={"Brewers.TV": "away", "Marquee Sports Network": "home"},
        )

    def _resolve(
        self,
        stream: dict,
        event,
        detect_team_names: bool = True,
        separation_enabled: bool = True,
    ):
        from teamarr.consumers.event_group_processor.matching import StreamMatching

        entry = {"stream": stream, "event": event, "feed_hint": None}
        StreamMatching._resolve_feed_teams(
            StreamMatching(), [entry], detect_team_names, separation_enabled
        )
        self._last_entry = entry
        return entry["feed_team"]

    def test_tvg_id_matches_broadcast_market(self, event):
        stream = {"name": "MIL @ CHC", "tvg_id": "Brewers.TV", "tvg_name": None}
        assert self._resolve(stream, event) is event.away_team

    def test_tvg_id_team_branded_token(self, event):
        # No broadcast-market hit ('Brewers.TV' removed) — the team-branded
        # channel token detector still recognizes the tvg-id.
        event.broadcast_markets = {}
        stream = {"name": "MIL @ CHC", "tvg_id": "Brewers.TV", "tvg_name": None}
        assert self._resolve(stream, event) is event.away_team

    def test_tvg_name_is_also_checked(self, event):
        stream = {"name": "MIL @ CHC", "tvg_id": None, "tvg_name": "Brewers.TV"}
        assert self._resolve(stream, event) is event.away_team

    def test_name_takes_precedence(self, event):
        # Display name resolves home; a conflicting tvg-id never gets consulted.
        stream = {
            "name": "MIL @ CHC on Marquee Sports Network",
            "tvg_id": "Brewers.TV",
            "tvg_name": None,
        }
        assert self._resolve(stream, event) is event.home_team

    def test_missing_identifier_keys_are_safe(self, event):
        stream = {"name": "MIL @ CHC"}
        assert self._resolve(stream, event) is None

    def test_detect_team_names_off_still_uses_broadcast_markets(self, event):
        stream = {"name": "MIL @ CHC", "tvg_id": "Brewers.TV", "tvg_name": None}
        assert self._resolve(stream, event, detect_team_names=False) is event.away_team

    # --- Identification decoupled from separation (#527) ---

    def test_separation_off_still_identifies_stream_feed_team(self, event):
        # The prioritization lever: with feed separation OFF, resolution still
        # runs and lands in stream_feed_team (→ persisted feed_team_id →
        # team_feed ordering rules), while the channel-splitting feed_team
        # key stays None so no feed-separated channels appear.
        stream = {"name": "MIL @ CHC", "tvg_id": "Brewers.TV", "tvg_name": None}
        assert self._resolve(stream, event, separation_enabled=False) is None
        assert self._last_entry["stream_feed_team"] is event.away_team

    def test_separation_on_populates_both_keys(self, event):
        stream = {"name": "MIL @ CHC", "tvg_id": "Brewers.TV", "tvg_name": None}
        assert self._resolve(stream, event, separation_enabled=True) is event.away_team
        assert self._last_entry["stream_feed_team"] is event.away_team

    def test_separation_off_team_branded_name_identifies(self, event):
        # FractalBoy's exact case (#527): 'Brewers.TV'-branded stream name,
        # no broadcast-market data, feed separation off.
        event.broadcast_markets = {}
        stream = {"name": "Brewers.TV", "tvg_id": None, "tvg_name": None}
        assert self._resolve(stream, event, separation_enabled=False) is None
        assert self._last_entry["stream_feed_team"] is event.away_team


# ===========================================================================
# TEAM_ONLY matched_side splits feed channels (#559)
# ===========================================================================


class TestResolveFeedTeamsMatchedSide:
    """A TEAM_ONLY match already proves which side's feed the stream is —
    the whole stream name is one team ('MLB | Miami Marlins'). With
    detect_team_names on, that matched_side splits the stream into its feed
    channel (#559); the context-regex detector, which requires feed-specific
    wording, never fires on bare team names by design (0b306e48)."""

    @pytest.fixture
    def event(self):
        @dataclass
        class MockEvent:
            home_team: object
            away_team: object
            broadcast_markets: dict

        return MockEvent(
            home_team=MockTeam(
                id="28",
                provider="espn",
                name="Miami Marlins",
                short_name="Marlins",
                abbreviation="MIA",
                league="mlb",
                sport="baseball",
            ),
            away_team=MockTeam(
                id="23",
                provider="espn",
                name="Pittsburgh Pirates",
                short_name="Pirates",
                abbreviation="PIT",
                league="mlb",
                sport="baseball",
            ),
            broadcast_markets={},
        )

    def _resolve(
        self,
        stream: dict,
        event,
        matched_side=None,
        feed_hint=None,
        detect_team_names: bool = True,
        separation_enabled: bool = True,
    ):
        from teamarr.consumers.event_group_processor.matching import StreamMatching

        entry = {
            "stream": stream,
            "event": event,
            "feed_hint": feed_hint,
            "matched_side": matched_side,
        }
        StreamMatching._resolve_feed_teams(
            StreamMatching(), [entry], detect_team_names, separation_enabled
        )
        self._last_entry = entry
        return entry["feed_team"]

    def test_matched_side_home_splits_to_home_feed(self, event):
        stream = {"name": "MLB | Miami Marlins"}
        assert self._resolve(stream, event, matched_side="home") is event.home_team

    def test_matched_side_away_splits_to_away_feed(self, event):
        stream = {"name": "US : MLB PITTSBURGH PIRATES"}
        assert self._resolve(stream, event, matched_side="away") is event.away_team

    def test_feed_hint_takes_precedence_over_matched_side(self, event):
        stream = {"name": "Miami Marlins HOME"}
        assert (
            self._resolve(stream, event, matched_side="away", feed_hint="home")
            is event.home_team
        )

    def test_matched_side_takes_precedence_over_broadcast_markets(self, event):
        # The match is a stronger signal than fuzzy market-name presence.
        event.broadcast_markets = {"Pirates TV": "away"}
        stream = {"name": "Pirates TV alternate"}
        assert self._resolve(stream, event, matched_side="home") is event.home_team

    def test_detect_team_names_off_ignores_matched_side(self, event):
        # The toggle means "team names are not a feed signal" — the split
        # stays off; per-stream persistence still happens in the creator's
        # matched_side fallback, not here.
        stream = {"name": "MLB | Miami Marlins"}
        assert (
            self._resolve(stream, event, matched_side="home", detect_team_names=False)
            is None
        )
        assert self._last_entry["stream_feed_team"] is None

    def test_separation_off_still_identifies_stream_feed_team(self, event):
        # Identification decoupled from separation (#527): channel-level
        # feed_team stays None, per-stream identity is still resolved.
        stream = {"name": "MLB | Miami Marlins"}
        assert (
            self._resolve(stream, event, matched_side="home", separation_enabled=False)
            is None
        )
        assert self._last_entry["stream_feed_team"] is event.home_team

    def test_matchup_stream_without_matched_side_never_splits(self, event):
        # Regression guard for the 0b306e48 false-positive fix: a
        # TEAM_VS_TEAM stream (no matched_side) whose name merely contains
        # team names stays a shared feed.
        stream = {"name": "Pittsburgh Pirates vs Miami Marlins"}
        assert self._resolve(stream, event, matched_side=None) is None


# ===========================================================================
# Reclaiming feed channels after the master toggle goes off (#672)
# ===========================================================================


class TestCleanupFeedSeparatedChannels:
    """_cleanup_feed_separated_channels un-splits after the toggle goes off.

    With separation off every lookup carries feed_team_id=None, so
    find_existing_channel constrains on `feed_team_id IS NULL` and never
    returns the rows that already carry a feed team — they were neither
    synced nor deleted, and sat beside a freshly created duplicate base
    channel until their scheduled deletion.
    """

    @staticmethod
    def _cleanup(channels, passed_event_ids, delete_ok=True):
        from unittest.mock import MagicMock, patch

        from teamarr.consumers.event_group_processor.matching import StreamMatching
        from tests.fakes import FakeGroup

        processor = StreamMatching()
        lifecycle = MagicMock()
        lifecycle.delete_managed_channel.return_value = delete_ok
        processor._get_lifecycle_service = lambda: lifecycle

        with patch(
            "teamarr.database.channels.get_managed_channels_for_group",
            return_value=channels,
        ):
            deleted = processor._cleanup_feed_separated_channels(
                FakeGroup(), MagicMock(), passed_event_ids
            )
        return deleted, lifecycle

    def test_deletes_feed_channels_for_matched_events(self):
        from tests.fakes import FakeChannel

        channels = [
            FakeChannel(id=1, event_id="401874913", feed_team_id=None),
            FakeChannel(id=2, event_id="401874913", feed_team_id="2"),
            FakeChannel(id=3, event_id="401874913", feed_team_id="10"),
        ]
        deleted, lifecycle = self._cleanup(channels, {"401874913"})
        assert deleted == 2
        deleted_ids = [c.args[1] for c in lifecycle.delete_managed_channel.call_args_list]
        assert deleted_ids == [2, 3]

    def test_base_channel_is_never_deleted(self):
        from tests.fakes import FakeChannel

        channels = [FakeChannel(id=1, event_id="401874913", feed_team_id=None)]
        deleted, lifecycle = self._cleanup(channels, {"401874913"})
        assert deleted == 0
        lifecycle.delete_managed_channel.assert_not_called()

    def test_unmatched_event_keeps_its_feed_channel(self):
        # No base channel would be created for it this run, so dropping the
        # feed channel would take a live broadcast off the air. It falls to
        # its normal end-of-event deletion instead.
        from tests.fakes import FakeChannel

        channels = [FakeChannel(id=2, event_id="401816721", feed_team_id="19")]
        deleted, lifecycle = self._cleanup(channels, {"401874913"})
        assert deleted == 0
        lifecycle.delete_managed_channel.assert_not_called()

    def test_segment_aware_event_ids_match(self):
        # Channel identity stores the segment-aware id, and so does
        # passed_event_ids — a segmented feed channel must still be reclaimed.
        from tests.fakes import FakeChannel

        channels = [FakeChannel(id=2, event_id="600053-prelims", feed_team_id="2")]
        deleted, _ = self._cleanup(channels, {"600053-prelims"})
        assert deleted == 1

    def test_empty_passed_set_is_a_noop(self):
        from tests.fakes import FakeChannel

        channels = [FakeChannel(id=2, event_id="401874913", feed_team_id="2")]
        deleted, lifecycle = self._cleanup(channels, set())
        assert deleted == 0
        lifecycle.delete_managed_channel.assert_not_called()

    def test_failed_deletion_is_not_counted(self):
        from tests.fakes import FakeChannel

        channels = [FakeChannel(id=2, event_id="401874913", feed_team_id="2")]
        deleted, _ = self._cleanup(channels, {"401874913"}, delete_ok=False)
        assert deleted == 0

    def test_deletion_reason_is_recorded(self):
        from tests.fakes import FakeChannel

        channels = [FakeChannel(id=2, event_id="401874913", feed_team_id="2")]
        _, lifecycle = self._cleanup(channels, {"401874913"})
        assert (
            lifecycle.delete_managed_channel.call_args.kwargs["reason"]
            == "feed_separation_disabled"
        )


class TestCleanupPassEligibility:
    """What the reclaim sweep is told is still eligible to be split (#672, #732).

    The sweep now runs every pass; the eligible-event set is what protects the
    channels separation just created. Passing an event id in that set is the
    only thing standing between a live feed channel and deletion, so these
    tests pin the set itself rather than whether the call happened.
    """

    @staticmethod
    def _run_pipeline(
        db_conn, db_factory, monkeypatch, *, separation_enabled, separation_sports=None
    ):
        from datetime import date
        from unittest.mock import MagicMock

        from teamarr.consumers.channel_lifecycle import StreamProcessResult
        from teamarr.consumers.event_group_processor import EventGroupProcessor
        from teamarr.consumers.matching.matcher import BatchMatchResult
        from teamarr.database.groups import create_group, get_group
        from teamarr.database.settings import update_feed_separation_settings
        from teamarr.services.stream_filter import FilterResult
        from tests.fakes import make_event

        cur = db_conn.execute(
            "INSERT INTO templates (name, template_type) VALUES ('T', 'event')"
        )
        db_conn.execute(
            "INSERT INTO subscription_templates (template_id) VALUES (?)", (cur.lastrowid,)
        )
        gid = create_group(db_conn, name="G", leagues=["nfl"])
        update_feed_separation_settings(
            db_conn, enabled=separation_enabled, sports=separation_sports
        )
        db_conn.commit()
        group = get_group(db_conn, gid)

        proc = EventGroupProcessor(db_factory=db_factory, service=MagicMock())
        streams = [{"id": 11, "name": "Team A vs Team B"}]
        event = make_event(
            home_team=MockTeam(
                id="1",
                provider="espn",
                name="Team A",
                short_name="A",
                abbreviation="TA",
                league="nfl",
                sport="football",
            ),
            away_team=MockTeam(
                id="2",
                provider="espn",
                name="Team B",
                short_name="B",
                abbreviation="TB",
                league="nfl",
                sport="football",
            ),
        )
        monkeypatch.setattr(proc, "_fetch_streams", lambda g: list(streams))
        monkeypatch.setattr(
            proc,
            "_filter_streams",
            lambda s, g: (list(streams), FilterResult(total_input=1, passed_count=1)),
        )
        monkeypatch.setattr(proc, "_match_streams", lambda *a, **k: BatchMatchResult())
        monkeypatch.setattr(
            proc,
            "_build_matched_stream_list",
            lambda *a, **k: [{"stream": streams[0], "event": event}],
        )
        monkeypatch.setattr(proc, "_enrich_matched_events", lambda ms: ms)
        reached: list = []
        monkeypatch.setattr(
            proc,
            "_process_channels",
            lambda *a, **k: reached.append("_process_channels") or StreamProcessResult(),
        )
        monkeypatch.setattr(proc, "_generate_xmltv", lambda *a, **k: ("", 0, 0, 0, 0))

        calls: list = []
        monkeypatch.setattr(
            proc,
            "_cleanup_feed_separated_channels",
            lambda g, conn, passed, separated=None: calls.append((passed, separated)) or 0,
        )

        result = proc._process_group_internal(db_conn, group, date(2026, 3, 1))
        # Guard the guard: an exception upstream would also produce zero calls.
        assert result.errors == []
        assert reached == ["_process_channels"]
        return calls

    def test_nothing_eligible_when_separation_is_off(
        self, db_conn, db_factory, monkeypatch
    ):
        calls = self._run_pipeline(
            db_conn, db_factory, monkeypatch, separation_enabled=False
        )
        assert calls == [({"123"}, set())]

    def test_everything_eligible_when_separation_is_on(
        self, db_conn, db_factory, monkeypatch
    ):
        # The sweep still runs — it is simply a no-op, because every event that
        # passed the filter is also still eligible to be split.
        calls = self._run_pipeline(
            db_conn, db_factory, monkeypatch, separation_enabled=True
        )
        assert calls == [({"123"}, {"123"})]

    def test_event_in_a_selected_sport_stays_eligible(
        self, db_conn, db_factory, monkeypatch
    ):
        calls = self._run_pipeline(
            db_conn,
            db_factory,
            monkeypatch,
            separation_enabled=True,
            separation_sports=["football"],
        )
        assert calls == [({"123"}, {"123"})]

    def test_event_outside_the_selected_sports_loses_eligibility(
        self, db_conn, db_factory, monkeypatch
    ):
        # The football event is no longer eligible, so its feed channels are
        # handed to the sweep for reclaim — the #672 bug in #732 clothing.
        calls = self._run_pipeline(
            db_conn,
            db_factory,
            monkeypatch,
            separation_enabled=True,
            separation_sports=["baseball"],
        )
        assert calls == [({"123"}, set())]


class TestPerSportScope:
    """feed_separation.sports narrows which sports get split (#732)."""

    @staticmethod
    def _event(sport: str | None):
        from dataclasses import dataclass

        @dataclass
        class MockEvent:
            home_team: object
            away_team: object
            broadcast_markets: dict

        @dataclass
        class SportedEvent(MockEvent):
            sport: str

        team_kwargs = {"provider": "espn", "league": "mlb", "sport": sport or "baseball"}
        home = MockTeam(
            id="28", name="Miami Marlins", short_name="Marlins",
            abbreviation="MIA", **team_kwargs,
        )
        away = MockTeam(
            id="23", name="Pittsburgh Pirates", short_name="Pirates",
            abbreviation="PIT", **team_kwargs,
        )
        if sport is None:
            return MockEvent(home_team=home, away_team=away, broadcast_markets={})
        return SportedEvent(
            home_team=home, away_team=away, broadcast_markets={}, sport=sport
        )

    # --- _separation_applies ---------------------------------------------

    def test_empty_list_means_every_sport(self):
        from teamarr.consumers.event_group_processor.matching import _separation_applies

        assert _separation_applies(self._event("baseball"), []) is True
        assert _separation_applies(self._event("baseball"), None) is True

    def test_listed_sport_applies(self):
        from teamarr.consumers.event_group_processor.matching import _separation_applies

        assert _separation_applies(self._event("baseball"), ["baseball", "hockey"]) is True

    def test_unlisted_sport_does_not_apply(self):
        from teamarr.consumers.event_group_processor.matching import _separation_applies

        assert _separation_applies(self._event("racing"), ["baseball"]) is False

    def test_event_without_a_sport_is_never_excluded(self):
        # The list can only speak about sports; not knowing the sport means it
        # has nothing to say, so the master toggle stands. Excluding here would
        # silently un-split channels whenever a provider omitted the field.
        from teamarr.consumers.event_group_processor.matching import _separation_applies

        assert _separation_applies(self._event(None), ["baseball"]) is True
        assert _separation_applies(None, ["baseball"]) is True

    # --- the gate in _resolve_feed_teams ---------------------------------

    @staticmethod
    def _resolve(event, separation_sports):
        from teamarr.consumers.event_group_processor.matching import StreamMatching

        entry = {
            "stream": {"name": "MLB | Miami Marlins"},
            "event": event,
            "feed_hint": None,
            "matched_side": "home",
        }
        StreamMatching._resolve_feed_teams(
            StreamMatching(), [entry], True, True, separation_sports
        )
        return entry

    def test_listed_sport_splits(self):
        event = self._event("baseball")
        entry = self._resolve(event, ["baseball"])
        assert entry["feed_team"] is event.home_team

    def test_unlisted_sport_does_not_split(self):
        entry = self._resolve(self._event("baseball"), ["hockey"])
        assert entry["feed_team"] is None

    def test_identification_survives_the_sport_gate(self):
        # #527's invariant: identification is never gated. A sport left out of
        # the list must still resolve its feed team for the team_feed /
        # feed_side stream-ordering rules — only the channel split stops.
        event = self._event("baseball")
        entry = self._resolve(event, ["hockey"])
        assert entry["stream_feed_team"] is event.home_team
        assert entry["feed_team"] is None

    # --- settings persistence --------------------------------------------

    def test_sports_round_trip(self, db_conn):
        from teamarr.database.settings import (
            get_feed_separation_settings,
            update_feed_separation_settings,
        )

        assert update_feed_separation_settings(db_conn, sports=["baseball", "hockey"])
        assert get_feed_separation_settings(db_conn).sports == ["baseball", "hockey"]

    def test_default_is_every_sport(self, db_conn):
        from teamarr.database.settings import get_feed_separation_settings

        assert get_feed_separation_settings(db_conn).sports == []

    def test_unknown_sport_code_is_rejected(self, db_conn):
        # A typo would match no event and so disable separation everywhere —
        # a silent failure, hence a hard reject rather than a filter.
        from teamarr.database.settings import (
            get_feed_separation_settings,
            update_feed_separation_settings,
        )

        assert not update_feed_separation_settings(db_conn, sports=["baseballl"])
        assert get_feed_separation_settings(db_conn).sports == []
