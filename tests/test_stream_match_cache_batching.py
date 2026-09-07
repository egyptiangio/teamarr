"""Cache writes commit once per session, not once per stream (#742).

``StreamMatchCache.session()`` pins one connection so a batch of cache
operations shares it, and its docstring has always claimed writes "commit once
at session end". They did not: every write method called ``conn.commit()``
itself, so the session saved the connection setup and never the fsync. A run
matching a couple of thousand streams paid a couple of thousand fsyncs, and on
the network-backed storage a Kubernetes deploy usually has, an fsync costs far
more than the row it commits — it profiled as the single largest cost in the
match phase.

The behaviour that must not change is durability of a *completed* session, so
these tests check what is readable from an independent connection afterwards.
"""


import pytest

from teamarr.consumers.stream_match_cache import StreamMatchCache

EVENT = {"id": "e1", "name": "Rays at Tigers"}


@pytest.fixture
def cache(db_factory):
    return StreamMatchCache(db_factory)


def _set(cache, stream_id, generation=1):
    return cache.set(
        group_id=1, stream_id=stream_id, stream_name=f"Stream {stream_id}",
        event_id=f"e{stream_id}", league="mlb", cached_data=EVENT,
        generation=generation,
    )


def _rows(db_factory):
    with db_factory() as conn:
        return conn.execute("SELECT COUNT(*) FROM stream_match_cache").fetchone()[0]


class TestDurability:
    def test_writes_inside_a_session_are_persisted_after_it(self, cache, db_factory):
        with cache.session():
            for i in range(5):
                assert _set(cache, i)
        assert _rows(db_factory) == 5

    def test_writes_are_readable_within_the_same_session(self, cache):
        """Batching must not make a write invisible to the loop that made it."""
        with cache.session():
            _set(cache, 1)
            entry = cache.get(1, 1, "Stream 1")
            assert entry is not None
            assert entry.event_id == "e1"

    def test_writes_outside_a_session_still_commit_immediately(self, cache, db_factory):
        _set(cache, 99)
        assert _rows(db_factory) == 1

    def test_failed_matches_batch_too(self, cache, db_factory):
        with cache.session():
            for i in range(3):
                cache.set_failed(group_id=1, stream_id=i, stream_name=f"S{i}", generation=1)
        assert _rows(db_factory) == 3

    def test_deletes_batch_and_persist(self, cache, db_factory):
        with cache.session():
            for i in range(4):
                _set(cache, i)
        with cache.session():
            cache.delete(1, 0, "Stream 0")
            cache.delete(1, 1, "Stream 1")
        assert _rows(db_factory) == 2

    def test_a_user_correction_survives_the_session(self, cache, db_factory):
        with cache.session():
            _set(cache, 7)
            cache.set_user_correction(
                group_id=1, stream_id=7, stream_name="Stream 7", event_id="corrected",
                league="mlb", cached_data=EVENT,
            )
        entry = cache.get(1, 7, "Stream 7")
        assert entry.event_id == "corrected"
        assert entry.user_corrected


class TestCommitCount:
    def test_a_session_commits_once_not_once_per_write(self, db_factory, monkeypatch):
        """The whole point: N writes must not mean N fsyncs."""
        commits = {"n": 0}

        class CountingConn:
            """Proxy: sqlite3.Connection is immutable, so we wrap rather than subclass."""

            def __init__(self, inner):
                self._inner = inner

            def commit(self):
                commits["n"] += 1
                return self._inner.commit()

            def __getattr__(self, name):
                return getattr(self._inner, name)

        from contextlib import contextmanager

        @contextmanager
        def counting_factory():
            with db_factory() as conn:
                yield CountingConn(conn)

        cache = StreamMatchCache(counting_factory)
        with cache.session():
            for i in range(25):
                _set(cache, i)
        # One commit for the session's own context exit; the 25 writes add none.
        assert commits["n"] <= 2, f"expected batched commits, saw {commits['n']}"

    def test_without_a_session_each_write_commits(self, db_factory):
        """The un-batched path is unchanged for callers that do not open one."""
        cache = StreamMatchCache(db_factory)
        for i in range(3):
            _set(cache, i)
        # Readable immediately, without any session having been closed.
        assert _rows(db_factory) == 3
