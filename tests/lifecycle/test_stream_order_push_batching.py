"""Stream-order pushes go out concurrently, off one bulk read (#735).

The ordering step used to walk managed channels one at a time, issuing two
SQLite queries and — since #712 made any order difference enough to push — one
Dispatcharr PATCH per channel, all serially. On an install with several hundred
channels that was the longest serial stretch left in a generation run.

The rework splits it into phases: all database work first (two bulk scans plus
the priority writes), then the pushes on a bounded thread pool. These tests pin
the properties that split must preserve — which channels get pushed and with
what — plus the two new ones: the reads are bulk, and the pushes overlap.

Convergence, the live-event pin and failed-push retry are covered next door in
test_stream_order_convergence.py; those tests run through this same code path
and are the regression net for the phase split itself.
"""

import threading
from unittest.mock import MagicMock, patch

from teamarr.consumers.generation import _apply_stream_ordering
from teamarr.dispatcharr.types import DispatcharrChannel, OperationResult


def _seed_channels(conn, spec: dict[int, tuple[list[int], list[int]]]):
    """Seed managed channels.

    ``spec`` maps dispatcharr_channel_id -> (db stream order, ignored) so a
    caller reads the intended order straight off the literal.
    """
    for row, (d_id, (db_streams, _)) in enumerate(spec.items(), start=1):
        conn.execute(
            """INSERT INTO managed_channels
               (id, event_id, event_provider, tvg_id, channel_name,
                channel_number, dispatcharr_channel_id, dispatcharr_uuid)
               VALUES (?, ?, 'espn', ?, ?, ?, ?, ?)""",
            (row, str(1000 + row), f"teamarr-event-{1000 + row}",
             f"Channel {d_id}", str(5000 + row), d_id, f"uuid-{d_id}"),
        )
        for priority, stream_id in enumerate(db_streams):
            conn.execute(
                """INSERT INTO managed_channel_streams
                   (managed_channel_id, dispatcharr_stream_id, stream_name, priority)
                   VALUES (?, ?, ?, ?)""",
                (row, stream_id, f"Stream {stream_id}", priority),
            )
    conn.commit()


def _manager(spec: dict[int, tuple[list[int], list[int]]], on_update=None):
    """ChannelManager stand-in holding each channel's Dispatcharr-side order."""
    channels = [
        DispatcharrChannel(
            id=d_id,
            uuid=f"uuid-{d_id}",
            name=f"Channel {d_id}",
            channel_number=str(5000 + i),
            tvg_id=f"teamarr-event-{1000 + i}",
            streams=tuple(d_streams),
        )
        for i, (d_id, (_, d_streams)) in enumerate(spec.items(), start=1)
    ]
    cm = MagicMock()
    cm.get_channels.return_value = channels
    cm.update_channel.side_effect = on_update or (
        lambda cid, data: OperationResult(success=True)
    )
    return cm


def _run(db_factory, cm):
    with patch("teamarr.consumers.generation.ChannelManager", return_value=cm):
        return _apply_stream_ordering(db_factory, MagicMock(), MagicMock())


def _pushed(cm) -> dict[int, list[int]]:
    """dispatcharr_channel_id -> the stream list it was handed."""
    return {
        call.args[0]: call.args[1]["streams"]
        for call in cm.update_channel.call_args_list
        if "streams" in call.args[1]
    }


# Three channels drifted, two already correct.
MIXED = {
    100: ([1, 2, 3], [3, 2, 1]),
    101: ([4, 5], [5, 4]),
    102: ([6, 7], [6, 7]),
    103: ([8, 9, 10], [10, 8, 9]),
    104: ([11], [11]),
}


class TestPushSelection:
    def test_only_drifted_channels_are_pushed(self, db_factory, db_conn):
        _seed_channels(db_conn, MIXED)
        cm = _manager(MIXED)

        result = _run(db_factory, cm)

        assert set(_pushed(cm)) == {100, 101, 103}
        assert result["order_drift_synced"] == 3

    def test_each_channel_gets_its_own_intended_order(self, db_factory, db_conn):
        """Batching must not cross-contaminate one channel's list with another's."""
        _seed_channels(db_conn, MIXED)
        cm = _manager(MIXED)

        _run(db_factory, cm)

        assert _pushed(cm) == {100: [1, 2, 3], 101: [4, 5], 103: [8, 9, 10]}

    def test_every_channel_is_pushed_at_most_once(self, db_factory, db_conn):
        _seed_channels(db_conn, MIXED)
        cm = _manager(MIXED)

        _run(db_factory, cm)

        pushed_ids = [c.args[0] for c in cm.update_channel.call_args_list]
        assert len(pushed_ids) == len(set(pushed_ids))

    def test_steady_state_pushes_nothing(self, db_factory, db_conn):
        quiet = {200: ([1, 2], [1, 2]), 201: ([3], [3])}
        _seed_channels(db_conn, quiet)
        cm = _manager(quiet)

        result = _run(db_factory, cm)

        assert _pushed(cm) == {}
        assert result.get("push_failures", 0) == 0


class TestPushConcurrency:
    def test_pushes_overlap(self, db_factory, db_conn):
        """The point of the change: pushes must not be serialized."""
        in_flight = 0
        peak = 0
        lock = threading.Lock()
        release = threading.Event()

        def slow_update(cid, data):
            nonlocal in_flight, peak
            with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            # Hold until every worker has had a chance to arrive; a serial
            # implementation deadlocks here instead of overlapping, so the
            # timeout is the failure signal.
            release.wait(timeout=2.0)
            with lock:
                in_flight -= 1
            return OperationResult(success=True)

        drifted = {300 + i: ([i, i + 1], [i + 1, i]) for i in range(4)}
        _seed_channels(db_conn, drifted)
        cm = _manager(drifted, on_update=slow_update)

        def unblock():
            release.set()

        timer = threading.Timer(0.5, unblock)
        timer.start()
        try:
            _run(db_factory, cm)
        finally:
            timer.cancel()
            release.set()

        assert peak > 1, "stream-order pushes ran serially"

    def test_one_failing_push_does_not_stop_the_others(self, db_factory, db_conn):
        def flaky(cid, data):
            if cid == 101:
                return OperationResult(success=False, error="boom")
            return OperationResult(success=True)

        _seed_channels(db_conn, MIXED)
        cm = _manager(MIXED, on_update=flaky)

        result = _run(db_factory, cm)

        assert set(_pushed(cm)) == {100, 101, 103}
        assert result["push_failures"] == 1

    def test_a_raising_push_is_isolated(self, db_factory, db_conn):
        def explode(cid, data):
            if cid == 103:
                raise RuntimeError("connection reset")
            return OperationResult(success=True)

        _seed_channels(db_conn, MIXED)
        cm = _manager(MIXED, on_update=explode)

        result = _run(db_factory, cm)

        assert result["push_failures"] == 1
        # The run completes rather than propagating.
        assert "error" not in result


class TestBulkReads:
    def test_stream_reads_do_not_scale_with_channel_count(self, db_factory, db_conn):
        """Two bulk scans, not two queries per channel."""
        many = {400 + i: ([i, i + 1], [i, i + 1]) for i in range(25)}
        _seed_channels(db_conn, many)
        cm = _manager(many)

        seen: list[str] = []
        from teamarr.database.channels import streams as streams_mod

        real_single = streams_mod.get_ordered_stream_ids

        def counting_single(conn, managed_channel_id, now=None):
            seen.append("per-channel")
            return real_single(conn, managed_channel_id, now)

        with patch.object(streams_mod, "get_ordered_stream_ids", counting_single):
            _run(db_factory, cm)

        assert seen == [], "ordering still issues a query per channel"
