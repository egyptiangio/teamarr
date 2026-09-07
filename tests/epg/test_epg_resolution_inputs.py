"""EPG resolution inputs are fetched and indexed once per run (#734).

``_build_epg_index`` runs once per event group, but everything it needs to
resolve stream tvg_ids -> EPG-source tvg_ids depends on the Dispatcharr install,
not the group. Before #734 each EPG-enabled source re-fetched the whole EPGData
catalog (one unpaginated response — 50k rows on the profiled install) and the
whole channel list, then rebuilt the same three lookups over them.

These tests pin the two halves of the fix: the fetches happen once per run and
are released between runs, and the catalog index is equivalent to what the
resolver builds for itself.
"""

from types import SimpleNamespace

from teamarr.consumers.event_group_processor.matching import StreamMatching
from teamarr.consumers.matching.epg_resolver import (
    build_epg_catalog_index,
    resolve_program_tvg_ids,
)

EPG_DATA = [
    {"id": 1, "tvg_id": "espn.us", "name": "ESPN", "epg_source": 7},
    {"id": 2, "tvg_id": "fs1.us", "name": "FS1 HD", "epg_source": 7},
    {"id": 3, "tvg_id": "espn2.us", "name": "ESPN", "epg_source": 7},
    {"id": 4, "tvg_id": "tsn.ca", "name": "TSN", "epg_source": 9},
]


class _Counter:
    """Stands in for the Dispatcharr client, counting every fetch."""

    def __init__(self):
        self.epg_data_calls = 0
        self.channel_map_calls = 0
        self.source_calls = 0

    # channels manager
    def get_epg_data_list(self):
        self.epg_data_calls += 1
        return list(EPG_DATA)

    def get_channel_maps(self, exclude_channel_ids=None):
        self.channel_map_calls += 1
        return {}, {}

    # raw client
    def paginated_get(self, path, error_context=None):
        self.source_calls += 1
        return [{"id": 7, "is_active": True, "name": "Guide"}]


class _Harness(StreamMatching):
    """StreamMatching with just the collaborators _epg_resolution_inputs touches."""

    def __init__(self, counter: _Counter):
        self._counter = counter
        self._dispatcharr_client = SimpleNamespace(channels=counter, client=counter)
        self._db_factory = self._no_db

    @staticmethod
    def _no_db():
        raise RuntimeError("no database in this harness")


def _harness() -> tuple[_Harness, _Counter]:
    counter = _Counter()
    return _Harness(counter), counter


def test_inputs_fetched_once_across_many_groups():
    h, counter = _harness()

    for _ in range(5):
        assert h._epg_resolution_inputs() is not None

    assert counter.epg_data_calls == 1
    assert counter.channel_map_calls == 1
    assert counter.source_calls == 1


def test_clear_releases_the_cache_for_the_next_run():
    h, counter = _harness()

    h._epg_resolution_inputs()
    h.clear_epg_resolution_cache()
    h._epg_resolution_inputs()

    assert counter.epg_data_calls == 2
    assert counter.channel_map_calls == 2


def test_fetch_failure_is_not_cached():
    """One transient blip must not disable EPG matching for the rest of the run."""
    h, counter = _harness()
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Dispatcharr unavailable")
        return list(EPG_DATA)

    counter.get_epg_data_list = flaky

    assert h._epg_resolution_inputs() is None
    assert h._epg_resolution_inputs() is not None


def test_cached_inputs_carry_a_prebuilt_catalog():
    h, _ = _harness()
    inputs = h._epg_resolution_inputs()

    assert inputs.catalog is not None
    # Scoped to the one active source (7); TSN's source 9 is not active.
    assert inputs.catalog.tvg_ids == {"espn.us", "fs1.us", "espn2.us"}
    # Full catalog for curated-link lookups, active or not.
    assert set(inputs.catalog.by_id) == {1, 2, 3, 4}


def test_prebuilt_catalog_matches_the_resolver_built_inline():
    """Passing ``catalog=`` must not change a single verdict."""
    streams = [
        {"id": 10, "tvg_id": "espn.us", "name": "ESPN"},
        {"id": 11, "tvg_id": "raw-fs1", "name": "FS1"},
        {"id": 12, "tvg_id": "raw-unknown", "name": "Some Channel"},
    ]
    active = {7}

    inline, inline_stats = resolve_program_tvg_ids(
        streams, EPG_DATA, {}, active_source_ids=active
    )
    prebuilt, prebuilt_stats = resolve_program_tvg_ids(
        streams,
        EPG_DATA,
        {},
        active_source_ids=active,
        catalog=build_epg_catalog_index(EPG_DATA, active),
    )

    assert inline == prebuilt
    assert inline_stats == prebuilt_stats


def test_ambiguous_names_stay_ambiguous_through_the_prebuilt_catalog():
    """"ESPN" maps to two tvg_ids, so it must resolve to neither."""
    streams = [{"id": 10, "tvg_id": "raw-espn", "name": "ESPN"}]

    resolution, stats = resolve_program_tvg_ids(
        streams,
        EPG_DATA,
        {},
        active_source_ids={7},
        catalog=build_epg_catalog_index(EPG_DATA, {7}),
    )

    assert resolution == {}
    assert stats["ambiguous_name"] == 1
