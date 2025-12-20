"""Settings helper functions for channels.

Helper functions that read settings related to channels.
"""

from sqlite3 import Connection


def get_dispatcharr_settings(conn: Connection) -> dict:
    """Get Dispatcharr integration settings.

    Args:
        conn: Database connection

    Returns:
        Dict with enabled, url, username, password, epg_id
    """
    cursor = conn.execute(
        """SELECT dispatcharr_enabled, dispatcharr_url, dispatcharr_username,
                  dispatcharr_password, dispatcharr_epg_id
           FROM settings WHERE id = 1"""
    )
    row = cursor.fetchone()
    if not row:
        return {
            "enabled": False,
            "url": None,
            "username": None,
            "password": None,
            "epg_id": None,
        }
    return {
        "enabled": bool(row["dispatcharr_enabled"]),
        "url": row["dispatcharr_url"],
        "username": row["dispatcharr_username"],
        "password": row["dispatcharr_password"],
        "epg_id": row["dispatcharr_epg_id"],
    }


def get_reconciliation_settings(conn: Connection) -> dict:
    """Get reconciliation settings.

    Args:
        conn: Database connection

    Returns:
        Dict with reconciliation settings
    """
    cursor = conn.execute(
        """SELECT reconcile_on_epg_generation, reconcile_on_startup,
                  auto_fix_orphan_teamarr, auto_fix_orphan_dispatcharr,
                  auto_fix_duplicates, default_duplicate_event_handling,
                  channel_history_retention_days
           FROM settings WHERE id = 1"""
    )
    row = cursor.fetchone()
    if not row:
        return {
            "reconcile_on_epg_generation": True,
            "reconcile_on_startup": True,
            "auto_fix_orphan_teamarr": True,
            "auto_fix_orphan_dispatcharr": False,
            "auto_fix_duplicates": False,
            "default_duplicate_event_handling": "consolidate",
            "channel_history_retention_days": 90,
        }
    return {
        "reconcile_on_epg_generation": bool(row["reconcile_on_epg_generation"]),
        "reconcile_on_startup": bool(row["reconcile_on_startup"]),
        "auto_fix_orphan_teamarr": bool(row["auto_fix_orphan_teamarr"]),
        "auto_fix_orphan_dispatcharr": bool(row["auto_fix_orphan_dispatcharr"]),
        "auto_fix_duplicates": bool(row["auto_fix_duplicates"]),
        "default_duplicate_event_handling": row["default_duplicate_event_handling"],
        "channel_history_retention_days": row["channel_history_retention_days"] or 90,
    }


def get_scheduler_settings(conn: Connection) -> dict:
    """Get scheduler settings.

    Args:
        conn: Database connection

    Returns:
        Dict with scheduler settings
    """
    cursor = conn.execute(
        """SELECT scheduler_enabled, scheduler_interval_minutes
           FROM settings WHERE id = 1"""
    )
    row = cursor.fetchone()
    if not row:
        return {"enabled": True, "interval_minutes": 15}
    return {
        "enabled": bool(row["scheduler_enabled"]),
        "interval_minutes": row["scheduler_interval_minutes"] or 15,
    }
