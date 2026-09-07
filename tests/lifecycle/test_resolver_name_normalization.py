"""A resolved group name that differs only in surrounding whitespace must still
resolve to the group Dispatcharr already has (#745).

Observed on a live install, 129 times in three hours:

    [RESOLVER] Failed to create group 'Auto | Football | NCAAF | FBS ': Bad request
    [LIFECYCLE] ... channel 59280: channel_group_id: This field may not be null.

The group `Auto | Football | NCAAF | FBS` existed the whole time. The trailing
space made the cache lookup miss, so the resolver tried to CREATE it; both
managers post `name.strip()`, so Dispatcharr saw a duplicate and returned 400;
the channel then got a null group id and its settings sync failed. Nothing
about that state changes between runs, so it repeated forever.
"""

from unittest.mock import MagicMock

import pytest

from teamarr.consumers.lifecycle.dynamic_resolver import DynamicResolver, _group_key


def _resolver(existing: dict[str, int], dispatcharr=None) -> DynamicResolver:
    r = DynamicResolver()
    r._initialized = True  # skip the Dispatcharr/DB load
    r._groups_loaded = True
    r._groups_by_name = {_group_key(n): i for n, i in existing.items()}
    r._profiles_by_name = {_group_key(n): i for n, i in existing.items()}
    r._known_group_ids = set(existing.values())
    if dispatcharr is not None:
        r._get_dispatcharr = lambda: dispatcharr
    return r


EXISTING = {"Auto | Football | NCAAF | FBS": 4004, "Auto | Football | NCAAF": 3914}


class TestGroupKey:
    def test_trims_and_lowercases(self):
        assert _group_key("  Auto | X  ") == "auto | x"

    def test_does_not_collapse_internal_runs(self):
        """Live data: 18 real groups differ only by \\xa0 vs a regular space."""
        assert _group_key("UK| ULTIMATE POOL PPV") != _group_key("UK| ULTIMATE\xa0POOL\xa0PPV")

    def test_internal_double_space_is_preserved(self):
        assert _group_key("DK: TV2 PLAY  [1080p]") != _group_key("DK: TV2 PLAY [1080p]")


class TestGroupLookup:
    @pytest.mark.parametrize(
        "name",
        [
            "Auto | Football | NCAAF | FBS",
            "Auto | Football | NCAAF | FBS ",     # the reported failure
            " Auto | Football | NCAAF | FBS",
            "\tAuto | Football | NCAAF | FBS\n",
            "auto | football | ncaaf | fbs ",
        ],
    )
    def test_whitespace_variants_all_find_the_existing_group(self, name):
        dispatcharr = MagicMock()
        r = _resolver(EXISTING, dispatcharr)

        assert r._get_or_create_group(name) == 4004
        # The whole point: no create attempt, so no duplicate 400 from Dispatcharr.
        dispatcharr.m3u.create_channel_group.assert_not_called()

    def test_a_genuinely_new_name_is_still_created(self):
        dispatcharr = MagicMock()
        dispatcharr.m3u.create_channel_group.return_value = MagicMock(
            success=True, data={"id": 9001}
        )
        r = _resolver(EXISTING, dispatcharr)

        assert r._get_or_create_group("Auto | Football | NCAAF | FCS ") == 9001
        # Created under the trimmed name, matching what the manager would post.
        dispatcharr.m3u.create_channel_group.assert_called_once_with(
            "Auto | Football | NCAAF | FCS"
        )

    def test_a_created_group_is_cached_under_the_normalized_key(self):
        dispatcharr = MagicMock()
        dispatcharr.m3u.create_channel_group.return_value = MagicMock(
            success=True, data={"id": 9001}
        )
        r = _resolver(EXISTING, dispatcharr)

        r._get_or_create_group("New Group ")
        assert r._get_or_create_group("  New Group") == 9001
        # Second lookup is a cache hit, not a second create.
        assert dispatcharr.m3u.create_channel_group.call_count == 1

    @pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
    def test_a_whitespace_only_name_is_refused_without_calling_dispatcharr(self, blank):
        dispatcharr = MagicMock()
        r = _resolver(EXISTING, dispatcharr)

        assert r._get_or_create_group(blank) is None
        dispatcharr.m3u.create_channel_group.assert_not_called()

    def test_distinct_groups_are_not_merged_by_normalization(self):
        """The collapsing bug this deliberately avoids."""
        dispatcharr = MagicMock()
        dispatcharr.m3u.create_channel_group.return_value = MagicMock(
            success=True, data={"id": 777}
        )
        r = _resolver({"UK| ULTIMATE POOL PPV": 100}, dispatcharr)

        # The nbsp variant is a DIFFERENT group and must not resolve to 100.
        assert r._get_or_create_group("UK| ULTIMATE\xa0POOL\xa0PPV") == 777


class TestProfileLookup:
    """Same contract — create_profile also posts name.strip()."""

    def test_whitespace_variant_finds_the_existing_profile(self):
        dispatcharr = MagicMock()
        r = _resolver({"Sports": 42}, dispatcharr)

        assert r._get_or_create_profile("Sports ") == 42
        dispatcharr.channels.create_profile.assert_not_called()

    def test_whitespace_only_profile_name_is_refused(self):
        dispatcharr = MagicMock()
        r = _resolver({"Sports": 42}, dispatcharr)

        assert r._get_or_create_profile("   ") is None
        dispatcharr.channels.create_profile.assert_not_called()
