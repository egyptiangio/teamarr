"""Declarative settings field registry.

Single source of truth mapping each settings dataclass field to its DB column
and serialization behavior. Field types and defaults are introspected from the
dataclasses in types.py, so a field is declared exactly once; the registry adds
only what the dataclass cannot express (column name, JSON handling, custom
parse/dump hooks).

The registry drives:
- read.py: generic row -> dataclass building (get_*_settings)
- update.py: generic UPDATE statement building (update_*_settings)
- parity tests: registry <-> schema.sql <-> Pydantic API models

Read semantics (FieldSpec.from_db):
- Missing column or NULL -> dataclass default (nullable fields pass None through)
- bool columns coerce via bool(); non-nullable str treats '' as unset
- JSON columns parse with fallback to the default on any error

Write semantics (FieldSpec.to_db):
- bool -> int, JSON -> json.dumps, plus per-field dump hooks (e.g. URL rstrip)
"""

from __future__ import annotations

import json
import types as _types
from collections.abc import Callable
from dataclasses import MISSING, asdict, is_dataclass
from dataclasses import dataclass as _dataclass
from dataclasses import fields as _dc_fields
from typing import Any, Union, cast, get_args, get_origin

from .types import (
    APISettings,
    BackupSettings,
    ChannelNumberingSettings,
    ChannelsDVRSettings,
    DispatcharrSettings,
    DisplaySettings,
    DurationSettings,
    EmbySettings,
    EPGSettings,
    FeedSeparationSettings,
    JellyfinSettings,
    LifecycleSettings,
    ProxySettings,
    ReconciliationSettings,
    SchedulerSettings,
    StreamFilterSettings,
    StreamOrderingSettings,
    TeamFilterSettings,
    UpdateCheckSettings,
)


@_dataclass(frozen=True)
class FieldSpec:
    """One settings field: dataclass field <-> DB column binding."""

    name: str  # dataclass field name
    column: str  # settings table column
    kind: str  # 'bool' | 'int' | 'float' | 'str' | 'json'
    nullable: bool  # NULL passes through as None (no default coalescing)
    default: Any  # value or zero-arg factory for NULL/missing column
    parse: Callable[[Any], Any] | None = None  # raw DB value -> field value
    dump: Callable[[Any], Any] | None = None  # field value -> DB value

    def default_value(self) -> Any:
        return self.default() if callable(self.default) else self.default

    def from_db(self, raw: Any) -> Any:
        """Convert a raw DB value (or None for NULL/missing column) to the field value."""
        if self.parse is not None:
            return self.parse(raw)
        if raw is None:
            return None if self.nullable else self.default_value()
        if self.kind == "bool":
            return bool(raw)
        if self.kind == "json":
            try:
                value = json.loads(raw)
            except (TypeError, ValueError):
                return None if self.nullable else self.default_value()
            if value is None:
                return None if self.nullable else self.default_value()
            return value
        if self.kind == "str" and not self.nullable:
            return raw or self.default_value()
        return raw

    def to_db(self, value: Any) -> Any:
        """Convert a field value to its DB representation."""
        if self.dump is not None:
            return self.dump(value)
        if value is None:
            return None
        if self.kind == "bool":
            return int(value)
        if self.kind == "json":
            return json.dumps(value)
        return value


@_dataclass(frozen=True)
class GroupSpec:
    """A settings group: dataclass + its field specs."""

    name: str  # attribute name on AllSettings
    cls: type
    fields: tuple[FieldSpec, ...]
    log_label: str

    @property
    def field_map(self) -> dict[str, FieldSpec]:
        return {f.name: f for f in self.fields}

    def field(self, name: str) -> FieldSpec:
        return self.field_map[name]

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields)


def _infer_kind(hint: Any) -> tuple[str, bool]:
    """Infer (kind, nullable) from a dataclass field type hint."""
    nullable = False
    origin = get_origin(hint)
    if origin in (Union, _types.UnionType):
        args = [a for a in get_args(hint) if a is not type(None)]
        nullable = len(args) < len(get_args(hint))
        # Collapse to the sole non-None arg when possible; mixed unions
        # (e.g. list[int | str]) are JSON regardless.
        hint = args[0] if len(args) == 1 else list
        origin = get_origin(hint)
    if origin in (list, dict) or hint in (list, dict):
        return "json", nullable
    if hint is bool:
        return "bool", nullable
    if hint is int:
        return "int", nullable
    if hint is float:
        return "float", nullable
    if hint is str:
        return "str", nullable
    # Nested dataclasses / anything else round-trips as JSON.
    return "json", nullable


def _specs(
    cls: type,
    columns: dict[str, str] | None = None,
    prefix: str = "",
    hooks: dict[str, dict[str, Callable[[Any], Any]]] | None = None,
) -> tuple[FieldSpec, ...]:
    """Build FieldSpecs for every field of a settings dataclass.

    columns: explicit field -> column overrides
    prefix: default column = prefix + field name (for e.g. emby_, duration_)
    hooks: per-field {'parse': fn, 'dump': fn} overrides
    """
    columns = columns or {}
    hooks = hooks or {}
    specs = []
    for f in _dc_fields(cls):
        default: Any
        if f.default is not MISSING:
            default = f.default
        else:
            default = f.default_factory  # keep factory for fresh mutables
        kind, nullable = _infer_kind(f.type)
        field_hooks = hooks.get(f.name, {})
        specs.append(
            FieldSpec(
                name=f.name,
                column=columns.get(f.name, prefix + f.name),
                kind=kind,
                nullable=nullable,
                default=default,
                parse=field_hooks.get("parse"),
                dump=field_hooks.get("dump"),
            )
        )
    return tuple(specs)


# ---------------------------------------------------------------------------
# Per-field hooks for the irregular cases
# ---------------------------------------------------------------------------


def _parse_profile_ids(raw: Any) -> list[int | str] | None:
    """None = all profiles, [] = no profiles, [1, '{sport}'] = specific."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _parse_league_starts(raw: Any) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return {k: int(v) for k, v in parsed.items()}
    except (TypeError, ValueError):
        pass
    return {}


def _parse_str_list(default: list[str]) -> Callable[[Any], list[str]]:
    def _parse(raw: Any) -> list[str]:
        if not raw:
            return list(default)
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return parsed
        except (TypeError, ValueError):
            pass
        return list(default)

    return _parse


def _parse_team_list(raw: Any) -> list[dict] | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _dump_team_list(value: Any) -> str | None:
    # Empty list clears to SQL NULL (matches historical clear semantics).
    return json.dumps(value) if value else None


def _parse_ordering_rules(raw: Any) -> list:
    from .types import (
        LEGACY_RULE_MODE,
        NO_VALUE_RULE_TYPES,
        VALID_RULE_MODES,
        StreamOrderingRule,
    )

    def _mode(v: Any) -> str:
        # Missing (legacy rows pre epic teamarr-5ag) or invalid -> hard 'priority',
        # preserving the original first-match ordering so migration is lossless.
        return v if v in VALID_RULE_MODES else LEGACY_RULE_MODE

    def _points(v: Any) -> int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    if not raw:
        return []
    try:
        rules_data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(rules_data, list):
        return []
    return [
        StreamOrderingRule(
            type=rule.get("type", "m3u"),
            value=rule.get("value", ""),
            priority=rule.get("priority", 99),
            mode=_mode(rule.get("mode")),
            points=_points(rule.get("points")),
        )
        for rule in rules_data
        if isinstance(rule, dict)
        and rule.get("type")
        and (rule.get("type") in NO_VALUE_RULE_TYPES or rule.get("value"))
    ]


def _dump_url(value: Any) -> Any:
    return value.rstrip("/") if value else value


def _parse_channelsdvr_servers(raw: Any) -> list:
    from .types import ChannelsDVRServer

    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [
        ChannelsDVRServer(
            name=entry.get("name", ""),
            url=entry.get("url"),
            source_name=entry.get("source_name"),
            lineup_id=entry.get("lineup_id"),
        )
        for entry in data
        if isinstance(entry, dict)
    ]


def _parse_media_servers(raw: Any) -> list:
    from .types import MediaServerEntry

    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [
        MediaServerEntry(
            name=entry.get("name", ""),
            url=entry.get("url"),
            username=entry.get("username"),
            password=entry.get("password"),
            api_key=entry.get("api_key"),
        )
        for entry in data
        if isinstance(entry, dict)
    ]


def _dump_server_list(value: Any) -> str:
    # Shared by the channelsdvr/emby/jellyfin server-list fields. Accepts
    # dataclass instances (internal callers) or dicts (API updates); copies
    # either way so callers' objects aren't mutated by the URL rstrip.
    entries: list[dict] = []
    for v in value or []:
        if is_dataclass(v) and not isinstance(v, type):
            entry: dict = asdict(v)
        else:
            entry = dict(cast("dict[str, Any]", v))
        url = entry.get("url")
        if isinstance(url, str):
            entry["url"] = url.rstrip("/")
        entries.append(entry)
    return json.dumps(entries)


_URL_HOOK = {"url": {"dump": _dump_url}}
_MEDIA_SERVERS_HOOK = {
    "servers": {"parse": _parse_media_servers, "dump": _dump_server_list}
}


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

GROUPS: dict[str, GroupSpec] = {
    g.name: g
    for g in (
        GroupSpec(
            "dispatcharr",
            DispatcharrSettings,
            _specs(
                DispatcharrSettings,
                columns={
                    "enabled": "dispatcharr_enabled",
                    "url": "dispatcharr_url",
                    "username": "dispatcharr_username",
                    "password": "dispatcharr_password",
                    "epg_id": "dispatcharr_epg_id",
                },
                hooks={
                    "default_channel_profile_ids": {
                        "parse": _parse_profile_ids,
                        # None means "all profiles" and is stored as JSON null,
                        # not SQL NULL.
                        "dump": json.dumps,
                    },
                    **_URL_HOOK,
                },
            ),
            "Dispatcharr",
        ),
        GroupSpec("lifecycle", LifecycleSettings, _specs(LifecycleSettings), "Lifecycle"),
        GroupSpec(
            "reconciliation",
            ReconciliationSettings,
            _specs(ReconciliationSettings),
            "Reconciliation",
        ),
        GroupSpec(
            "scheduler",
            SchedulerSettings,
            _specs(
                SchedulerSettings,
                columns={
                    "enabled": "scheduler_enabled",
                    "interval_minutes": "scheduler_interval_minutes",
                },
            ),
            "Scheduler",
        ),
        GroupSpec("epg", EPGSettings, _specs(EPGSettings), "EPG"),
        GroupSpec(
            "durations", DurationSettings, _specs(DurationSettings, prefix="duration_"), "Duration"
        ),
        GroupSpec("display", DisplaySettings, _specs(DisplaySettings), "Display"),
        GroupSpec(
            "api",
            APISettings,
            _specs(
                APISettings,
                columns={"timeout": "api_timeout", "retry_count": "api_retry_count"},
            ),
            "API",
        ),
        GroupSpec(
            "stream_filter",
            StreamFilterSettings,
            _specs(StreamFilterSettings, prefix="stream_filter_"),
            "Stream filter",
        ),
        GroupSpec(
            "team_filter",
            TeamFilterSettings,
            _specs(
                TeamFilterSettings,
                columns={
                    "enabled": "team_filter_enabled",
                    "include_teams": "default_include_teams",
                    "exclude_teams": "default_exclude_teams",
                    "mode": "default_team_filter_mode",
                    "bypass_filter_for_playoffs": "default_bypass_filter_for_playoffs",
                },
                hooks={
                    "include_teams": {"parse": _parse_team_list, "dump": _dump_team_list},
                    "exclude_teams": {"parse": _parse_team_list, "dump": _dump_team_list},
                },
            ),
            "Team filter",
        ),
        GroupSpec(
            "channel_numbering",
            ChannelNumberingSettings,
            _specs(
                ChannelNumberingSettings,
                hooks={"league_channel_starts": {"parse": _parse_league_starts}},
            ),
            "Channel numbering",
        ),
        GroupSpec(
            "stream_ordering",
            StreamOrderingSettings,
            _specs(
                StreamOrderingSettings,
                columns={"rules": "stream_ordering_rules"},
                hooks={"rules": {"parse": _parse_ordering_rules, "dump": json.dumps}},
            ),
            "Stream ordering",
        ),
        GroupSpec(
            "update_check",
            UpdateCheckSettings,
            _specs(
                UpdateCheckSettings,
                columns={
                    "enabled": "update_check_enabled",
                    "notify_stable": "update_notify_stable",
                    "notify_dev": "update_notify_dev",
                    "github_owner": "update_github_owner",
                    "github_repo": "update_github_repo",
                    "dev_branch": "update_dev_branch",
                    "auto_detect_branch": "update_auto_detect_branch",
                },
            ),
            "Update check",
        ),
        GroupSpec(
            "backup",
            BackupSettings,
            _specs(BackupSettings, prefix="scheduled_backup_"),
            "Backup",
        ),
        GroupSpec(
            "feed_separation",
            FeedSeparationSettings,
            _specs(
                FeedSeparationSettings,
                columns={
                    "enabled": "feed_separation_enabled",
                    "sports": "feed_separation_sports",
                    "home_terms": "feed_home_terms",
                    "away_terms": "feed_away_terms",
                    "detect_team_names": "feed_detect_team_names",
                    "label_style": "feed_label_style",
                },
                hooks={
                    "sports": {"parse": _parse_str_list([])},
                    "home_terms": {"parse": _parse_str_list(["HOME"])},
                    "away_terms": {"parse": _parse_str_list(["AWAY"])},
                },
            ),
            "Feed separation",
        ),
        GroupSpec(
            "emby",
            EmbySettings,
            _specs(EmbySettings, prefix="emby_", hooks=_MEDIA_SERVERS_HOOK),
            "Emby",
        ),
        GroupSpec(
            "jellyfin",
            JellyfinSettings,
            _specs(JellyfinSettings, prefix="jellyfin_", hooks=_MEDIA_SERVERS_HOOK),
            "Jellyfin",
        ),
        GroupSpec(
            "channelsdvr",
            ChannelsDVRSettings,
            _specs(
                ChannelsDVRSettings,
                prefix="channelsdvr_",
                hooks={
                    "servers": {
                        "parse": _parse_channelsdvr_servers,
                        "dump": _dump_server_list,
                    },
                },
            ),
            "Channels DVR",
        ),
        GroupSpec("proxy", ProxySettings, _specs(ProxySettings, prefix="proxy_"), "Proxy"),
    )
}
