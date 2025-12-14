"""Utility modules for Teamarr"""

from .logger import setup_logging, get_logger


def to_pascal_case(text: str) -> str:
    """
    Convert text to PascalCase, stripping punctuation.

    Examples:
        "Detroit Pistons" -> "DetroitPistons"
        "St. Louis Blues" -> "StLouisBlues"
    """
    if not text:
        return ''
    return ''.join(
        ''.join(c for c in word if c.isalnum()).capitalize()
        for word in text.split()
    )
from .notifications import (
    NotificationHelper,
    NotificationType,
    NotificationTemplates,
    notify_success,
    notify_error,
    notify_warning,
    notify_info,
    json_success,
    json_error,
    json_warning
)

__all__ = [
    'setup_logging',
    'get_logger',
    'to_pascal_case',
    'NotificationHelper',
    'NotificationType',
    'NotificationTemplates',
    'notify_success',
    'notify_error',
    'notify_warning',
    'notify_info',
    'json_success',
    'json_error',
    'json_warning'
]
