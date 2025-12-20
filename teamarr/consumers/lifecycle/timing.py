"""Channel lifecycle timing decisions.

Handles when to create and delete event channels based on timing rules.

Create timing options:
- stream_available: Create immediately when stream exists
- same_day: Create on the day of the event
- day_before: Create 1 day before event
- 2_days_before, 3_days_before, 1_week_before

Delete timing options:
- stream_removed: Delete only when stream disappears
- same_day: Delete at 23:59 of event END date
- day_after: Delete at 23:59 of day after event ends
- 2_days_after, 3_days_after, 1_week_after
"""

from datetime import datetime, timedelta

from teamarr.core import Event
from teamarr.utilities.time_blocks import crosses_midnight
from teamarr.utilities.tz import now_user, to_user_tz

from .types import CreateTiming, DeleteTiming, LifecycleDecision


class ChannelLifecycleManager:
    """Manages event channel creation and deletion timing.

    Usage:
        manager = ChannelLifecycleManager(
            create_timing='same_day',
            delete_timing='day_after',
            default_duration_hours=3.0,
        )

        # Check if channel should be created
        decision = manager.should_create_channel(event)
        if decision.should_act:
            create_channel(event)

        # Check if channel should be deleted
        decision = manager.should_delete_channel(event)
        if decision.should_act:
            delete_channel(event)
    """

    def __init__(
        self,
        create_timing: CreateTiming = "same_day",
        delete_timing: DeleteTiming = "day_after",
        default_duration_hours: float = 3.0,
    ):
        self.create_timing = create_timing
        self.delete_timing = delete_timing
        self.default_duration_hours = default_duration_hours

    def should_create_channel(
        self,
        event: Event,
        stream_exists: bool = False,
    ) -> LifecycleDecision:
        """Determine if a channel should be created for this event.

        Args:
            event: The event to check
            stream_exists: Whether a matching stream currently exists

        Returns:
            LifecycleDecision with should_act and reason
        """
        if self.create_timing == "manual":
            return LifecycleDecision(False, "Manual creation only")

        if self.create_timing == "stream_available":
            if stream_exists:
                return LifecycleDecision(True, "Stream available")
            return LifecycleDecision(False, "Waiting for stream")

        # Calculate create threshold
        create_threshold = self._calculate_create_threshold(event)
        now = now_user()

        # Check if we're past delete threshold (prevents create-then-delete)
        delete_threshold = self._calculate_delete_threshold(event)
        if delete_threshold and now >= delete_threshold:
            return LifecycleDecision(
                False,
                f"Past delete threshold ({delete_threshold.strftime('%m/%d %I:%M %p')})",
                delete_threshold,
            )

        if now >= create_threshold:
            return LifecycleDecision(
                True,
                f"Create threshold reached ({create_threshold.strftime('%m/%d %I:%M %p')})",
                create_threshold,
            )

        return LifecycleDecision(
            False,
            f"Before create threshold ({create_threshold.strftime('%m/%d %I:%M %p')})",
            create_threshold,
        )

    def should_delete_channel(
        self,
        event: Event,
        stream_exists: bool = True,
    ) -> LifecycleDecision:
        """Determine if a channel should be deleted for this event.

        Args:
            event: The event to check
            stream_exists: Whether a matching stream currently exists

        Returns:
            LifecycleDecision with should_act and reason
        """
        if self.delete_timing == "manual":
            return LifecycleDecision(False, "Manual deletion only")

        if self.delete_timing == "stream_removed":
            if not stream_exists:
                return LifecycleDecision(True, "Stream removed")
            return LifecycleDecision(False, "Stream still exists")

        # Calculate delete threshold
        delete_threshold = self._calculate_delete_threshold(event)
        if not delete_threshold:
            return LifecycleDecision(False, "Could not calculate delete time")

        now = now_user()

        if now >= delete_threshold:
            return LifecycleDecision(
                True,
                f"Delete threshold reached ({delete_threshold.strftime('%m/%d %I:%M %p')})",
                delete_threshold,
            )

        return LifecycleDecision(
            False,
            f"Before delete threshold ({delete_threshold.strftime('%m/%d %I:%M %p')})",
            delete_threshold,
        )

    def _calculate_create_threshold(self, event: Event) -> datetime:
        """Calculate when channel should be created."""
        event_start = to_user_tz(event.start_time)

        # Start of event day (midnight)
        day_start = event_start.replace(hour=0, minute=0, second=0, microsecond=0)

        timing_map = {
            "same_day": day_start,
            "day_before": day_start - timedelta(days=1),
            "2_days_before": day_start - timedelta(days=2),
            "3_days_before": day_start - timedelta(days=3),
            "1_week_before": day_start - timedelta(days=7),
        }

        return timing_map.get(self.create_timing, day_start)

    def _calculate_delete_threshold(self, event: Event) -> datetime | None:
        """Calculate when channel should be deleted.

        Uses event END date for midnight-crossing games.
        """
        event_start = to_user_tz(event.start_time)
        event_end = event_start + timedelta(hours=self.default_duration_hours)

        # Use END date (important for midnight-crossing games)
        end_date = event_end.date()

        # End of day (23:59:59)
        day_end = datetime.combine(
            end_date,
            datetime.max.time(),
        ).replace(tzinfo=event_end.tzinfo)

        timing_map = {
            "same_day": day_end,
            "day_after": day_end + timedelta(days=1),
            "2_days_after": day_end + timedelta(days=2),
            "3_days_after": day_end + timedelta(days=3),
            "1_week_after": day_end + timedelta(days=7),
        }

        return timing_map.get(self.delete_timing)

    def calculate_delete_time(self, event: Event) -> datetime | None:
        """Calculate scheduled delete time for an event."""
        return self._calculate_delete_threshold(event)

    def get_event_end_time(self, event: Event) -> datetime:
        """Calculate estimated event end time."""
        return to_user_tz(event.start_time) + timedelta(hours=self.default_duration_hours)

    def event_crosses_midnight(self, event: Event) -> bool:
        """Check if event crosses midnight."""
        start = to_user_tz(event.start_time)
        end = self.get_event_end_time(event)
        return crosses_midnight(start, end)
