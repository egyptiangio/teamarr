"""
Comprehensive logging system for Teamarr
Provides structured logging with multiple levels and handlers
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Optional


class TeamarrLogger:
    """Centralized logger for Teamarr application"""

    _instance = None
    _loggers = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, 'initialized'):
            self.initialized = False
            self.log_dir = None
            self.log_level = logging.DEBUG

    def setup(self, log_dir: str, log_level: str = 'DEBUG'):
        """
        Initialize the logging system

        Args:
            log_dir: Directory to store log files
            log_level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        """
        if self.initialized:
            return

        self.log_dir = log_dir
        self.log_level = getattr(logging, log_level.upper(), logging.DEBUG)

        # Create logs directory
        os.makedirs(log_dir, exist_ok=True)

        # Configure root logger
        self._configure_root_logger()

        self.initialized = True

    def _configure_root_logger(self):
        """Configure the root logger with handlers"""

        # Define log format
        log_format = logging.Formatter(
            '[%(asctime)s] %(levelname)-8s [%(name)s:%(lineno)d] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(self.log_level)
        console_handler.setFormatter(log_format)

        # Main log file handler (rotating)
        main_log_file = os.path.join(self.log_dir, 'teamarr.log')
        file_handler = RotatingFileHandler(
            main_log_file,
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5
        )
        file_handler.setLevel(logging.DEBUG)  # Always log DEBUG to file
        file_handler.setFormatter(log_format)

        # Error log file handler (separate file for errors)
        error_log_file = os.path.join(self.log_dir, 'teamarr_errors.log')
        error_handler = RotatingFileHandler(
            error_log_file,
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=3
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(log_format)

        # API log file handler (for API-specific logs)
        api_log_file = os.path.join(self.log_dir, 'teamarr_api.log')
        api_handler = RotatingFileHandler(
            api_log_file,
            maxBytes=5 * 1024 * 1024,  # 5MB
            backupCount=3
        )
        api_handler.setLevel(logging.DEBUG)
        api_handler.setFormatter(log_format)
        api_handler.addFilter(lambda record: 'api' in record.name.lower())

        # Configure root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(self.log_level)
        root_logger.addHandler(console_handler)
        root_logger.addHandler(file_handler)
        root_logger.addHandler(error_handler)
        root_logger.addHandler(api_handler)

        # Reduce verbosity of werkzeug (Flask development server)
        logging.getLogger('werkzeug').setLevel(logging.WARNING)

    def get_logger(self, name: str) -> logging.Logger:
        """
        Get or create a logger for a specific module

        Args:
            name: Logger name (usually __name__ of the module)

        Returns:
            Configured logger instance
        """
        if name not in self._loggers:
            logger = logging.getLogger(name)
            logger.setLevel(self.log_level)
            self._loggers[name] = logger

        return self._loggers[name]


# Global instance
_logger_instance = TeamarrLogger()


def setup_logging(app, log_level: str = 'DEBUG'):
    """
    Setup logging for Flask app

    Args:
        app: Flask application instance
        log_level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    # Prefer /app/data/logs for Docker (persisted via volume)
    if os.path.exists('/app/data'):
        log_dir = '/app/data/logs'
    else:
        # Local development - use logs/ in project root
        log_dir = os.path.join(os.path.dirname(app.root_path), 'logs')

    _logger_instance.setup(log_dir, log_level)

    # Configure Flask app logger
    app.logger.setLevel(_logger_instance.log_level)

    # Print startup banner
    app.logger.info('=' * 80)
    app.logger.info('🚀 Teamarr - Dynamic EPG Generator for Sports Channels')
    app.logger.info('=' * 80)
    app.logger.info(f'Log level: {log_level}')
    app.logger.info(f'Log directory: {log_dir}')
    app.logger.info(f'Port: 9195')
    app.logger.info('=' * 80)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance for a specific module

    Args:
        name: Logger name (usually __name__)

    Returns:
        Logger instance

    Example:
        logger = get_logger(__name__)
        logger.info("This is an info message")
        logger.debug("This is a debug message")
        logger.error("This is an error message")
    """
    return _logger_instance.get_logger(name)
