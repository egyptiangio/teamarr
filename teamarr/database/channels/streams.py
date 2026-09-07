"""Stream management operations for managed channels.

CRUD operations for managed_channel_streams table.
"""

import json
import logging
import re
from sqlite3 import Connection

from teamarr.database.aliases import list_aliases
from teamarr.dispatcharr.factory import get_dispatcharr_client

from .types import ManagedChannelStream

logger = logging.getLogger(__name__)


def add_stream_to_channel(
    conn: Connection,
    managed_channel_id: int,
    dispatcharr_stream_id: int,
    stream_name: str | None = None,
    priority: int = 0,
    **kwargs,
) -> int:
    """Add a stream to a managed channel.

    Args:
        conn: Database connection
        managed_channel_id: Channel ID
        dispatcharr_stream_id: Stream ID in Dispatcharr
        stream_name: Stream display name
        priority: Stream priority (0 = primary)
        **kwargs: Additional fields

    Returns:
        ID of created stream record
    """
    columns = ["managed_channel_id", "dispatcharr_stream_id", "priority"]
    values: list = [managed_channel_id, dispatcharr_stream_id, priority]

    if stream_name:
        columns.append("stream_name")
        values.append(stream_name)

    allowed_fields = [
        "source_group_id",
        "source_group_type",
        "m3u_account_id",
        "m3u_account_name",
        "exception_keyword",
        "match_type",
        "match_method",  # how matched ('epg', 'fuzzy', …); drives the epg_match ordering rule
        "feed_team_id",  # resolved feed/matched team; drives team_feed rules (#489)
        "feed_side",  # 'home'/'away'; NULL = unknown. Drives home_feed/away_feed rules (#533)
        "dispatcharr_channel_group",  # DP channel group; drives dispatcharr_group rule (ybt.3)
        "dispatcharr_channel_group_id",  # Stable DP channel group id for profile overrides
        "attach_at",   # time-windowed membership (183.5); None = full-life
        "detach_at",
    ]

    for field_name in allowed_fields:
        if field_name in kwargs and kwargs[field_name] is not None:
            columns.append(field_name)
            values.append(kwargs[field_name])

    placeholders = ", ".join(["?"] * len(values))
    column_str = ", ".join(columns)

    cursor = conn.execute(
        f"INSERT INTO managed_channel_streams ({column_str}) VALUES ({placeholders})",
        values,
    )
    stream_id = cursor.lastrowid
    assert stream_id is not None  # just-inserted row always has a rowid
    logger.debug(
        "[ATTACHED] Stream %d to channel %d priority=%d",
        dispatcharr_stream_id,
        managed_channel_id,
        priority,
    )
    return stream_id


def remove_stream_from_channel(
    conn: Connection,
    managed_channel_id: int,
    dispatcharr_stream_id: int,
    reason: str | None = None,
) -> bool:
    """Soft-remove a stream from a channel.

    Args:
        conn: Database connection
        managed_channel_id: Channel ID
        dispatcharr_stream_id: Stream ID
        reason: Removal reason

    Returns:
        True if removed, False if not found
    """
    cursor = conn.execute(
        """UPDATE managed_channel_streams
           SET removed_at = datetime('now'),
               remove_reason = ?
           WHERE managed_channel_id = ?
             AND dispatcharr_stream_id = ?
             AND removed_at IS NULL""",
        (reason, managed_channel_id, dispatcharr_stream_id),
    )
    if cursor.rowcount > 0:
        logger.debug(
            "[DETACHED] Stream %d from channel %d reason=%s",
            dispatcharr_stream_id,
            managed_channel_id,
            reason,
        )
        return True
    return False


def get_channel_streams(
    conn: Connection,
    managed_channel_id: int,
    include_removed: bool = False,
) -> list[ManagedChannelStream]:
    """Get all streams for a channel.

    Args:
        conn: Database connection
        managed_channel_id: Channel ID
        include_removed: Whether to include removed streams

    Returns:
        List of ManagedChannelStream objects (ordered by priority)
    """
    if include_removed:
        cursor = conn.execute(
            """SELECT * FROM managed_channel_streams
               WHERE managed_channel_id = ?
               ORDER BY priority, added_at""",
            (managed_channel_id,),
        )
    else:
        cursor = conn.execute(
            """SELECT * FROM managed_channel_streams
               WHERE managed_channel_id = ? AND removed_at IS NULL
               ORDER BY priority, added_at""",
            (managed_channel_id,),
        )
    return [ManagedChannelStream.from_row(dict(row)) for row in cursor.fetchall()]


def stream_exists_on_channel(
    conn: Connection,
    managed_channel_id: int,
    dispatcharr_stream_id: int,
) -> bool:
    """Check if stream is attached to channel.

    Args:
        conn: Database connection
        managed_channel_id: Channel ID
        dispatcharr_stream_id: Stream ID

    Returns:
        True if stream exists on channel
    """
    cursor = conn.execute(
        """SELECT 1 FROM managed_channel_streams
           WHERE managed_channel_id = ?
             AND dispatcharr_stream_id = ?
             AND removed_at IS NULL""",
        (managed_channel_id, dispatcharr_stream_id),
    )
    return cursor.fetchone() is not None


def compute_stream_priority_from_rules(
    conn: Connection,
    stream_name: str | None = None,
    m3u_account_name: str | None = None,
    source_group_id: int | None = None,
    match_type: str = "event",
    match_method: str | None = None,
    dispatcharr_channel_group: str | None = None,
    feed_team_id: str | None = None,
    feed_side: str | None = None,
) -> int:
    """Compute priority for a stream based on ordering rules.

    If rules are defined, computes priority based on first matching rule.
    If no rules or no match, returns 999 (sort to end).

    Callers should pass every field they have (#379): rule types match on
    match_type (stream_type rules), match_method (epg_match rules),
    dispatcharr_channel_group (dispatcharr_group rules), feed_team_id
    (team_feed/not_team_feed rules, #489), and feed_side (home_feed/away_feed
    rules, #533) — omitting them makes those rules silently non-matching at
    attach time, so the order pushed to Dispatcharr is wrong until the
    end-of-generation reorder pass corrects it.

    Args:
        conn: Database connection
        stream_name: Stream display name (for regex matching)
        m3u_account_name: M3U account name (for m3u type matching)
        source_group_id: Source group ID (for group type matching)
        match_type: 'event' or 'team' (for stream_type rules)
        match_method: 'epg', 'fuzzy', etc. (for epg_match rules)
        dispatcharr_channel_group: DP channel group name (for dispatcharr_group rules)
        feed_team_id: Resolved feed/matched team id (for team_feed rules)
        feed_side: 'home'/'away', or None = unknown (for home_feed/away_feed
            rules). Unknown matches neither rule — never coerced to a side.

    Returns:
        Computed priority (lower = higher priority)
    """
    from teamarr.database.channels.types import ManagedChannelStream
    from teamarr.services.stream_ordering import get_stream_ordering_service

    ordering_service = get_stream_ordering_service(conn)
    if not ordering_service.rules:
        # No rules - use sequential ordering (will be assigned by get_next_stream_priority)
        return None  # type: ignore

    # Create a temporary stream object for matching
    temp_stream = ManagedChannelStream(
        id=0,
        managed_channel_id=0,
        dispatcharr_stream_id=0,
        stream_name=stream_name,
        m3u_account_name=m3u_account_name,
        source_group_id=source_group_id,
        match_type=match_type,
        match_method=match_method,
        dispatcharr_channel_group=dispatcharr_channel_group,
        feed_team_id=feed_team_id,
        feed_side=feed_side,
    )

    return ordering_service.compute_priority(temp_stream)


def get_next_stream_priority(conn: Connection, managed_channel_id: int) -> int:
    """Get the next available stream priority for a channel.

    Args:
        conn: Database connection
        managed_channel_id: Channel ID

    Returns:
        Next priority number (max + 1, or 0 if no streams)
    """
    cursor = conn.execute(
        """SELECT COALESCE(MAX(priority), -1) + 1 FROM managed_channel_streams
           WHERE managed_channel_id = ? AND removed_at IS NULL""",
        (managed_channel_id,),
    )
    return cursor.fetchone()[0]


def update_stream_name(
    conn: Connection,
    managed_channel_id: int,
    dispatcharr_stream_id: int,
    new_name: str,
) -> bool:
    """Update the stored stream name for an active stream record.

    Used when a stream's name changes but it's still matched to the same event
    (e.g., provider renamed the stream). Keeps the stream record in sync.

    Args:
        conn: Database connection
        managed_channel_id: Channel that owns the stream
        dispatcharr_stream_id: Stream ID in Dispatcharr
        new_name: New stream name to store

    Returns:
        True if updated, False if not found
    """
    cursor = conn.execute(
        """UPDATE managed_channel_streams
           SET stream_name = ?
           WHERE managed_channel_id = ? AND dispatcharr_stream_id = ? AND removed_at IS NULL""",
        (new_name, managed_channel_id, dispatcharr_stream_id),
    )
    if cursor.rowcount > 0:
        logger.debug(
            "Updated stream name for channel=%d stream=%d: %s",
            managed_channel_id,
            dispatcharr_stream_id,
            new_name,
        )
    return cursor.rowcount > 0


def update_stream_channel_source_group(
    conn: Connection,
    managed_channel_id: int,
    dispatcharr_stream_id: int,
    dispatcharr_channel_group_id: int,
) -> bool:
    """Backfill channel-source group metadata for an active stream.

    Only channel-source candidates supply this immutable Dispatcharr group id;
    ordinary M3U/name-matched streams must remain NULL.
    """
    cursor = conn.execute(
        """UPDATE managed_channel_streams
           SET dispatcharr_channel_group_id = ?
           WHERE managed_channel_id = ? AND dispatcharr_stream_id = ? AND removed_at IS NULL""",
        (dispatcharr_channel_group_id, managed_channel_id, dispatcharr_stream_id),
    )
    return cursor.rowcount > 0


def update_stream_account_name(
    conn: Connection,
    managed_channel_id: int,
    dispatcharr_stream_id: int,
    account_name: str,
    account_id: int | None = None,
) -> bool:
    """Refresh the stored M3U account name (and optionally id) for an active stream.

    Self-heal for #297: rows attached before per-stream account resolution carry
    the group's single configured account name, mislabeling multi-login streams.
    Called each generation for already-attached streams; the WHERE guard makes it
    a no-op when the stored values already match.

    Returns:
        True if a row was updated (values actually changed), False otherwise
    """
    cursor = conn.execute(
        """UPDATE managed_channel_streams
           SET m3u_account_name = ?,
               m3u_account_id = COALESCE(?, m3u_account_id)
           WHERE managed_channel_id = ? AND dispatcharr_stream_id = ?
             AND removed_at IS NULL
             AND (m3u_account_name IS NOT ?
                  OR (? IS NOT NULL AND m3u_account_id IS NOT ?))""",
        (
            account_name,
            account_id,
            managed_channel_id,
            dispatcharr_stream_id,
            account_name,
            account_id,
            account_id,
        ),
    )
    if cursor.rowcount > 0:
        logger.debug(
            "Updated M3U account for channel=%d stream=%d: %s",
            managed_channel_id,
            dispatcharr_stream_id,
            account_name,
        )
    return cursor.rowcount > 0


def update_stream_feed_team(
    conn: Connection,
    managed_channel_id: int,
    dispatcharr_stream_id: int,
    feed_team_id: str,
) -> bool:
    """Backfill/refresh the resolved feed team of an attached stream (#489).

    Rows attached before the feed_team_id column existed carry NULL; called
    each generation for already-attached streams so team_feed/not_team_feed
    ordering rules see the resolved team without waiting for re-attach.
    Guarded on a resolved value — never nulls on a transient resolution miss.

    Returns:
        True if a row was updated (value actually changed), False otherwise
    """
    cursor = conn.execute(
        """UPDATE managed_channel_streams
           SET feed_team_id = ?
           WHERE managed_channel_id = ? AND dispatcharr_stream_id = ?
             AND removed_at IS NULL
             AND feed_team_id IS NOT ?""",
        (
            feed_team_id,
            managed_channel_id,
            dispatcharr_stream_id,
            feed_team_id,
        ),
    )
    if cursor.rowcount > 0:
        logger.debug(
            "[FEED] Stream %d on channel %d feed_team_id -> %s",
            dispatcharr_stream_id,
            managed_channel_id,
            feed_team_id,
        )
    return cursor.rowcount > 0


def update_stream_feed_side(
    conn: Connection,
    managed_channel_id: int,
    dispatcharr_stream_id: int,
    feed_side: str,
) -> bool:
    """Backfill/refresh the resolved feed side of an attached stream (#533).

    Rows attached before the feed_side column existed carry NULL (unknown);
    called each generation for already-attached streams so home_feed/away_feed
    ordering rules see the resolved side without waiting for re-attach.

    Guarded on a resolved value — the caller must not pass None. An unknown
    side is left as NULL rather than written, so a transient resolution miss
    can never overwrite a good row, and NULL keeps meaning "we don't know"
    instead of being confused with a negative answer.

    Returns:
        True if a row was updated (value actually changed), False otherwise
    """
    cursor = conn.execute(
        """UPDATE managed_channel_streams
           SET feed_side = ?
           WHERE managed_channel_id = ? AND dispatcharr_stream_id = ?
             AND removed_at IS NULL
             AND feed_side IS NOT ?""",
        (
            feed_side,
            managed_channel_id,
            dispatcharr_stream_id,
            feed_side,
        ),
    )
    if cursor.rowcount > 0:
        logger.debug(
            "[FEED] Stream %d on channel %d feed_side -> %s",
            dispatcharr_stream_id,
            managed_channel_id,
            feed_side,
        )
    return cursor.rowcount > 0


def update_stream_priority(
    conn: Connection,
    stream_db_id: int,
    new_priority: int,
) -> bool:
    """Update the priority of a stream.

    Args:
        conn: Database connection
        stream_db_id: Stream record ID in managed_channel_streams
        new_priority: New priority value

    Returns:
        True if updated, False if not found
    """
    cursor = conn.execute(
        """UPDATE managed_channel_streams
           SET priority = ?
           WHERE id = ?""",
        (new_priority, stream_db_id),
    )
    return cursor.rowcount > 0


def update_stream_window(
    conn: Connection,
    managed_channel_id: int,
    dispatcharr_stream_id: int,
    attach_at: str | None,
    detach_at: str | None,
) -> bool:
    """Refresh the time-window (attach_at/detach_at) of an attached stream.

    Used by epic teamarrv2-183.5 (bead teamarrv2-095): the window is recomputed
    every generation run from the fresh EPG program slot + current buffers, so a
    change to epg_stream_pre/post_buffer_minutes takes effect on already-attached
    streams instead of only at first attach. Targets the active (not removed) row.

    Returns True if a row was updated (i.e. the values actually changed), False
    otherwise.
    """
    cursor = conn.execute(
        """UPDATE managed_channel_streams
           SET attach_at = ?, detach_at = ?
           WHERE managed_channel_id = ?
             AND dispatcharr_stream_id = ?
             AND removed_at IS NULL
             AND (attach_at IS NOT ? OR detach_at IS NOT ?)""",
        (
            attach_at,
            detach_at,
            managed_channel_id,
            dispatcharr_stream_id,
            attach_at,
            detach_at,
        ),
    )
    if cursor.rowcount > 0:
        logger.debug(
            "[WINDOW] Stream %d on channel %d window -> attach=%s detach=%s",
            dispatcharr_stream_id,
            managed_channel_id,
            attach_at,
            detach_at,
        )
        return True
    return False


def reorder_channel_streams(
    conn: Connection,
    managed_channel_id: int,
) -> int:
    """Reorder streams on a channel based on stream ordering rules.

    Fetches stream ordering rules from settings and recomputes priority
    for all active streams on the channel.

    Args:
        conn: Database connection
        managed_channel_id: Channel ID

    Returns:
        Number of streams reordered
    """
    from teamarr.services.stream_ordering import get_stream_ordering_service

    # Get current streams
    streams = get_channel_streams(conn, managed_channel_id)
    if not streams:
        return 0

    # Get ordering service with rules
    ordering_service = get_stream_ordering_service(conn)
    if not ordering_service.rules:
        # No rules defined - skip reordering
        return 0

    # Compute priorities and update
    updated_count = 0
    for stream in streams:
        new_priority = ordering_service.compute_priority(stream)
        if stream.priority != new_priority:
            update_stream_priority(conn, stream.id, new_priority)
            updated_count += 1
            logger.debug(
                "[REORDER] Stream %d priority %d -> %d",
                stream.dispatcharr_stream_id,
                stream.priority,
                new_priority,
            )

    if updated_count > 0:
        logger.info(
            "[REORDER] Reordered %d/%d streams on channel %d",
            updated_count,
            len(streams),
            managed_channel_id,
        )

    return updated_count


# Dispatcharr's by-ids endpoint is requested with page_size=1000, so a longer id
# list would be silently truncated at the page boundary — every stream past the
# first thousand would keep whatever stats it already had, which reads as "the
# refresh ran" rather than as an error. Chunk to match.
STATS_BATCH_SIZE = 1000


def get_active_dispatcharr_stream_ids(conn: Connection) -> list[int]:
    """Distinct Dispatcharr stream ids attached to channels Teamarr still owns.

    The population an ordering pass is about to score: streams still on a
    channel (``removed_at IS NULL``) whose channel has not been soft-deleted.
    Distinct because one stream is routinely attached to several event channels
    and only needs fetching once.
    """
    cursor = conn.execute(
        """SELECT DISTINCT s.dispatcharr_stream_id
           FROM managed_channel_streams s
           JOIN managed_channels mc ON mc.id = s.managed_channel_id
           WHERE s.removed_at IS NULL
             AND mc.deleted_at IS NULL
             AND s.dispatcharr_stream_id IS NOT NULL"""
    )
    return [row[0] for row in cursor.fetchall()]


def refresh_stream_stats_bulk(conn: Connection, stream_ids: list[int]) -> int:
    """Fetch and cache stream_stats for many Dispatcharr streams in one pass.

    Pulls from Dispatcharr's ``/api/channels/streams/by-ids/`` endpoint in
    chunks of ``STATS_BATCH_SIZE`` and writes the ``stream_stats`` /
    ``stream_stats_updated_at`` columns. Streams Dispatcharr has not probed
    return ``stream_stats=null`` and are left unchanged rather than blanked —
    absent stats and stats that read zero mean different things to a
    ``stats_metric`` rule, and only one of them is true.

    The write is keyed on ``dispatcharr_stream_id`` alone, not on a channel: a
    stream sitting on six event channels is fetched once and lands on all six
    rows. That is the whole reason this exists next to the per-channel call —
    ordering a full catalogue through the single-channel path costs one HTTP
    round trip per channel, which is why the ordering pass never made it.

    Args:
        conn: Database connection
        stream_ids: Dispatcharr stream ids to refresh

    Returns:
        Number of distinct streams whose stats were written
    """
    ids = sorted({int(sid) for sid in stream_ids if sid is not None})
    if not ids:
        return 0

    client = get_dispatcharr_client()
    if client is None:
        return 0

    updated = 0
    for start in range(0, len(ids), STATS_BATCH_SIZE):
        chunk = ids[start : start + STATS_BATCH_SIZE]
        stats_list = client.get_stream_stats_by_ids(chunk)
        if not stats_list:
            continue
        for entry in stats_list:
            sid = entry.get("id")
            raw_stats = entry.get("stream_stats")
            updated_at = entry.get("stream_stats_updated_at")
            if sid is None or raw_stats is None:
                continue
            stats_json = json.dumps(raw_stats) if isinstance(raw_stats, dict) else raw_stats
            result = conn.execute(
                """UPDATE managed_channel_streams
                   SET stream_stats = ?, stream_stats_updated_at = ?
                   WHERE dispatcharr_stream_id = ? AND removed_at IS NULL""",
                (stats_json, updated_at, sid),
            )
            if result.rowcount > 0:
                updated += 1

    if updated:
        logger.debug(
            "[STREAM STATS] Updated stats for %d/%d stream(s) in %d batch(es)",
            updated,
            len(ids),
            (len(ids) + STATS_BATCH_SIZE - 1) // STATS_BATCH_SIZE,
        )
    return updated


def refresh_stream_stats(conn: Connection, managed_channel_id: int) -> int:
    """Fetch and cache stream_stats from Dispatcharr for a managed channel's active streams.

    Thin wrapper over :func:`refresh_stream_stats_bulk` scoped to one channel's
    streams. Those streams may also be attached elsewhere, so the refresh can
    touch rows on other channels too — that is a strictly fresher cache for the
    same single fetch, never a stale one.

    Args:
        conn: Database connection
        managed_channel_id: Managed channel whose streams should be refreshed

    Returns:
        Number of streams whose stats were updated
    """

    cursor = conn.execute(
        """SELECT dispatcharr_stream_id FROM managed_channel_streams
           WHERE managed_channel_id = ? AND removed_at IS NULL""",
        (managed_channel_id,),
    )
    stream_ids = [row[0] for row in cursor.fetchall()]
    if not stream_ids:
        return 0

    return refresh_stream_stats_bulk(conn, stream_ids)


def get_stream_match_details(
    conn: Connection, pairs: list[tuple[int, int]]
) -> dict[tuple[int, int], dict]:
    """Fetch cached match details for (source_group_id, stream_id) pairs.

    Reads stream_match_cache to explain how a stream matched its event: the
    matched event name/league, the finer match method, and any user correction.
    Returns the most recent entry per pair. Pairs with no cache row (e.g. EPG or
    dedicated/exact matches, which don't use the fingerprint cache) are absent.

    Each value dict has: event_name, league, match_method, user_corrected,
    corrected_at, created_at, aliases (list of {alias, team} for alias matches),
    patterns (list of {token, team} for pattern matches).
    """
    if not pairs:
        return {}

    group_ids = {g for g, _ in pairs}
    stream_ids = {s for _, s in pairs}
    gp = ",".join("?" * len(group_ids))
    sp = ",".join("?" * len(stream_ids))
    rows = conn.execute(
        f"""SELECT group_id, stream_id, stream_name, event_id, league,
                   cached_event_data, match_method, user_corrected, corrected_at,
                   created_at
            FROM stream_match_cache
            WHERE group_id IN ({gp}) AND stream_id IN ({sp})
            ORDER BY updated_at ASC""",
        [*group_ids, *stream_ids],
    ).fetchall()

    wanted = set(pairs)
    aliases_by_league: dict[str, list] = {}  # memoize per league within this call
    out: dict[tuple[int, int], dict] = {}
    for r in rows:
        key = (r["group_id"], r["stream_id"])
        if key not in wanted or r["event_id"] == "__FAILED__":
            continue
        data = {}
        event_name = None
        if r["cached_event_data"]:
            try:
                data = json.loads(r["cached_event_data"])
                event_name = data.get("name") or data.get("short_name")
            except (ValueError, AttributeError):
                data = {}
        # Newer rows overwrite older ones (rows are ordered oldest-first).
        out[key] = {
            "event_name": event_name,
            "league": r["league"],
            "match_method": r["match_method"],
            "user_corrected": bool(r["user_corrected"]),
            "corrected_at": r["corrected_at"],
            "created_at": r["created_at"],
            "aliases": _reconstruct_aliases(conn, r, data, aliases_by_league),
            "patterns": _reconstruct_patterns(r, data),
        }
    return out


def _reconstruct_aliases(
    conn: Connection, row, event_data: dict, cache: dict[str, list]
) -> list[dict]:
    """Find which user-defined alias(es) produced an alias match, for display.

    Returns [{alias, team}] for aliases whose text appears in the cached stream
    name and whose team is one of the matched event's teams. Non-alias matches
    return []. Best-effort (substring match), purely informational.
    """
    if row["match_method"] != "alias" or not event_data:
        return []

    home = event_data.get("home_team") or {}
    away = event_data.get("away_team") or {}
    team_ids = {str(home.get("id")), str(away.get("id"))} - {"None"}
    if not team_ids:
        return []
    league = (row["league"] or "").lower()
    if league not in cache:
        cache[league] = list_aliases(conn, league=league)
    name_lower = (row["stream_name"] or "").lower()
    return [
        {"alias": a.alias, "team": a.team_name}
        for a in cache[league]
        if str(a.team_id) in team_ids and a.alias.lower() in name_lower
    ]


def _reconstruct_patterns(row, event_data: dict) -> list[dict]:
    """Find which team-name form produced a pattern match, for display.

    Returns [{token, team}] for the matched event's teams whose name / short
    name / abbreviation appears in the cached stream name. Non-pattern matches
    return []. Best-effort and purely informational: the longest (most specific)
    form is preferred, and abbreviations match only on a word boundary to avoid
    short-token false positives.
    """
    if row["match_method"] != "pattern" or not event_data:
        return []
    name_lower = (row["stream_name"] or "").lower()
    if not name_lower:
        return []

    out: list[dict] = []
    for side in ("home_team", "away_team"):
        team = event_data.get(side) or {}
        team_name = team.get("name")
        if not team_name:
            continue
        token = None
        for cand in (team.get("name"), team.get("short_name")):
            if cand and cand.lower() in name_lower:
                token = cand
                break
        if not token:
            abbr = team.get("abbreviation")
            if abbr and re.search(rf"\b{re.escape(abbr.lower())}\b", name_lower):
                token = abbr
        if token:
            out.append({"token": token, "team": team_name})
    return out


def clear_stream_stats(conn: Connection, group_id: int | None = None) -> int:
    """Null cached stream stats so they're freshly pulled from Dispatcharr next run.

    Called when a group's match cache is cleared. With ``group_id`` set, scopes to
    streams sourced from that event group (managed_channel_streams.source_group_id);
    with ``group_id=None``, clears every active stream (the clear-all path). The
    "already null" guard keeps the returned count to rows that actually changed.

    Args:
        conn: Database connection
        group_id: Event group ID to scope to, or None to clear all active streams

    Returns:
        Number of stream rows whose stats were cleared
    """
    where = (
        "removed_at IS NULL "
        "AND (stream_stats IS NOT NULL OR stream_stats_updated_at IS NOT NULL)"
    )
    params: tuple = ()
    if group_id is not None:
        where = "source_group_id = ? AND " + where
        params = (group_id,)
    cursor = conn.execute(
        f"UPDATE managed_channel_streams "
        f"SET stream_stats = NULL, stream_stats_updated_at = NULL WHERE {where}",
        params,
    )
    return cursor.rowcount


def get_all_channel_streams(
    conn: Connection,
) -> dict[int, list[ManagedChannelStream]]:
    """Every active stream on every managed channel, grouped by channel id.

    The bulk form of :func:`get_channel_streams` (#735). Callers that walk all
    managed channels — stream ordering, the post-run audit — were issuing one
    query per channel; on an install with several hundred channels that is a
    few thousand round trips through SQLite for data one scan already has.

    Channels with no active streams are simply absent, matching what the
    per-channel call returns for them (an empty list).

    Returns:
        ``{managed_channel_id: [ManagedChannelStream, ...]}`` in priority order.
    """
    cursor = conn.execute(
        """SELECT * FROM managed_channel_streams
           WHERE removed_at IS NULL
           ORDER BY managed_channel_id, priority, added_at"""
    )
    grouped: dict[int, list[ManagedChannelStream]] = {}
    for row in cursor.fetchall():
        stream = ManagedChannelStream.from_row(dict(row))
        grouped.setdefault(stream.managed_channel_id, []).append(stream)
    return grouped


def get_all_ordered_stream_ids(
    conn: Connection,
    now: str | None = None,
) -> dict[int, list[int]]:
    """The ACTIVE, priority-ordered stream ids for every managed channel.

    The bulk form of :func:`get_ordered_stream_ids` (#735), running the exact
    same window predicate in one scan so the two cannot drift apart. Pass an
    explicit ``now`` to evaluate every channel's attach/detach window at ONE
    instant — walking channels one at a time re-reads the clock per channel, so
    a long ordering pass could open a window partway through and treat two
    channels sharing a stream inconsistently.

    Returns:
        ``{managed_channel_id: [dispatcharr_stream_id, ...]}``; channels whose
        active set is empty are absent (callers read them as an empty list).
    """
    now_expr = "datetime('now')" if now is None else "?"
    params: tuple = () if now is None else (now, now)
    cursor = conn.execute(
        f"""SELECT managed_channel_id, dispatcharr_stream_id
           FROM managed_channel_streams
           WHERE removed_at IS NULL
             AND (attach_at IS NULL
                  OR (attach_at <= {now_expr} AND {now_expr} < detach_at))
           ORDER BY managed_channel_id, priority, added_at""",
        params,
    )
    grouped: dict[int, list[int]] = {}
    for channel_id, stream_id in cursor.fetchall():
        grouped.setdefault(channel_id, []).append(stream_id)
    return grouped


def get_ordered_stream_ids(
    conn: Connection,
    managed_channel_id: int,
    now: str | None = None,
) -> list[int]:
    """Get the ACTIVE stream IDs for a channel in priority order.

    This is the set pushed to Dispatcharr. It honors time-windowed membership
    (epic teamarrv2-183.5): a stream is active when it has no window
    (attach_at IS NULL — full-life, the default) OR the current time is inside
    its window (attach_at <= now < detach_at). Out-of-window time-shared linear
    streams are excluded so they swap out of the channel until their next slot.

    Args:
        conn: Database connection
        managed_channel_id: Channel ID
        now: UTC "YYYY-MM-DD HH:MM:SS" timestamp for window gating. Defaults to
            SQLite datetime('now') (UTC). Pass explicitly for deterministic tests
            and to share one instant across a generation run.

    Returns:
        List of dispatcharr_stream_id values in priority order
    """
    now_expr = "datetime('now')" if now is None else "?"
    params: tuple = (
        (managed_channel_id,) if now is None else (managed_channel_id, now, now)
    )
    cursor = conn.execute(
        f"""SELECT dispatcharr_stream_id FROM managed_channel_streams
           WHERE managed_channel_id = ? AND removed_at IS NULL
             AND (attach_at IS NULL
                  OR (attach_at <= {now_expr} AND {now_expr} < detach_at))
           ORDER BY priority, added_at""",
        params,
    )
    return [row[0] for row in cursor.fetchall()]
