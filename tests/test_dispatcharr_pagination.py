"""DispatcharrClient.paginated_get fetches pages 2..N at once (#735).

The generic paginator walked `next` links one round trip at a time while the
M3U stream lister had already learned (#610) that pages are independently
addressable once page 1 reports `count`. Every channel/EPGData/group fetch in a
run paid that difference.

The load-bearing property is that nothing about the RESULT changes: same items,
same order. Callers build last-write-wins maps from this list
(`get_channel_maps`), so a reordered result is a silently different map.
"""

from __future__ import annotations

import threading

from teamarr.dispatcharr.client import DispatcharrClient


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _client(get_impl) -> DispatcharrClient:
    client = DispatcharrClient.__new__(DispatcharrClient)
    client.get = get_impl  # type: ignore[method-assign]
    return client


class _Paged:
    """Serves `total` items as pages of `page_size`, honouring ?page=N."""

    def __init__(self, total: int, page_size: int, fail_pages=(), status=500,
                 count_field: bool = True, hold: threading.Event | None = None):
        self.total = total
        self.page_size = page_size
        self.fail_pages = set(fail_pages)
        self.status = status
        self.count_field = count_field
        self.hold = hold
        self.requested: list[int] = []
        self.in_flight = 0
        self.peak = 0
        self._lock = threading.Lock()

    @property
    def pages(self) -> int:
        return -(-self.total // self.page_size)

    def __call__(self, url: str):
        page = 1
        for part in url.partition("?")[2].split("&"):
            if part.startswith("page="):
                page = int(part[5:])
        with self._lock:
            self.requested.append(page)
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        try:
            if self.hold is not None and page > 1:
                self.hold.wait(timeout=2.0)
            if page in self.fail_pages:
                return _FakeResponse(None, status_code=self.status)
            start = (page - 1) * self.page_size
            results = [
                {"id": i} for i in range(start, min(start + self.page_size, self.total))
            ]
            payload: dict = {"results": results}
            if self.count_field:
                payload["count"] = self.total
            if page < self.pages:
                payload["next"] = (
                    f"http://dp:9191/api/x/?page={page + 1}&page_size={self.page_size}"
                )
            return _FakeResponse(payload)
        finally:
            with self._lock:
                self.in_flight -= 1


ENDPOINT = "/api/x/?page=1&page_size=100"


def test_all_items_returned_in_page_order():
    paged = _Paged(total=1000, page_size=100)
    items = _client(paged).paginated_get(ENDPOINT, "x")

    assert [i["id"] for i in items] == list(range(1000))


def test_every_page_is_requested_exactly_once():
    paged = _Paged(total=1000, page_size=100)
    _client(paged).paginated_get(ENDPOINT, "x")

    assert sorted(paged.requested) == list(range(1, 11))


def test_pages_are_fetched_concurrently():
    hold = threading.Event()
    paged = _Paged(total=1000, page_size=100, hold=hold)
    threading.Timer(0.4, hold.set).start()

    _client(paged).paginated_get(ENDPOINT, "x")

    assert paged.peak > 1, "pages were fetched serially"


def test_single_page_needs_no_pool():
    paged = _Paged(total=40, page_size=100)
    items = _client(paged).paginated_get(ENDPOINT, "x")

    assert [i["id"] for i in items] == list(range(40))
    assert paged.requested == [1]


def test_missing_count_falls_back_to_walking_next():
    """No `count` means pages are not addressable — the serial walk still works."""
    paged = _Paged(total=350, page_size=100, count_field=False)
    items = _client(paged).paginated_get(ENDPOINT, "x")

    assert [i["id"] for i in items] == list(range(350))
    assert paged.requested == [1, 2, 3, 4]


def test_non_paginated_list_response_is_returned_whole():
    """/api/epg/epgdata/ answers with every row in one list, page_size and all."""
    rows = [{"id": i} for i in range(5)]
    items = _client(lambda url: _FakeResponse(rows)).paginated_get(ENDPOINT, "x")

    assert items == rows


def test_page_size_is_measured_not_assumed():
    """An endpoint that ignores page_size must not be split into phantom pages."""
    def ignores_page_size(url: str):
        return _FakeResponse({"count": 300, "results": [{"id": i} for i in range(300)]})

    paged_client = _client(ignores_page_size)
    assert len(paged_client.paginated_get(ENDPOINT, "x")) == 300


def test_failed_page_returns_a_prefix_never_a_hole():
    """A gap in the middle would be indistinguishable from a smaller collection."""
    paged = _Paged(total=1000, page_size=100, fail_pages={5})
    items = _client(paged).paginated_get(ENDPOINT, "x")

    ids = [i["id"] for i in items]
    assert ids == sorted(ids)
    assert ids == list(range(len(ids)))
    assert len(ids) < 1000


def test_failed_first_page_returns_empty():
    paged = _Paged(total=1000, page_size=100, fail_pages={1})
    assert _client(paged).paginated_get(ENDPOINT, "x") == []


def test_client_is_built_once_under_concurrent_first_use():
    """Lazy init is double-checked — a race must not leak a second pooled client."""
    client = DispatcharrClient.__new__(DispatcharrClient)
    client._timeout = 5.0
    client._client = None
    client._client_lock = threading.Lock()

    built: list[object] = []
    barrier = threading.Barrier(8)

    def build():
        barrier.wait()
        built.append(client._get_client())

    threads = [threading.Thread(target=build) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len({id(c) for c in built}) == 1
    client._client.close()


def test_pool_keepalive_matches_max_connections():
    """A lower keepalive cap means a TLS handshake per request past the cap."""
    client = DispatcharrClient.__new__(DispatcharrClient)
    client._timeout = 5.0
    client._client = None
    client._client_lock = threading.Lock()

    pooled = client._get_client()
    try:
        pool = pooled._transport._pool
        assert pool._max_keepalive_connections == pool._max_connections
    finally:
        pooled.close()
