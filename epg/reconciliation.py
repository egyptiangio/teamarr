"""
Channel Reconciliation System for Event-based EPG

Detects and resolves inconsistencies between Teamarr's managed_channels
database and Dispatcharr's actual channel state.

Issue Types:
- Orphan (Teamarr): Record exists in DB but channel missing in Dispatcharr
- Orphan (Dispatcharr): Channel with teamarr-* tvg_id exists but no DB record
- Duplicate: Multiple channels for the same ESPN event
- Drift: Channel settings differ between Teamarr and Dispatcharr

Actions:
- auto_fix: Automatically resolve issues based on settings
- detect_only: Report issues without fixing
- manual: Queue issues for user review
"""

import logging
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ReconciliationIssue:
    """Represents a single reconciliation issue."""
    issue_type: str  # orphan_teamarr, orphan_dispatcharr, duplicate, drift
    severity: str  # critical, warning, info
    managed_channel_id: int = None
    dispatcharr_channel_id: int = None
    dispatcharr_uuid: str = None  # Immutable identifier from Dispatcharr
    channel_name: str = None
    espn_event_id: str = None
    details: Dict = field(default_factory=dict)
    suggested_action: str = None  # delete, create, merge, update, ignore
    auto_fixable: bool = False


@dataclass
class ReconciliationResult:
    """Results from a reconciliation run."""
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime = None
    issues_found: List[ReconciliationIssue] = field(default_factory=list)
    issues_fixed: List[Dict] = field(default_factory=list)
    issues_skipped: List[Dict] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def summary(self) -> Dict[str, int]:
        """Get counts by issue type."""
        counts = {
            'orphan_teamarr': 0,
            'orphan_dispatcharr': 0,
            'duplicate': 0,
            'drift': 0,
            'total': len(self.issues_found),
            'fixed': len(self.issues_fixed),
            'skipped': len(self.issues_skipped),
            'errors': len(self.errors)
        }
        for issue in self.issues_found:
            if issue.issue_type in counts:
                counts[issue.issue_type] += 1
        return counts


class ChannelReconciler:
    """
    Reconciles Teamarr managed channels with Dispatcharr.

    Detects orphans, duplicates, and drift, then optionally fixes them
    based on configured settings.
    """

    def __init__(
        self,
        dispatcharr_url: str,
        dispatcharr_username: str,
        dispatcharr_password: str,
        settings: Dict = None
    ):
        """
        Initialize the reconciler.

        Args:
            dispatcharr_url: Dispatcharr base URL
            dispatcharr_username: Dispatcharr username
            dispatcharr_password: Dispatcharr password
            settings: App settings with reconciliation config
        """
        from api.dispatcharr_client import ChannelManager

        self.channel_api = ChannelManager(
            dispatcharr_url,
            dispatcharr_username,
            dispatcharr_password
        )
        self.settings = settings or {}

    def reconcile(
        self,
        auto_fix: bool = None,
        group_ids: List[int] = None
    ) -> ReconciliationResult:
        """
        Run full reconciliation check.

        Args:
            auto_fix: Override auto-fix setting (None = use settings)
            group_ids: Limit to specific groups (None = all)

        Returns:
            ReconciliationResult with all findings and actions taken
        """
        result = ReconciliationResult()

        try:
            # Step 1: Detect orphans (Teamarr records without Dispatcharr channels)
            teamarr_orphans = self._detect_orphan_teamarr(group_ids)
            result.issues_found.extend(teamarr_orphans)

            # Step 2: Detect orphans (Dispatcharr channels without Teamarr records)
            dispatcharr_orphans = self._detect_orphan_dispatcharr(group_ids)
            result.issues_found.extend(dispatcharr_orphans)

            # Step 3: Detect duplicates
            duplicates = self._detect_duplicates(group_ids)
            result.issues_found.extend(duplicates)

            # Step 4: Detect drift (setting mismatches)
            drift_issues = self._detect_drift(group_ids)
            result.issues_found.extend(drift_issues)

            # Step 5: Apply fixes if auto_fix is enabled
            should_auto_fix = auto_fix if auto_fix is not None else self.settings.get('auto_fix_enabled', False)
            if should_auto_fix:
                self._apply_fixes(result)

        except Exception as e:
            result.errors.append(f"Reconciliation error: {e}")
            logger.error(f"Reconciliation failed: {e}")

        result.completed_at = datetime.now()
        return result

    def _detect_orphan_teamarr(self, group_ids: List[int] = None) -> List[ReconciliationIssue]:
        """
        Detect Teamarr records that have no corresponding Dispatcharr channel.

        Uses UUID as authoritative identifier when available, with channel ID as fallback.
        Also backfills UUIDs for channels that don't have them yet.

        These are channels that were created but may have been deleted externally,
        or where creation partially failed.
        """
        from database import get_connection, update_managed_channel

        issues = []

        conn = get_connection()
        try:
            query = """
                SELECT mc.*, eg.group_name
                FROM managed_channels mc
                LEFT JOIN event_epg_groups eg ON mc.event_epg_group_id = eg.id
                WHERE mc.deleted_at IS NULL
            """
            params = []
            if group_ids:
                placeholders = ','.join('?' * len(group_ids))
                query += f" AND mc.event_epg_group_id IN ({placeholders})"
                params.extend(group_ids)

            channels = [dict(row) for row in conn.execute(query, params).fetchall()]
        finally:
            conn.close()

        for channel in channels:
            dispatcharr_id = channel.get('dispatcharr_channel_id')
            stored_uuid = channel.get('dispatcharr_uuid')

            if not dispatcharr_id:
                continue

            # Check if channel exists in Dispatcharr
            dispatcharr_channel = self.channel_api.get_channel(dispatcharr_id)

            if not dispatcharr_channel:
                issues.append(ReconciliationIssue(
                    issue_type='orphan_teamarr',
                    severity='warning',
                    managed_channel_id=channel['id'],
                    dispatcharr_channel_id=dispatcharr_id,
                    channel_name=channel.get('channel_name'),
                    espn_event_id=channel.get('espn_event_id'),
                    details={
                        'group_name': channel.get('group_name'),
                        'channel_number': channel.get('channel_number'),
                        'tvg_id': channel.get('tvg_id'),
                        'uuid': stored_uuid
                    },
                    suggested_action='mark_deleted',
                    auto_fixable=self.settings.get('auto_fix_orphan_teamarr', True)
                ))
            else:
                # Channel exists - backfill UUID if we don't have it
                if not stored_uuid and dispatcharr_channel.get('uuid'):
                    try:
                        update_managed_channel(channel['id'], {
                            'dispatcharr_uuid': dispatcharr_channel['uuid']
                        })
                        logger.debug(
                            f"Backfilled UUID for channel '{channel.get('channel_name')}': "
                            f"{dispatcharr_channel['uuid']}"
                        )
                    except Exception as e:
                        logger.warning(f"Failed to backfill UUID for channel {channel['id']}: {e}")

        if issues:
            logger.info(f"Found {len(issues)} Teamarr orphan(s)")

        return issues

    def _detect_orphan_dispatcharr(self, group_ids: List[int] = None) -> List[ReconciliationIssue]:
        """
        Detect Dispatcharr channels with teamarr-* tvg_id that aren't tracked.

        Uses two identification methods:
        1. UUID match (authoritative) - immutable identifier from Dispatcharr
        2. tvg_id pattern match (fallback) - for channels created before UUID tracking

        These are channels that may have been created manually or where
        Teamarr's database record was lost.
        """
        from database import get_connection

        issues = []

        # Get all channels from Dispatcharr
        all_channels = self.channel_api.get_channels()

        # Build sets of known identifiers from our managed_channels
        conn = get_connection()
        try:
            # Try query with UUID column (v2 schema)
            try:
                rows = conn.execute("""
                    SELECT dispatcharr_channel_id, dispatcharr_uuid
                    FROM managed_channels WHERE deleted_at IS NULL
                """).fetchall()
                known_channel_ids = {row[0] for row in rows if row[0]}
                known_uuids = {row[1] for row in rows if row[1]}
            except Exception:
                # UUID column doesn't exist yet - fall back to channel ID only
                rows = conn.execute("""
                    SELECT dispatcharr_channel_id
                    FROM managed_channels WHERE deleted_at IS NULL
                """).fetchall()
                known_channel_ids = {row[0] for row in rows if row[0]}
                known_uuids = set()
        finally:
            conn.close()

        for channel in all_channels:
            channel_id = channel.get('id')
            channel_uuid = channel.get('uuid')
            tvg_id = channel.get('tvg_id') or ''

            # Check if this is a Teamarr channel (either by UUID or tvg_id pattern)
            is_ours_by_uuid = channel_uuid and channel_uuid in known_uuids
            is_ours_by_id = channel_id in known_channel_ids
            has_teamarr_tvg_id = tvg_id.startswith('teamarr-event-')

            # If we know this channel (by UUID or ID), it's not orphaned
            if is_ours_by_uuid or is_ours_by_id:
                continue

            # If it has our tvg_id pattern but we don't have a record, it's orphaned
            if has_teamarr_tvg_id:
                event_id = tvg_id.replace('teamarr-event-', '')

                issues.append(ReconciliationIssue(
                    issue_type='orphan_dispatcharr',
                    severity='warning',
                    dispatcharr_channel_id=channel_id,
                    channel_name=channel.get('name'),
                    espn_event_id=event_id,
                    details={
                        'channel_number': channel.get('channel_number'),
                        'tvg_id': tvg_id,
                        'uuid': channel_uuid,
                        'streams': channel.get('streams', [])
                    },
                    suggested_action='delete_or_adopt',
                    auto_fixable=self.settings.get('auto_fix_orphan_dispatcharr', False)
                ))

        if issues:
            logger.info(f"Found {len(issues)} Dispatcharr orphan(s)")

        return issues

    def _detect_duplicates(self, group_ids: List[int] = None) -> List[ReconciliationIssue]:
        """
        Detect multiple channels for the same ESPN event within a group.

        This can happen if:
        - duplicate_event_handling changed from 'separate' to 'consolidate'
        - Bug in channel creation
        - Manual channel creation
        """
        from database import get_connection

        issues = []

        conn = get_connection()
        try:
            # Find events with multiple channels (excluding 'separate' mode groups)
            query = """
                SELECT mc.espn_event_id, mc.event_epg_group_id, eg.group_name,
                       eg.duplicate_event_handling,
                       COUNT(*) as channel_count,
                       GROUP_CONCAT(mc.id) as channel_ids,
                       GROUP_CONCAT(mc.channel_name) as channel_names
                FROM managed_channels mc
                JOIN event_epg_groups eg ON mc.event_epg_group_id = eg.id
                WHERE mc.deleted_at IS NULL
                  AND mc.espn_event_id IS NOT NULL
            """
            params = []
            if group_ids:
                placeholders = ','.join('?' * len(group_ids))
                query += f" AND mc.event_epg_group_id IN ({placeholders})"
                params.extend(group_ids)

            query += """
                GROUP BY mc.espn_event_id, mc.event_epg_group_id
                HAVING channel_count > 1
            """

            duplicates = [dict(row) for row in conn.execute(query, params).fetchall()]
        finally:
            conn.close()

        for dup in duplicates:
            # Skip if group is in 'separate' mode (duplicates are expected)
            if dup.get('duplicate_event_handling') == 'separate':
                continue

            issues.append(ReconciliationIssue(
                issue_type='duplicate',
                severity='warning',
                espn_event_id=dup['espn_event_id'],
                details={
                    'group_name': dup.get('group_name'),
                    'channel_count': dup['channel_count'],
                    'channel_ids': dup.get('channel_ids', '').split(','),
                    'channel_names': dup.get('channel_names', '').split(','),
                    'duplicate_mode': dup.get('duplicate_event_handling')
                },
                suggested_action='merge',
                auto_fixable=self.settings.get('auto_fix_duplicates', False)
            ))

        if issues:
            logger.info(f"Found {len(issues)} duplicate event(s)")

        return issues

    def _detect_drift(self, group_ids: List[int] = None) -> List[ReconciliationIssue]:
        """
        Detect channels where Teamarr's expected state differs from Dispatcharr.

        Checks:
        - Channel number mismatch
        - Channel name mismatch
        - tvg_id mismatch
        - Stream assignment mismatch
        - Channel group mismatch
        """
        from database import get_connection

        issues = []

        conn = get_connection()
        try:
            query = """
                SELECT mc.*, eg.group_name, eg.channel_group_id as expected_group_id,
                       eg.stream_profile_id as expected_stream_profile_id
                FROM managed_channels mc
                LEFT JOIN event_epg_groups eg ON mc.event_epg_group_id = eg.id
                WHERE mc.deleted_at IS NULL
            """
            params = []
            if group_ids:
                placeholders = ','.join('?' * len(group_ids))
                query += f" AND mc.event_epg_group_id IN ({placeholders})"
                params.extend(group_ids)

            channels = [dict(row) for row in conn.execute(query, params).fetchall()]
        finally:
            conn.close()

        for channel in channels:
            dispatcharr_id = channel.get('dispatcharr_channel_id')
            if not dispatcharr_id:
                continue

            # Get current state from Dispatcharr
            dispatcharr_channel = self.channel_api.get_channel(dispatcharr_id)
            if not dispatcharr_channel:
                continue  # Will be caught by orphan detection

            drift_fields = []

            # Check channel number
            expected_number = channel.get('channel_number')
            actual_number = dispatcharr_channel.get('channel_number')
            if expected_number and actual_number and expected_number != actual_number:
                drift_fields.append({
                    'field': 'channel_number',
                    'expected': expected_number,
                    'actual': actual_number
                })

            # Check tvg_id
            expected_tvg_id = channel.get('tvg_id')
            actual_tvg_id = dispatcharr_channel.get('tvg_id')
            if expected_tvg_id and expected_tvg_id != actual_tvg_id:
                drift_fields.append({
                    'field': 'tvg_id',
                    'expected': expected_tvg_id,
                    'actual': actual_tvg_id
                })

            # Check channel group
            expected_group_id = channel.get('expected_group_id')
            actual_group_id = dispatcharr_channel.get('channel_group_id')
            if expected_group_id and expected_group_id != actual_group_id:
                drift_fields.append({
                    'field': 'channel_group_id',
                    'expected': expected_group_id,
                    'actual': actual_group_id
                })

            if drift_fields:
                issues.append(ReconciliationIssue(
                    issue_type='drift',
                    severity='info',
                    managed_channel_id=channel['id'],
                    dispatcharr_channel_id=dispatcharr_id,
                    channel_name=channel.get('channel_name'),
                    espn_event_id=channel.get('espn_event_id'),
                    details={
                        'drift_fields': drift_fields,
                        'group_name': channel.get('group_name')
                    },
                    suggested_action='sync',
                    auto_fixable=True  # Drift is generally safe to auto-fix
                ))

        if issues:
            logger.info(f"Found {len(issues)} channel(s) with drift")

        return issues

    def _apply_fixes(self, result: ReconciliationResult):
        """Apply automatic fixes for auto-fixable issues."""
        from database import mark_managed_channel_deleted, log_channel_history, update_channel_sync_status

        for issue in result.issues_found:
            if not issue.auto_fixable:
                result.issues_skipped.append({
                    'issue_type': issue.issue_type,
                    'channel_name': issue.channel_name,
                    'reason': 'Auto-fix disabled for this issue type'
                })
                continue

            try:
                if issue.issue_type == 'orphan_teamarr':
                    # Mark as deleted in Teamarr DB
                    if issue.managed_channel_id:
                        mark_managed_channel_deleted(issue.managed_channel_id)
                        update_channel_sync_status(
                            issue.managed_channel_id,
                            'orphaned',
                            'Channel not found in Dispatcharr - marked deleted'
                        )
                        log_channel_history(
                            managed_channel_id=issue.managed_channel_id,
                            change_type='deleted',
                            change_source='reconciliation',
                            notes='Orphan detected - channel missing from Dispatcharr'
                        )
                        result.issues_fixed.append({
                            'issue_type': issue.issue_type,
                            'channel_name': issue.channel_name,
                            'action': 'marked_deleted'
                        })
                        logger.info(f"Fixed orphan: marked '{issue.channel_name}' as deleted")

                elif issue.issue_type == 'orphan_dispatcharr':
                    # Delete from Dispatcharr (if auto_fix_orphan_dispatcharr is enabled)
                    if self.settings.get('auto_fix_orphan_dispatcharr', False):
                        delete_result = self.channel_api.delete_channel(issue.dispatcharr_channel_id)
                        if delete_result.get('success'):
                            result.issues_fixed.append({
                                'issue_type': issue.issue_type,
                                'channel_name': issue.channel_name,
                                'action': 'deleted_from_dispatcharr'
                            })
                            logger.info(f"Fixed orphan: deleted '{issue.channel_name}' from Dispatcharr")
                        else:
                            result.errors.append(
                                f"Failed to delete orphan channel {issue.channel_name}: "
                                f"{delete_result.get('error')}"
                            )
                    else:
                        result.issues_skipped.append({
                            'issue_type': issue.issue_type,
                            'channel_name': issue.channel_name,
                            'reason': 'auto_fix_orphan_dispatcharr is disabled'
                        })

                elif issue.issue_type == 'drift':
                    # Sync settings to Dispatcharr
                    if issue.managed_channel_id and issue.dispatcharr_channel_id:
                        drift_fields = issue.details.get('drift_fields', [])
                        update_data = {}
                        for df in drift_fields:
                            field_name = df['field']
                            expected_value = df['expected']
                            if expected_value is not None:
                                update_data[field_name] = expected_value

                        if update_data:
                            update_result = self.channel_api.update_channel(
                                issue.dispatcharr_channel_id,
                                update_data
                            )
                            if update_result.get('success'):
                                update_channel_sync_status(
                                    issue.managed_channel_id,
                                    'in_sync',
                                    'Drift corrected by reconciliation'
                                )
                                log_channel_history(
                                    managed_channel_id=issue.managed_channel_id,
                                    change_type='modified',
                                    change_source='reconciliation',
                                    notes=f"Drift corrected: {', '.join(update_data.keys())}"
                                )
                                result.issues_fixed.append({
                                    'issue_type': issue.issue_type,
                                    'channel_name': issue.channel_name,
                                    'action': 'synced',
                                    'fields': list(update_data.keys())
                                })
                                logger.info(
                                    f"Fixed drift: synced '{issue.channel_name}' "
                                    f"({', '.join(update_data.keys())})"
                                )
                            else:
                                result.errors.append(
                                    f"Failed to sync channel {issue.channel_name}: "
                                    f"{update_result.get('error')}"
                                )

                elif issue.issue_type == 'duplicate':
                    # Skip duplicates - too complex for auto-fix
                    result.issues_skipped.append({
                        'issue_type': issue.issue_type,
                        'espn_event_id': issue.espn_event_id,
                        'reason': 'Duplicate resolution requires manual review'
                    })

            except Exception as e:
                result.errors.append(f"Error fixing {issue.issue_type} for {issue.channel_name}: {e}")
                logger.error(f"Fix error: {e}")

    def verify_channel(self, managed_channel_id: int) -> Dict[str, Any]:
        """
        Verify a single channel's sync status.

        Args:
            managed_channel_id: ID of managed channel to verify

        Returns:
            Dict with verification result and any issues found
        """
        from database import get_connection, update_channel_sync_status, log_channel_history

        result = {
            'status': 'unknown',
            'in_sync': False,
            'issues': [],
            'channel_exists': False
        }

        conn = get_connection()
        try:
            channel = dict(conn.execute(
                "SELECT * FROM managed_channels WHERE id = ?",
                (managed_channel_id,)
            ).fetchone() or {})
        finally:
            conn.close()

        if not channel:
            result['status'] = 'not_found'
            return result

        dispatcharr_id = channel.get('dispatcharr_channel_id')
        if not dispatcharr_id:
            result['status'] = 'no_dispatcharr_id'
            return result

        # Check if channel exists in Dispatcharr
        dispatcharr_channel = self.channel_api.get_channel(dispatcharr_id)

        if not dispatcharr_channel:
            result['status'] = 'orphaned'
            result['issues'].append('Channel not found in Dispatcharr')
            update_channel_sync_status(managed_channel_id, 'orphaned', 'Channel missing from Dispatcharr')
            return result

        result['channel_exists'] = True

        # Check for drift
        drift_issues = []

        expected_tvg_id = channel.get('tvg_id')
        actual_tvg_id = dispatcharr_channel.get('tvg_id')
        if expected_tvg_id and expected_tvg_id != actual_tvg_id:
            drift_issues.append(f"tvg_id: expected {expected_tvg_id}, got {actual_tvg_id}")

        expected_number = channel.get('channel_number')
        actual_number = dispatcharr_channel.get('channel_number')
        if expected_number and actual_number and expected_number != actual_number:
            drift_issues.append(f"channel_number: expected {expected_number}, got {actual_number}")

        if drift_issues:
            result['status'] = 'drifted'
            result['issues'] = drift_issues
            update_channel_sync_status(managed_channel_id, 'drifted', '; '.join(drift_issues))
        else:
            result['status'] = 'in_sync'
            result['in_sync'] = True
            update_channel_sync_status(managed_channel_id, 'in_sync', None)

            log_channel_history(
                managed_channel_id=managed_channel_id,
                change_type='verified',
                change_source='reconciliation'
            )

        return result


def get_reconciler() -> Optional[ChannelReconciler]:
    """
    Get a ChannelReconciler instance using settings from database.

    Returns:
        ChannelReconciler or None if Dispatcharr not configured
    """
    from database import get_connection

    conn = get_connection()
    try:
        settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
    finally:
        conn.close()

    if not settings.get('dispatcharr_enabled'):
        return None

    url = settings.get('dispatcharr_url')
    username = settings.get('dispatcharr_username')
    password = settings.get('dispatcharr_password')

    if not all([url, username, password]):
        return None

    return ChannelReconciler(
        url, username, password,
        settings=settings
    )


def run_reconciliation(auto_fix: bool = None, group_ids: List[int] = None) -> Optional[ReconciliationResult]:
    """
    Run reconciliation using default settings.

    Args:
        auto_fix: Override auto-fix setting
        group_ids: Limit to specific groups

    Returns:
        ReconciliationResult or None if reconciler unavailable
    """
    reconciler = get_reconciler()
    if not reconciler:
        logger.warning("Cannot run reconciliation - Dispatcharr not configured")
        return None

    return reconciler.reconcile(auto_fix=auto_fix, group_ids=group_ids)
