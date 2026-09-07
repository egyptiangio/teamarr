"""Live Dispatcharr calls stop blocking the UI on every request (#736).

Two hot paths were making uncached round trips:

* `/api/v1/groups` spent 93% of its wall time inside `m3u.list_accounts()`,
  called on every Sources page load purely to freshen account NAMES that
  already have a stored fallback.
* `/api/v1/dispatcharr/status` — polled every 30s from every open tab — went
  through `factory.test_connection()`, which builds a THROWAWAY client and makes
  three calls, one of them pulling every channel group just to count it.

These tests pin the memos and, just as importantly, the things a memo must not
break: a failure is never cached, mutations invalidate, and the Test button
still makes a genuine round trip.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

from teamarr.dispatcharr.factory import ConnectionTestResult, DispatcharrFactory
from teamarr.dispatcharr.managers.m3u import M3UManager


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


ACCOUNTS = [
    {"id": 1, "name": "Provider A", "server_url": "http://a"},
    {"id": 2, "name": "custom", "server_url": ""},
]
GROUPS = [{"id": 10, "name": "Sports"}, {"id": 11, "name": "News"}]


class _CountingClient:
    def __init__(self, accounts=ACCOUNTS, groups=GROUPS, fail=False):
        self.accounts = accounts
        self.groups = groups
        self.fail = fail
        self.calls: list[str] = []

    def get(self, url: str):
        self.calls.append(url)
        if self.fail:
            return _FakeResponse(None, status_code=500)
        if url.startswith("/api/m3u/accounts"):
            return _FakeResponse(self.accounts)
        if url.startswith("/api/channels/groups"):
            return _FakeResponse(self.groups)
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url: str, data=None):
        self.calls.append(f"POST {url}")
        return _FakeResponse({}, status_code=202)

    def count(self, prefix: str) -> int:
        return sum(1 for c in self.calls if c.startswith(prefix))


class TestAccountCaching:
    def test_repeat_calls_hit_dispatcharr_once(self):
        client = _CountingClient()
        mgr = M3UManager(client)

        for _ in range(5):
            mgr.list_accounts()

        assert client.count("/api/m3u/accounts") == 1

    def test_custom_filter_is_applied_per_call_not_baked_into_the_memo(self):
        client = _CountingClient()
        mgr = M3UManager(client)

        without = mgr.list_accounts()
        with_custom = mgr.list_accounts(include_custom=True)

        assert [a.name for a in without] == ["Provider A"]
        assert {a.name for a in with_custom} == {"Provider A", "custom"}
        assert client.count("/api/m3u/accounts") == 1

    def test_failure_is_not_cached(self):
        """A blip must not blank every account name for the whole TTL."""
        client = _CountingClient(fail=True)
        mgr = M3UManager(client)

        assert mgr.list_accounts() == []
        client.fail = False
        assert [a.name for a in mgr.list_accounts()] == ["Provider A"]

    def test_refresh_invalidates(self):
        """A refresh moves status/updated_at, which the Sources UI shows."""
        client = _CountingClient()
        mgr = M3UManager(client)

        mgr.list_accounts()
        mgr.refresh_account(1)
        mgr.list_accounts()

        assert client.count("/api/m3u/accounts") == 2

    def test_explicit_invalidation_forces_a_refetch(self):
        client = _CountingClient()
        mgr = M3UManager(client)

        mgr.list_accounts()
        mgr.invalidate_accounts_cache()
        mgr.list_accounts()

        assert client.count("/api/m3u/accounts") == 2

    def test_concurrent_first_use_makes_one_request(self):
        client = _CountingClient()
        mgr = M3UManager(client)
        barrier = threading.Barrier(6)

        def call():
            barrier.wait()
            mgr.list_accounts()

        threads = [threading.Thread(target=call) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert client.count("/api/m3u/accounts") == 1


class TestGroupCaching:
    def test_repeat_calls_hit_dispatcharr_once(self):
        client = _CountingClient()
        mgr = M3UManager(client)

        for _ in range(4):
            mgr.list_groups()

        assert client.count("/api/channels/groups") == 1

    def test_search_and_exclude_filters_still_apply_to_cached_groups(self):
        client = _CountingClient()
        mgr = M3UManager(client)

        mgr.list_groups()
        assert [g.name for g in mgr.list_groups(search="spo")] == ["Sports"]
        assert client.count("/api/channels/groups") == 1

    def test_creating_a_group_invalidates(self):
        client = _CountingClient()
        mgr = M3UManager(client)
        client.post = lambda url, data=None: _FakeResponse(  # type: ignore[method-assign]
            {"id": 12, "name": "New"}, status_code=201
        )

        mgr.list_groups()
        mgr.create_channel_group("New")
        mgr.list_groups()

        assert client.count("/api/channels/groups") == 2

    def test_failure_is_not_cached(self):
        client = _CountingClient(fail=True)
        mgr = M3UManager(client)

        assert mgr.list_groups() == []
        client.fail = False
        assert [g.name for g in mgr.list_groups()] == ["Sports", "News"]


class _ProbeFactory(DispatcharrFactory):
    """Factory whose configuration and connection are supplied by the test."""

    def __init__(self, connection, settings, hash_value="h1"):
        super().__init__(db_factory=lambda: _FakeDb(settings))
        self._fake_connection = connection
        self._hash_value = hash_value

    @property
    def is_configured(self) -> bool:
        return True

    def _get_settings_hash(self) -> str:
        return self._hash_value

    def get_connection(self):
        return self._fake_connection


class _FakeDb:
    def __init__(self, settings):
        self.settings = settings

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Settings:
    url = "http://dispatcharr:9191"
    username = "user"


def _connection(status_code=200, calls=None):
    client = MagicMock()

    def get(url):
        (calls if calls is not None else []).append(url)
        return _FakeResponse({"count": 1, "results": []}, status_code=status_code)

    client.get.side_effect = get
    client.parse_api_error.return_value = "nope"
    return MagicMock(client=client)


class TestConnectionProbe:
    def _factory(self, conn, monkeypatch, hash_value="h1"):
        factory = _ProbeFactory(conn, _Settings(), hash_value)
        monkeypatch.setattr(
            "teamarr.dispatcharr.factory.get_dispatcharr_settings",
            lambda _conn: _Settings(),
        )
        return factory

    def test_probe_makes_one_request(self, monkeypatch):
        calls: list[str] = []
        factory = self._factory(_connection(calls=calls), monkeypatch)

        result = factory.probe_connection()

        assert result.success is True
        assert len(calls) == 1
        # The cheapest page the API will answer — not a full channel listing.
        assert "page_size=1" in calls[0]

    def test_repeat_probes_are_memoized(self, monkeypatch):
        calls: list[str] = []
        factory = self._factory(_connection(calls=calls), monkeypatch)

        for _ in range(10):
            factory.probe_connection()

        assert len(calls) == 1

    def test_zero_max_age_always_probes(self, monkeypatch):
        calls: list[str] = []
        factory = self._factory(_connection(calls=calls), monkeypatch)

        factory.probe_connection()
        factory.probe_connection(max_age_seconds=0)

        assert len(calls) == 2

    def test_settings_change_invalidates_the_memo(self, monkeypatch):
        """The memo rides the same hash get_connection reconnects on."""
        calls: list[str] = []
        factory = self._factory(_connection(calls=calls), monkeypatch)

        factory.probe_connection()
        factory._hash_value = "h2"
        factory.probe_connection()

        assert len(calls) == 2

    def test_failure_verdict_is_reported(self, monkeypatch):
        factory = self._factory(_connection(status_code=500), monkeypatch)

        result = factory.probe_connection()

        assert result.success is False
        assert result.error

    def test_probe_never_raises(self, monkeypatch):
        conn = MagicMock()
        conn.client.get.side_effect = RuntimeError("socket exploded")
        factory = self._factory(conn, monkeypatch)

        result = factory.probe_connection()

        assert isinstance(result, ConnectionTestResult)
        assert result.success is False

    def test_unconfigured_factory_reports_not_configured(self, monkeypatch):
        factory = self._factory(_connection(), monkeypatch)
        type(factory).is_configured = property(lambda self: False)
        try:
            result = factory.probe_connection()
        finally:
            type(factory).is_configured = property(lambda self: True)

        assert result.success is False
