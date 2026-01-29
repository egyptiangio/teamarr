"""Channel Reconciliation System for Event-based EPG.

Detects and resolves inconsistencies between Teamarr's managed_channels
database and Dispatcharr's actual channel state.

Issue Types:
- Orphan (Teamarr): Record exists in DB but channel missing in Dispatcharr
- Orphan (Dispatcharr): Channel with teamarr-* tvg_id exists but no DB record
- Duplicate: Multiple channels for the same event
- Drift: Channel settings differ between Teamarr and Dispatcharr

Actions:
- auto_fix: Automatically resolve issues based on settings
- detect_only: Report issues without fixing
- manual: Queue issues for user review
"""

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from sqlite3 import Connection
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# TYPES
# =============================================================================


@dataclass
class ReconciliationIssue:
    """Represents a single reconciliation issue."""

    issue_type: str  # orphan_teamarr, orphan_dispatcharr, duplicate, drift
    severity: str  # critical, warning, info

    managed_channel_id: int | None = None
    dispatcharr_channel_id: int | None = None
    dispatcharr_uuid: str | None = None
    channel_name: str | None = None
    event_id: str | None = None

    details: dict = field(default_factory=dict)
    suggested_action: str | None = None  # delete, create, merge, update, ignore
    auto_fixable: bool = False

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return {
            "issue_type": self.issue_type,
            "severity": self.severity,
            "managed_channel_id": self.managed_channel_id,
            "dispatcharr_channel_id": self.dispatcharr_channel_id,
            "dispatcharr_uuid": self.dispatcharr_uuid,
            "channel_name": self.channel_name,
            "event_id": self.event_id,
            "details": self.details,
            "suggested_action": self.suggested_action,
            "auto_fixable": self.auto_fixable,
        }


@dataclass
class ReconciliationResult:
    """Results from a reconciliation run."""

    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    issues_found: list[ReconciliationIssue] = field(default_factory=list)
    issues_fixed: list[dict] = field(default_factory=list)
    issues_skipped: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, int]:
        """Get counts by issue type."""
        counts = {
            "orphan_teamarr": 0,
            "orphan_dispatcharr": 0,
            "duplicate": 0,
            "drift": 0,
            "total": len(self.issues_found),
            "fixed": len(self.issues_fixed),
            "skipped": len(self.issues_skipped),
            "errors": len(self.errors),
        }
        for issue in self.issues_found:
            if issue.issue_type in counts:
                counts[issue.issue_type] += 1
        return counts

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return {
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "summary": self.summary,
            "issues_found": [i.to_dict() for i in self.issues_found],
            "issues_fixed": self.issues_fixed,
            "issues_skipped": self.issues_skipped,
            "errors": self.errors,
        }


# =============================================================================
# RECONCILER
# =============================================================================


class ChannelReconciler:
    """Reconciles Teamarr managed channels with Dispatcharr.

    Detects orphans, duplicates, and drift, then optionally fixes them
    based on configured settings.

    Usage:
        from teamarr.dispatcharr import DispatcharrClient, ChannelManager
        from teamarr.database import get_db

        with DispatcharrClient(url, user, password) as client:
            reconciler = ChannelReconciler(
                db_factory=get_db,
                channel_manager=ChannelManager(client),
            )
            result = reconciler.reconcile(auto_fix=True)
            logger.info(f"Found {result.summary['total']} issues, fixed {result.summary['fixed']}")
    """

    def __init__(
        self,
        db_factory: Any,
        channel_manager: Any = None,
        settings: dict | None = None,
    ):
        """Initialize the reconciler.

        Args:
            db_factory: Factory function returning database connection
            channel_manager: ChannelManager instance for Dispatcharr operations
            settings: App settings with reconciliation config
        """
        self._db_factory = db_factory
        self._channel_manager = channel_manager
        self._settings = settings or {}
        self._dispatcharr_lock = threading.Lock()

    @property
    def dispatcharr_enabled(self) -> bool:
        """Check if Dispatcharr integration is enabled."""
        return self._channel_manager is not None

    def reconcile(
        self,
        auto_fix: bool | None = None,
        group_ids: list[int] | None = None,
    ) -> ReconciliationResult:
        """Run full reconciliation check.

        Args:
            auto_fix: Override auto-fix setting (None = use settings)
            group_ids: Limit to specific groups (None = all)

        Returns:
            ReconciliationResult with all findings and actions taken
        """
        result = ReconciliationResult()

        if not self.dispatcharr_enabled:
            result.errors.append("Dispatcharr not configured")
            result.completed_at = datetime.now()
            return result

        # Clear channel cache to ensure fresh data from Dispatcharr
        self._channel_manager.clear_cache()

        try:
            with self._db_factory() as conn:
                # Step 1: Detect orphans (Teamarr records without Dispatcharr channels)
                teamarr_orphans = self._detect_orphan_teamarr(conn, group_ids)
                result.issues_found.extend(teamarr_orphans)

                # Step 2: Detect orphans (Dispatcharr channels without Teamarr records)
                dispatcharr_orphans = self._detect_orphan_dispatcharr(conn, group_ids)
                result.issues_found.extend(dispatcharr_orphans)

                # Step 3: Detect duplicates
                duplicates = self._detect_duplicates(conn, group_ids)
                result.issues_found.extend(duplicates)

                # Step 4: Detect drift (setting mismatches)
                drift_issues = self._detect_drift(conn, group_ids)
                result.issues_found.extend(drift_issues)

                # Step 5: Apply fixes if auto_fix is enabled
                should_fix = (
                    auto_fix
                    if auto_fix is not None
                    else self._settings.get("auto_fix_enabled", False)
                )
                if should_fix:
                    self._apply_fixes(conn, result)
                    conn.commit()

        except Exception as e:
            result.errors.append(f"Reconciliation error: {e}")
            logger.exception("Reconciliation failed")

        result.completed_at = datetime.now()
        return result

    def _detect_orphan_teamarr(
        self,
        conn: Connection,
        group_ids: list[int] | None = None,
    ) -> list[ReconciliationIssue]:
        """Detect Teamarr records that have no corresponding Dispatcharr channel.

        These are channels that were created but may have been deleted externally,
        or where creation partially failed.
        """
        from teamarr.database.channels import (
            get_all_managed_channels,
            get_managed_channels_for_group,
            update_managed_channel,
        )

        issues = []

        # Get managed channels
        if group_ids:
            channels = []
            for gid in group_ids:
                channels.extend(get_managed_channels_for_group(conn, gid))
        else:
            channels = get_all_managed_channels(conn, include_deleted=False)

        for channel in channels:
            if not channel.dispatcharr_channel_id:
                continue

            # Check if channel exists in Dispatcharr
            with self._dispatcharr_lock:
                dispatcharr_channel = self._channel_manager.get_channel(
                    channel.dispatcharr_channel_id
                )

            if not dispatcharr_channel:
                issues.append(
                    ReconciliationIssue(
                        issue_type="orphan_teamarr",
                        severity="warning",
                        managed_channel_id=channel.id,
                        dispatcharr_channel_id=channel.dispatcharr_channel_id,
                        dispatcharr_uuid=channel.dispatcharr_uuid,
                        channel_name=channel.channel_name,
                        event_id=channel.event_id,
                        details={
                            "channel_number": channel.channel_number,
                            "tvg_id": channel.tvg_id,
                            "group_id": channel.event_epg_group_id,
                        },
                        suggested_action="mark_deleted",
                        auto_fixable=self._settings.get("auto_fix_orphan_teamarr", True),
                    )
                )
            else:
                # Channel exists - backfill UUID if we don't have it
                if not channel.dispatcharr_uuid and dispatcharr_channel.uuid:
                    update_managed_channel(
                        conn,
                        channel.id,
                        {"dispatcharr_uuid": dispatcharr_channel.uuid},
                    )
                    logger.debug(
                        "[RECONCILE] Backfilled UUID for channel '%s': %s",
                        channel.channel_name,
                        dispatcharr_channel.uuid,
                    )

        if issues:
            logger.info("[ORPHAN_TEAMARR] Found %d orphan(s)", len(issues))

        return issues

    def _detect_orphan_dispatcharr(
        self,
        conn: Connection,
        group_ids: list[int] | None = None,
    ) -> list[ReconciliationIssue]:
        """Detect Dispatcharr channels with teamarr-* tvg_id that aren't tracked.

        These are channels that may have been created manually or where
        Teamarr's database record was lost.
        """
        issues = []

        # Get all channels from Dispatcharr
        with self._dispatcharr_lock:
            all_channels = self._channel_manager.get_channels()

        # Build sets of known identifiers from managed_channels
        cursor = conn.execute(
            """SELECT dispatcharr_channel_id, dispatcharr_uuid
               FROM managed_channels WHERE deleted_at IS NULL"""
        )
        rows = cursor.fetchall()
        known_channel_ids = {row[0] for row in rows if row[0]}
        known_uuids = {row[1] for row in rows if row[1]}

        for channel in all_channels:
            channel_id = channel.id
            channel_uuid = channel.uuid
            tvg_id = channel.tvg_id or ""

            # Check if this is a Teamarr channel
            is_ours_by_uuid = channel_uuid and channel_uuid in known_uuids
            is_ours_by_id = channel_id in known_channel_ids
            has_teamarr_tvg_id = tvg_id.startswith("teamarr-event-")

            # If we know this channel, it's not orphaned
            if is_ours_by_uuid or is_ours_by_id:
                continue

            # If it has our tvg_id pattern but we don't have a record, it's orphaned
            if has_teamarr_tvg_id:
                event_id = tvg_id.replace("teamarr-event-", "")

                issues.append(
                    ReconciliationIssue(
                        issue_type="orphan_dispatcharr",
                        severity="warning",
                        dispatcharr_channel_id=channel_id,
                        dispatcharr_uuid=channel_uuid,
                        channel_name=channel.name,
                        event_id=event_id,
                        details={
                            "channel_number": channel.channel_number,
                            "tvg_id": tvg_id,
                            "streams": list(channel.streams),  # Already int IDs
                        },
                        suggested_action="delete_or_adopt",
                        auto_fixable=self._settings.get("auto_fix_orphan_dispatcharr", False),
                    )
                )

        if issues:
            logger.info("[ORPHAN_DISPATCHARR] Found %d orphan(s)", len(issues))

        return issues

    def _detect_duplicates(
        self,
        conn: Connection,
        group_ids: list[int] | None = None,
    ) -> list[ReconciliationIssue]:
        """Detect multiple channels for the same event within a group.

        This can happen if:
        - duplicate_event_handling changed from 'separate' to 'consolidate'
        - Bug in channel creation
        - Manual channel creation
        """
        issues = []

        # Find events with multiple channels (excluding 'separate' mode groups)
        query = """
            SELECT mc.event_id, mc.event_epg_group_id,
                   eg.duplicate_event_handling,
                   COUNT(*) as channel_count,
                   GROUP_CONCAT(mc.id) as channel_ids,
                   GROUP_CONCAT(mc.channel_name) as channel_names
            FROM managed_channels mc
            LEFT JOIN event_epg_groups eg ON mc.event_epg_group_id = eg.id
            WHERE mc.deleted_at IS NULL
              AND mc.event_id IS NOT NULL
        """
        params: list = []
        if group_ids:
            placeholders = ",".join("?" * len(group_ids))
            query += f" AND mc.event_epg_group_id IN ({placeholders})"
            params.extend(group_ids)

        query += """
            GROUP BY mc.event_id, mc.event_epg_group_id
            HAVING channel_count > 1
        """

        cursor = conn.execute(query, params)
        duplicates = [dict(row) for row in cursor.fetchall()]

        for dup in duplicates:
            # Skip if group is in 'separate' mode (duplicates are expected)
            if dup.get("duplicate_event_handling") == "separate":
                continue

            issues.append(
                ReconciliationIssue(
                    issue_type="duplicate",
                    severity="warning",
                    event_id=dup["event_id"],
                    details={
                        "group_id": dup.get("event_epg_group_id"),
                        "channel_count": dup["channel_count"],
                        "channel_ids": (
                            dup.get("channel_ids", "").split(",") if dup.get("channel_ids") else []
                        ),
                        "channel_names": (
                            dup.get("channel_names", "").split(",")
                            if dup.get("channel_names")
                            else []
                        ),
                        "duplicate_mode": dup.get("duplicate_event_handling"),
                    },
                    suggested_action="merge",
                    auto_fixable=self._settings.get("auto_fix_duplicates", False),
                )
            )

        if issues:
            logger.info("[DUPLICATE] Found %d duplicate event(s)", len(issues))

        return issues

    def _detect_drift(
        self,
        conn: Connection,
        group_ids: list[int] | None = None,
    ) -> list[ReconciliationIssue]:
        """Detect channels where Teamarr's expected state differs from Dispatcharr.

        Checks:
        - Channel number mismatch
        - tvg_id mismatch
        - Channel group mismatch
        - Stream profile mismatch
        """
        from teamarr.database.channels import (
            get_all_managed_channels,
            get_managed_channels_for_group,
        )

        issues = []

        # Get managed channels with group config
        if group_ids:
            channels = []
            for gid in group_ids:
                channels.extend(get_managed_channels_for_group(conn, gid))
        else:
            channels = get_all_managed_channels(conn, include_deleted=False)

        for channel in channels:
            if not channel.dispatcharr_channel_id:
                continue

            # Get current state from Dispatcharr
            with self._dispatcharr_lock:
                dispatcharr_channel = self._channel_manager.get_channel(
                    channel.dispatcharr_channel_id
                )

            if not dispatcharr_channel:
                continue  # Will be caught by orphan detection

            drift_fields = []

            # Note: channel_number drift is not checked here because it's
            # enforced during generation in _sync_channel_settings

            # Check tvg_id
            if channel.tvg_id and channel.tvg_id != dispatcharr_channel.tvg_id:
                drift_fields.append(
                    {
                        "field": "tvg_id",
                        "expected": channel.tvg_id,
                        "actual": dispatcharr_channel.tvg_id,
                    }
                )

            # Check channel_group_id
            expected_group = channel.channel_group_id
            actual_group = dispatcharr_channel.channel_group_id
            if expected_group and expected_group != actual_group:
                drift_fields.append(
                    {
                        "field": "channel_group_id",
                        "expected": expected_group,
                        "actual": actual_group,
                    }
                )

            if drift_fields:
                issues.append(
                    ReconciliationIssue(
                        issue_type="drift",
                        severity="info",
                        managed_channel_id=channel.id,
                        dispatcharr_channel_id=channel.dispatcharr_channel_id,
                        channel_name=channel.channel_name,
                        event_id=channel.event_id,
                        details={
                            "drift_fields": drift_fields,
                            "group_id": channel.event_epg_group_id,
                        },
                        suggested_action="sync",
                        auto_fixable=True,  # Drift is generally safe to auto-fix
                    )
                )

        if issues:
            logger.info("[DRIFT] Found %d channel(s) with drift", len(issues))

        return issues

    def _apply_fixes(
        self,
        conn: Connection,
        result: ReconciliationResult,
    ) -> None:
        """Apply automatic fixes for auto-fixable issues."""
        from teamarr.database.channels import (
            log_channel_history,
            mark_channel_deleted,
        )

        for issue in result.issues_found:
            if not issue.auto_fixable:
                result.issues_skipped.append(
                    {
                        "issue_type": issue.issue_type,
                        "channel_name": issue.channel_name,
                        "reason": "Auto-fix disabled for this issue type",
                    }
                )
                continue

            try:
                if issue.issue_type == "orphan_teamarr":
                    # Mark as deleted in Teamarr DB
                    if issue.managed_channel_id:
                        mark_channel_deleted(
                            conn,
                            issue.managed_channel_id,
                            reason="Orphan - channel missing from Dispatcharr",
                        )
                        log_channel_history(
                            conn=conn,
                            managed_channel_id=issue.managed_channel_id,
                            change_type="deleted",
                            change_source="reconciliation",
                            notes="Orphan detected - channel missing from Dispatcharr",
                        )
                        result.issues_fixed.append(
                            {
                                "issue_type": issue.issue_type,
                                "channel_name": issue.channel_name,
                                "action": "marked_deleted",
                            }
                        )
                        logger.info("[FIXED] Marked orphan deleted: %s", issue.channel_name)

                elif issue.issue_type == "orphan_dispatcharr":
                    # Delete from Dispatcharr
                    if issue.dispatcharr_channel_id:
                        with self._dispatcharr_lock:
                            delete_result = self._channel_manager.delete_channel(
                                issue.dispatcharr_channel_id
                            )
                        if delete_result.success:
                            result.issues_fixed.append(
                                {
                                    "issue_type": issue.issue_type,
                                    "channel_name": issue.channel_name,
                                    "action": "deleted_from_dispatcharr",
                                }
                            )
                            logger.info(
                                "[FIXED] Deleted orphan from Dispatcharr: %s", issue.channel_name
                            )
                        else:
                            result.errors.append(
                                f"Failed to delete orphan channel: {delete_result.error}"
                            )

                elif issue.issue_type == "drift":
                    # Sync settings to Dispatcharr
                    if issue.managed_channel_id and issue.dispatcharr_channel_id:
                        drift_fields = issue.details.get("drift_fields", [])
                        update_data = {}
                        for drift in drift_fields:
                            field_name = drift["field"]
                            expected_value = drift["expected"]
                            update_data[field_name] = expected_value

                        if update_data:
                            with self._dispatcharr_lock:
                                self._channel_manager.update_channel(
                                    issue.dispatcharr_channel_id,
                                    update_data,
                                )
                            result.issues_fixed.append(
                                {
                                    "issue_type": issue.issue_type,
                                    "channel_name": issue.channel_name,
                                    "action": "synced",
                                    "fields": list(update_data.keys()),
                                }
                            )
                            logger.info("[FIXED] Synced drift: %s", issue.channel_name)

                elif issue.issue_type == "duplicate":
                    # Duplicate fix is more complex - skip for now
                    result.issues_skipped.append(
                        {
                            "issue_type": issue.issue_type,
                            "event_id": issue.event_id,
                            "reason": "Duplicate merge requires manual review",
                        }
                    )

            except Exception as e:
                result.errors.append(
                    f"Failed to fix {issue.issue_type} for {issue.channel_name}: {e}"
                )
                logger.warning("[FIX_ERROR] %s: %s", issue.channel_name, e)

    def verify_channel(
        self,
        managed_channel_id: int,
    ) -> ReconciliationIssue | None:
        """Verify a single channel and return any issues.

        Args:
            managed_channel_id: Channel ID to verify

        Returns:
            ReconciliationIssue if found, None if channel is healthy
        """
        from teamarr.database.channels import get_managed_channel

        with self._db_factory() as conn:
            channel = get_managed_channel(conn, managed_channel_id)
            if not channel:
                return None

            if not channel.dispatcharr_channel_id:
                return ReconciliationIssue(
                    issue_type="orphan_teamarr",
                    severity="warning",
                    managed_channel_id=channel.id,
                    channel_name=channel.channel_name,
                    suggested_action="sync_to_dispatcharr",
                )

            # Check if exists in Dispatcharr
            with self._dispatcharr_lock:
                dispatcharr_channel = self._channel_manager.get_channel(
                    channel.dispatcharr_channel_id
                )

            if not dispatcharr_channel:
                return ReconciliationIssue(
                    issue_type="orphan_teamarr",
                    severity="warning",
                    managed_channel_id=channel.id,
                    dispatcharr_channel_id=channel.dispatcharr_channel_id,
                    channel_name=channel.channel_name,
                    suggested_action="mark_deleted",
                )

            # Check for drift
            if channel.tvg_id and channel.tvg_id != dispatcharr_channel.tvg_id:
                return ReconciliationIssue(
                    issue_type="drift",
                    severity="info",
                    managed_channel_id=channel.id,
                    dispatcharr_channel_id=channel.dispatcharr_channel_id,
                    channel_name=channel.channel_name,
                    details={
                        "drift_fields": [
                            {
                                "field": "tvg_id",
                                "expected": channel.tvg_id,
                                "actual": dispatcharr_channel.tvg_id,
                            }
                        ]
                    },
                    suggested_action="sync",
                )

            return None  # Channel is healthy


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def create_reconciler(
    db_factory: Any,
    dispatcharr_client: Any = None,
) -> ChannelReconciler:
    """Create a ChannelReconciler with optional Dispatcharr integration.

    Args:
        db_factory: Factory function returning database connection
        dispatcharr_client: Optional DispatcharrClient instance

    Returns:
        Configured ChannelReconciler
    """
    from teamarr.database.channels import (
        get_dispatcharr_settings,
        get_reconciliation_settings,
    )

    with db_factory() as conn:
        dispatcharr_settings = get_dispatcharr_settings(conn)
        reconciliation_settings = get_reconciliation_settings(conn)

    channel_manager = None

    if dispatcharr_client and dispatcharr_settings.get("enabled"):
        from teamarr.dispatcharr import ChannelManager
        from teamarr.dispatcharr.factory import DispatcharrConnection

        # Extract raw client if we received a DispatcharrConnection
        raw_client = (
            dispatcharr_client.client
            if isinstance(dispatcharr_client, DispatcharrConnection)
            else dispatcharr_client
        )
        channel_manager = ChannelManager(raw_client)

    return ChannelReconciler(
        db_factory=db_factory,
        channel_manager=channel_manager,
        settings=reconciliation_settings,
    )
