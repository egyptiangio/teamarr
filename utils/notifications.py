"""
Server-side notification helpers for Teamarr
Provides consistent notification formatting and flash message utilities
"""

from flask import flash, jsonify
from typing import Optional, Dict, Any
from enum import Enum


class NotificationType(Enum):
    """Notification severity levels"""
    SUCCESS = 'success'
    ERROR = 'error'
    WARNING = 'warning'
    INFO = 'info'


class NotificationHelper:
    """Helper class for managing notifications"""

    @staticmethod
    def flash_success(message: str, title: Optional[str] = None):
        """
        Flash a success notification

        Args:
            message: Notification message
            title: Optional custom title (defaults to "Success")
        """
        flash(message, NotificationType.SUCCESS.value)

    @staticmethod
    def flash_error(message: str, title: Optional[str] = None):
        """
        Flash an error notification

        Args:
            message: Notification message
            title: Optional custom title (defaults to "Error")
        """
        flash(message, NotificationType.ERROR.value)

    @staticmethod
    def flash_warning(message: str, title: Optional[str] = None):
        """
        Flash a warning notification

        Args:
            message: Notification message
            title: Optional custom title (defaults to "Warning")
        """
        flash(message, NotificationType.WARNING.value)

    @staticmethod
    def flash_info(message: str, title: Optional[str] = None):
        """
        Flash an info notification

        Args:
            message: Notification message
            title: Optional custom title (defaults to "Info")
        """
        flash(message, NotificationType.INFO.value)

    @staticmethod
    def json_response(
        success: bool,
        message: str,
        data: Optional[Dict[str, Any]] = None,
        notification_type: Optional[NotificationType] = None
    ) -> Dict[str, Any]:
        """
        Create a standardized JSON response with notification info

        Args:
            success: Whether the operation succeeded
            message: Response message
            data: Optional additional data to include
            notification_type: Optional notification type override

        Returns:
            Dictionary for JSON response
        """
        response = {
            'success': success,
            'message': message
        }

        if data:
            response['data'] = data

        # Auto-determine notification type if not provided
        if notification_type is None:
            notification_type = NotificationType.SUCCESS if success else NotificationType.ERROR

        response['notification_type'] = notification_type.value

        return response

    @staticmethod
    def success_response(message: str, data: Optional[Dict[str, Any]] = None):
        """Shorthand for success JSON response"""
        return NotificationHelper.json_response(True, message, data, NotificationType.SUCCESS)

    @staticmethod
    def error_response(message: str, data: Optional[Dict[str, Any]] = None):
        """Shorthand for error JSON response"""
        return NotificationHelper.json_response(False, message, data, NotificationType.ERROR)

    @staticmethod
    def warning_response(message: str, data: Optional[Dict[str, Any]] = None):
        """Shorthand for warning JSON response"""
        return NotificationHelper.json_response(True, message, data, NotificationType.WARNING)


# Convenience functions (module-level shortcuts)

def notify_success(message: str, title: Optional[str] = None):
    """Flash a success notification"""
    NotificationHelper.flash_success(message, title)


def notify_error(message: str, title: Optional[str] = None):
    """Flash an error notification"""
    NotificationHelper.flash_error(message, title)


def notify_warning(message: str, title: Optional[str] = None):
    """Flash a warning notification"""
    NotificationHelper.flash_warning(message, title)


def notify_info(message: str, title: Optional[str] = None):
    """Flash an info notification"""
    NotificationHelper.flash_info(message, title)


def json_success(message: str, data: Optional[Dict[str, Any]] = None):
    """Return JSON success response"""
    return jsonify(NotificationHelper.success_response(message, data))


def json_error(message: str, data: Optional[Dict[str, Any]] = None):
    """Return JSON error response"""
    return jsonify(NotificationHelper.error_response(message, data))


def json_warning(message: str, data: Optional[Dict[str, Any]] = None):
    """Return JSON warning response"""
    return jsonify(NotificationHelper.warning_response(message, data))


# Common notification templates

class NotificationTemplates:
    """Pre-defined notification message templates"""

    @staticmethod
    def created(entity: str, name: str) -> str:
        """Template for entity creation"""
        return f"{entity} '{name}' created successfully!"

    @staticmethod
    def updated(entity: str, name: str) -> str:
        """Template for entity update"""
        return f"{entity} '{name}' updated successfully!"

    @staticmethod
    def deleted(entity: str, name: str) -> str:
        """Template for entity deletion"""
        return f"{entity} '{name}' deleted successfully!"

    @staticmethod
    def bulk_operation(count: int, action: str, entity: str) -> str:
        """Template for bulk operations"""
        plural = entity if entity.endswith('s') else f"{entity}s"
        return f"{action} {count} {plural if count != 1 else entity}"

    @staticmethod
    def not_found(entity: str) -> str:
        """Template for not found errors"""
        return f"{entity} not found"

    @staticmethod
    def validation_error(field: str, issue: str) -> str:
        """Template for validation errors"""
        return f"Validation error in '{field}': {issue}"

    @staticmethod
    def operation_failed(operation: str, reason: Optional[str] = None) -> str:
        """Template for operation failures"""
        if reason:
            return f"{operation} failed: {reason}"
        return f"{operation} failed"


# Usage examples:
#
# # Flash messages
# notify_success("Template created successfully!")
# notify_error("Failed to save team")
# notify_warning("This template has 5 teams assigned")
#
# # JSON responses (for AJAX)
# return json_success("Teams updated", {"count": 5})
# return json_error("Invalid template ID")
#
# # Using templates
# notify_success(NotificationTemplates.created("Template", "NBA Standard"))
# return json_success(NotificationTemplates.bulk_operation(3, "Deleted", "team"))
