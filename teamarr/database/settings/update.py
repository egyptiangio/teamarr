"""Settings update operations.

Functions to modify settings in the database.

All updaters funnel through a registry-driven UPDATE builder (_apply); the
public functions keep their historical signatures and semantics:
- explicit-keyword functions skip fields left at None
- fields where None is a meaningful "set to NULL" use the _NOT_PROVIDED sentinel
- **kwargs functions silently ignore unknown keys and None values

Group-specific behavior (validation, relayout arming) lives in the wrappers.
"""

import logging
from sqlite3 import Connection
from typing import Any

from .registry import GROUPS

logger = logging.getLogger(__name__)


_NOT_PROVIDED = object()  # Sentinel to distinguish "not provided" from None


def _skip_none(**kwargs: Any) -> dict[str, Any]:
    """Keep only explicitly provided (non-None) fields."""
    return {k: v for k, v in kwargs.items() if v is not None}


def _skip_missing(**kwargs: Any) -> dict[str, Any]:
    """Keep fields unless left at the _NOT_PROVIDED sentinel (None passes through)."""
    return {k: v for k, v in kwargs.items() if v is not _NOT_PROVIDED}


def _known_fields(group_name: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Filter **kwargs to the group's known fields, skipping None values."""
    fields = GROUPS[group_name].field_map
    return {k: v for k, v in kwargs.items() if k in fields and v is not None}


def _apply(conn: Connection, group_name: str, provided: dict[str, Any]) -> bool:
    """Build and execute an UPDATE for the provided fields of a settings group.

    Values are converted via the registry field specs (bool -> int,
    JSON serialization, per-field dump hooks).
    """
    if not provided:
        return False

    group = GROUPS[group_name]
    field_map = group.field_map
    columns: list[str] = []
    values: list[Any] = []
    for name, value in provided.items():
        spec = field_map[name]
        columns.append(spec.column)
        values.append(spec.to_db(value))

    assignments = ", ".join(f"{col} = ?" for col in columns)
    cursor = conn.execute(f"UPDATE settings SET {assignments} WHERE id = 1", values)
    if cursor.rowcount > 0:
        logger.info("[UPDATED] %s settings: %s", group.log_label, columns)
        return True
    return False


def update_dispatcharr_settings(
    conn: Connection,
    enabled: bool | None = None,
    url: str | None = None,
    username: str | None = None,
    password: str | None = None,
    epg_id: int | None = None,
    default_channel_profile_ids: list[int] | None | object = _NOT_PROVIDED,
    default_stream_profile_id: int | None | object = _NOT_PROVIDED,
    default_channel_group_id: int | None | object = _NOT_PROVIDED,
    default_channel_group_mode: str | None | object = _NOT_PROVIDED,
    cleanup_unused_logos: bool | None = None,
) -> bool:
    """Update Dispatcharr settings.

    Only updates fields that are explicitly provided. For the sentinel-guarded
    fields, None is a meaningful value: default_channel_profile_ids None =
    "all profiles" (stored as JSON null); the profile/group ids None = clear
    to SQL NULL.

    Returns:
        True if updated
    """
    provided = _skip_none(
        enabled=enabled,
        url=url,
        username=username,
        password=password,
        epg_id=epg_id,
        cleanup_unused_logos=cleanup_unused_logos,
    ) | _skip_missing(
        default_channel_profile_ids=default_channel_profile_ids,
        default_stream_profile_id=default_stream_profile_id,
        default_channel_group_id=default_channel_group_id,
        default_channel_group_mode=default_channel_group_mode,
    )
    return _apply(conn, "dispatcharr", provided)


def update_scheduler_settings(
    conn: Connection,
    enabled: bool | None = None,
    interval_minutes: int | None = None,
    channel_reset_enabled: bool | None = None,
    channel_reset_cron: str | None | object = _NOT_PROVIDED,
) -> bool:
    """Update scheduler settings (channel_reset_cron: None = clear).

    Returns:
        True if updated
    """
    provided = _skip_none(
        enabled=enabled,
        interval_minutes=interval_minutes,
        channel_reset_enabled=channel_reset_enabled,
    ) | _skip_missing(channel_reset_cron=channel_reset_cron)
    return _apply(conn, "scheduler", provided)


def update_lifecycle_settings(
    conn: Connection,
    channel_create_timing: str | None = None,
    channel_delete_timing: str | None = None,
    channel_pre_buffer_minutes: int | None = None,
    channel_post_buffer_minutes: int | None = None,
    channel_range_start: int | None = None,
    channel_range_end: int | None | object = _NOT_PROVIDED,
) -> bool:
    """Update channel lifecycle settings (channel_range_end: None = no limit).

    Returns:
        True if updated
    """
    provided = _skip_none(
        channel_create_timing=channel_create_timing,
        channel_delete_timing=channel_delete_timing,
        channel_pre_buffer_minutes=channel_pre_buffer_minutes,
        channel_post_buffer_minutes=channel_post_buffer_minutes,
        channel_range_start=channel_range_start,
    ) | _skip_missing(channel_range_end=channel_range_end)

    # A range change only takes effect at re-layout in the sticky modes (locked
    # channels keep their numbers), so arm the one-shot re-grid like gap-size /
    # stability-mode changes do — otherwise moving the range silently does
    # nothing until the daily reset. Compare against current values so the UI's
    # full-PUT of unchanged settings doesn't arm spuriously.
    arm_relayout = False
    if channel_range_start is not None or channel_range_end is not _NOT_PROVIDED:
        try:
            from teamarr.database.channel_numbers import is_sticky_mode

            cur = conn.execute(
                "SELECT channel_range_start, channel_range_end FROM settings WHERE id = 1"
            ).fetchone()
            start_changed = (
                channel_range_start is not None
                and cur is not None
                and channel_range_start != cur["channel_range_start"]
            )
            end_changed = (
                channel_range_end is not _NOT_PROVIDED
                and cur is not None
                and channel_range_end != cur["channel_range_end"]
            )
            arm_relayout = (start_changed or end_changed) and is_sticky_mode(conn)
        except Exception:
            arm_relayout = False

    if not _apply(conn, "lifecycle", provided):
        return False

    if arm_relayout:
        from teamarr.database.channel_numbers import arm_channel_relayout

        if arm_channel_relayout(conn):
            logger.info("[CHANNEL_NUM] Armed one-shot re-grid (channel range changed)")
    return True


def update_epg_settings(conn: Connection, **kwargs) -> bool:
    """Update EPG generation settings (unknown keys and None values ignored).

    Returns:
        True if updated
    """
    return _apply(conn, "epg", _known_fields("epg", kwargs))


def update_reconciliation_settings(conn: Connection, **kwargs) -> bool:
    """Update reconciliation settings (unknown keys and None values ignored).

    Returns:
        True if updated
    """
    return _apply(conn, "reconciliation", _known_fields("reconciliation", kwargs))


def update_duration_settings(conn: Connection, **kwargs) -> bool:
    """Update game duration settings (sport name -> hours; unknown sports ignored).

    Returns:
        True if updated
    """
    return _apply(conn, "durations", _known_fields("durations", kwargs))


def update_display_settings(conn: Connection, **kwargs) -> bool:
    """Update display/formatting settings (unknown keys and None values ignored).

    Returns:
        True if updated
    """
    return _apply(conn, "display", _known_fields("display", kwargs))


def update_team_filter_settings(
    conn: Connection,
    enabled: bool | None = None,
    include_teams: list[dict] | None = None,
    exclude_teams: list[dict] | None = None,
    mode: str | None = None,
    clear_include_teams: bool = False,
    clear_exclude_teams: bool = False,
    bypass_filter_for_playoffs: bool | None = None,
) -> bool:
    """Update default team filtering settings.

    Team lists replace existing values; an empty list or the clear_* flags
    clear the column to NULL.

    Returns:
        True if updated
    """
    provided = _skip_none(
        enabled=enabled,
        mode=mode,
        bypass_filter_for_playoffs=bypass_filter_for_playoffs,
    )
    # The registry dump hook stores falsy team lists as SQL NULL.
    if clear_include_teams:
        provided["include_teams"] = None
    elif include_teams is not None:
        provided["include_teams"] = include_teams
    if clear_exclude_teams:
        provided["exclude_teams"] = None
    elif exclude_teams is not None:
        provided["exclude_teams"] = exclude_teams

    return _apply(conn, "team_filter", provided)


def update_channel_numbering_settings(
    conn: Connection,
    global_channel_mode: str | None = None,
    league_channel_starts: dict | None = None,
    global_consolidation_mode: str | None = None,
    channel_stability_mode: str | None = None,
    channel_gap_size: int | None = None,
    channel_daily_reset_enabled: bool | None = None,
    channel_daily_reset_time: str | None = None,
) -> bool:
    """Update channel numbering and consolidation settings.

    Returns:
        True if updated (False on validation failure)
    """
    # Manual mode retired in v88 (#333) — pinned blocks replace per-league starts.
    if global_channel_mode is not None and global_channel_mode != "auto":
        logger.warning("[CHANNEL_NUM] Invalid global_channel_mode '%s'", global_channel_mode)
        return False
    if global_consolidation_mode is not None and global_consolidation_mode not in (
        "consolidate",
        "separate",
    ):
        logger.warning(
            "[CHANNEL_NUM] Invalid global_consolidation_mode '%s'", global_consolidation_mode
        )
        return False
    if channel_stability_mode is not None and channel_stability_mode not in (
        "compact",
        "gap",
        "strict",
    ):
        logger.warning("[CHANNEL_NUM] Invalid channel_stability_mode '%s'", channel_stability_mode)
        return False

    provided = _skip_none(
        global_channel_mode=global_channel_mode,
        league_channel_starts=league_channel_starts,
        global_consolidation_mode=global_consolidation_mode,
        channel_stability_mode=channel_stability_mode,
        channel_gap_size=max(1, int(channel_gap_size)) if channel_gap_size is not None else None,
        channel_daily_reset_enabled=channel_daily_reset_enabled,
        channel_daily_reset_time=channel_daily_reset_time,
    )
    if not provided:
        return False

    # Auto-arm a one-shot re-grid when a layout-affecting setting actually changes
    # while in (or switching into) a sticky mode — existing locked channels
    # otherwise keep their numbers until the daily reset, so the change would
    # silently do nothing. Compare against current values to avoid arming on every
    # unrelated save (the UI full-PUTs the whole settings object).
    arm_relayout = False
    clear_pending = False
    if channel_gap_size is not None or channel_stability_mode is not None:
        try:
            cur = conn.execute(
                "SELECT channel_stability_mode, channel_gap_size FROM settings WHERE id = 1"
            ).fetchone()
            cur_mode = (cur["channel_stability_mode"] if cur else None) or "compact"
            cur_gap = int((cur["channel_gap_size"] if cur else None) or 3)
            new_mode = channel_stability_mode or cur_mode
            gap_changed = channel_gap_size is not None and int(channel_gap_size) != cur_gap
            mode_changed = (
                channel_stability_mode is not None and channel_stability_mode != cur_mode
            )
            arm_relayout = (gap_changed or mode_changed) and new_mode in ("gap", "strict")
            # Leaving the sticky modes: drop any armed re-grid so it doesn't
            # linger as stale queued state (compact re-sorts every run anyway).
            clear_pending = mode_changed and new_mode == "compact"
        except Exception:
            arm_relayout = False

    if not _apply(conn, "channel_numbering", provided):
        return False

    if arm_relayout:
        from teamarr.database.channel_numbers import arm_channel_relayout

        if arm_channel_relayout(conn):
            logger.info("[CHANNEL_NUM] Armed one-shot re-grid (layout setting changed)")
    elif clear_pending:
        from teamarr.database.channel_numbers import clear_channel_relayout

        if clear_channel_relayout(conn):
            logger.info("[CHANNEL_NUM] Cleared armed re-grid (left sticky mode)")
    return True


def update_stream_ordering_rules(
    conn: Connection,
    rules: list,
) -> bool:
    """Update stream ordering rules (full replacement).

    Args:
        conn: Database connection
        rules: List of rules (dicts or StreamOrderingRule dataclasses).
            Each rule: {"type": str, "value": str, "priority": int,
            "mode": "priority"|"score", "points": int}. Callers omitting
            mode/points get the legacy hard-priority defaults (mode="priority",
            points=0).

    Returns:
        True if updated
    """
    from .types import LEGACY_RULE_MODE, NO_VALUE_RULE_TYPES, VALID_RULE_MODES, VALID_RULE_TYPES
    from .types import StreamOrderingRule as RuleType

    validated_rules = []

    for rule in rules:
        # Handle both dict and dataclass
        if isinstance(rule, RuleType):
            rule_type = rule.type
            rule_value = rule.value
            rule_priority = rule.priority
            rule_mode = rule.mode
            rule_points = rule.points
        else:
            rule_type = rule.get("type")
            rule_value = rule.get("value")
            rule_priority = rule.get("priority")
            # Legacy callers (old UI/API) omit mode/points. Default mode to
            # LEGACY_RULE_MODE ('priority') so re-saving pre-migration rules keeps
            # their hard first-match behaviour rather than silently flipping to score.
            rule_mode = rule.get("mode", LEGACY_RULE_MODE)
            rule_points = rule.get("points", 0)

        if rule_type not in VALID_RULE_TYPES:
            logger.warning(
                "[STREAM_ORDER] Invalid rule type '%s', must be one of %s",
                rule_type,
                VALID_RULE_TYPES,
            )
            continue

        if rule_type not in NO_VALUE_RULE_TYPES and (
            not rule_value or not isinstance(rule_value, str)
        ):
            logger.warning("[STREAM_ORDER] Rule missing value, skipping")
            continue

        if not isinstance(rule_priority, int) or rule_priority < 1 or rule_priority > 99:
            logger.warning(
                "[STREAM_ORDER] Invalid priority %s, must be 1-99, defaulting to 99",
                rule_priority,
            )
            rule_priority = 99

        if rule_mode not in VALID_RULE_MODES:
            logger.warning(
                "[STREAM_ORDER] Invalid mode %r, must be one of %s, defaulting to %r",
                rule_mode,
                VALID_RULE_MODES,
                LEGACY_RULE_MODE,
            )
            rule_mode = LEGACY_RULE_MODE

        # bool is an int subclass; reject it explicitly so True/False can't become points.
        if isinstance(rule_points, bool) or not isinstance(rule_points, int):
            logger.warning(
                "[STREAM_ORDER] Invalid points %r, must be an int, defaulting to 0",
                rule_points,
            )
            rule_points = 0

        validated_rules.append(
            {
                "type": rule_type,
                "value": rule_value,
                "priority": rule_priority,
                "mode": rule_mode,
                "points": rule_points,
            }
        )

    if _apply(conn, "stream_ordering", {"rules": validated_rules}):
        logger.info("[STREAM_ORDER] Updated %d rules", len(validated_rules))
        return True
    return False


def update_update_check_settings(
    conn: Connection,
    enabled: bool | None = None,
    notify_stable: bool | None = None,
    notify_dev: bool | None = None,
    github_owner: str | None = None,
    github_repo: str | None = None,
    dev_branch: str | None = None,
    auto_detect_branch: bool | None = None,
) -> bool:
    """Update update check settings.

    Returns:
        True if updated
    """
    provided = _skip_none(
        enabled=enabled,
        notify_stable=notify_stable,
        notify_dev=notify_dev,
        github_owner=github_owner,
        github_repo=github_repo,
        dev_branch=dev_branch,
        auto_detect_branch=auto_detect_branch,
    )
    return _apply(conn, "update_check", provided)


def update_feed_separation_settings(
    conn: Connection,
    enabled: bool | None = None,
    sports: list[str] | None = None,
    home_terms: list[str] | None = None,
    away_terms: list[str] | None = None,
    detect_team_names: bool | None = None,
    label_style: str | None = None,
) -> bool:
    """Update feed separation settings.

    Returns:
        True if updated (False on invalid label_style or unknown sport code)
    """
    if label_style is not None:
        valid_styles = ("team_name", "short_name", "home_away")
        if label_style not in valid_styles:
            logger.warning(
                "[FEED_SEP] Invalid label_style '%s', must be one of %s",
                label_style,
                valid_styles,
            )
            return False

    if sports:
        # An unknown code would never match a real event, silently disabling
        # separation everywhere rather than failing visibly (#732).
        known = {
            row["sport_code"] for row in conn.execute("SELECT sport_code FROM sports")
        }
        unknown = sorted(set(sports) - known)
        if unknown:
            logger.warning(
                "[FEED_SEP] Unknown sport code(s) %s, must be one of %s",
                unknown,
                sorted(known),
            )
            return False

    provided = _skip_none(
        enabled=enabled,
        sports=sports,
        home_terms=home_terms,
        away_terms=away_terms,
        detect_team_names=detect_team_names,
        label_style=label_style,
    )
    return _apply(conn, "feed_separation", provided)


def update_emby_settings(
    conn: Connection,
    enabled: bool | None = None,
    servers: list[dict] | None = None,
) -> bool:
    """Update Emby integration settings (only provided fields).

    `servers` replaces the whole list (full-replace semantics). Masked
    secrets must be resolved by the caller (API route) before this point.

    Returns:
        True if updated
    """
    provided = _skip_none(enabled=enabled, servers=servers)
    return _apply(conn, "emby", provided)


def update_jellyfin_settings(
    conn: Connection,
    enabled: bool | None = None,
    servers: list[dict] | None = None,
) -> bool:
    """Update Jellyfin integration settings (only provided fields).

    `servers` replaces the whole list (full-replace semantics). Masked
    secrets must be resolved by the caller (API route) before this point.

    Returns:
        True if updated
    """
    provided = _skip_none(enabled=enabled, servers=servers)
    return _apply(conn, "jellyfin", provided)


def update_channelsdvr_settings(
    conn: Connection,
    enabled: bool | None = None,
    servers: list[dict] | None = None,
) -> bool:
    """Update Channels DVR integration settings (only provided fields).

    `servers` replaces the whole list (full-replace semantics, like
    stream-ordering rules): pass every server entry, not a delta.

    Returns:
        True if updated
    """
    provided = _skip_none(enabled=enabled, servers=servers)
    return _apply(conn, "channelsdvr", provided)


def update_proxy_settings(
    conn: Connection,
    enabled: bool | None = None,
    url: str | None | object = _NOT_PROVIDED,
    user_agent: str | None | object = _NOT_PROVIDED,
    excluded_providers: list[str] | None | object = _NOT_PROVIDED,
) -> bool:
    """Update provider SOCKS5 proxy settings (only provided fields)."""
    provided = _skip_none(enabled=enabled)
    provided.update(
        _skip_missing(
            url=url,
            user_agent=user_agent,
            excluded_providers=excluded_providers,
        )
    )
    return _apply(conn, "proxy", provided)


def update_backup_settings(
    conn: Connection,
    enabled: bool | None = None,
    cron: str | None = None,
    max_count: int | None = None,
    path: str | None = None,
) -> bool:
    """Update scheduled backup settings.

    Returns:
        True if updated (False on invalid cron or max_count)
    """
    if cron is not None:
        from croniter import croniter

        try:
            croniter(cron)
        except (KeyError, ValueError) as e:
            logger.warning("[BACKUP] Invalid cron expression '%s': %s", cron, e)
            return False
    if max_count is not None and max_count < 1:
        logger.warning("[BACKUP] max_count must be at least 1, got %d", max_count)
        return False

    provided = _skip_none(enabled=enabled, cron=cron, max_count=max_count, path=path)
    return _apply(conn, "backup", provided)
