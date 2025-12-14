"""Centralized logging configuration for Teamarr.

Provides structured logging with console and file output.
Call setup_logging() once at application startup.

Usage:
    from teamarr.utilities.logging import setup_logging, get_logger

    # At startup (main.py or app.py)
    setup_logging()

    # In any module
    logger = get_logger(__name__)
    logger.info("Something happened")

Environment variables:
    LOG_LEVEL: DEBUG, INFO, WARNING, ERROR, CRITICAL (default: INFO)
    LOG_DIR: Directory for log files (default: ./logs or /app/data/logs in Docker)
    LOG_FORMAT: "text" or "json" (default: text)
"""

import json
import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Track if logging has been configured
_configured = False


class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logging.

    Useful for log aggregation systems (ELK, Loki, etc.)
    """

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
        }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add extra fields if present
        if hasattr(record, "extra_data"):
            log_data["data"] = record.extra_data

        return json.dumps(log_data)


def _get_log_dir() -> Path:
    """Determine log directory with Docker awareness."""
    # 1. Explicit env var
    if env_dir := os.getenv("LOG_DIR"):
        return Path(env_dir)

    # 2. Docker environment (persisted volume)
    docker_path = Path("/app/data/logs")
    if docker_path.parent.exists():
        return docker_path

    # 3. Local development - project root/logs
    # Walk up from this file to find project root (where pyproject.toml is)
    current = Path(__file__).parent
    for _ in range(5):  # Max 5 levels up
        if (current / "pyproject.toml").exists():
            return current / "logs"
        current = current.parent

    # 4. Fallback to current directory
    return Path("logs")


def _get_log_level() -> int:
    """Get log level from environment."""
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    return getattr(logging, level_name, logging.INFO)


def _get_formatter(use_json: bool = False) -> logging.Formatter:
    """Get the appropriate formatter."""
    if use_json:
        return JSONFormatter()

    return logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def setup_logging(
    log_level: str | None = None,
    log_dir: str | Path | None = None,
    use_json: bool | None = None,
) -> None:
    """Initialize the logging system.

    Call this once at application startup. Safe to call multiple times
    (subsequent calls are no-ops).

    Args:
        log_level: Override LOG_LEVEL env var
        log_dir: Override LOG_DIR env var
        use_json: Override LOG_FORMAT env var (True for JSON output)
    """
    global _configured
    if _configured:
        return

    # Resolve configuration
    level = getattr(logging, (log_level or "").upper(), None) or _get_log_level()
    log_path = Path(log_dir) if log_dir else _get_log_dir()

    if use_json is None:
        use_json = os.getenv("LOG_FORMAT", "text").lower() == "json"

    # Create log directory
    log_path.mkdir(parents=True, exist_ok=True)

    # Get formatter
    formatter = _get_formatter(use_json)

    # === Console Handler ===
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)

    # === Main Log File (rotating) ===
    main_log = log_path / "teamarr.log"
    file_handler = RotatingFileHandler(
        main_log,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)  # Always log DEBUG to file
    file_handler.setFormatter(formatter)

    # === Error Log File (errors only) ===
    error_log = log_path / "teamarr_errors.log"
    error_handler = RotatingFileHandler(
        error_log,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=3,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)

    # === Configure Root Logger ===
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # Handlers filter from here

    # Remove any existing handlers (in case of reload)
    root_logger.handlers.clear()

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(error_handler)

    # === Quiet Noisy Loggers ===
    noisy_loggers = [
        "uvicorn",
        "uvicorn.access",
        "uvicorn.error",
        "httpx",
        "httpcore",
        "httpcore.connection",
        "httpcore.http11",
        "hpack",
        "watchfiles",
    ]
    for name in noisy_loggers:
        logging.getLogger(name).setLevel(logging.WARNING)

    # Keep uvicorn.error at INFO for startup messages
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)

    _configured = True

    # Log startup info
    logger = logging.getLogger("teamarr")
    logger.info("=" * 60)
    logger.info("Teamarr v2 - Dynamic EPG Generator for Sports Channels")
    logger.info("=" * 60)
    logger.info(f"Log level: {logging.getLevelName(level)}")
    logger.info(f"Log directory: {log_path}")
    logger.info(f"Log format: {'JSON' if use_json else 'text'}")
    logger.info("=" * 60)


def get_logger(name: str) -> logging.Logger:
    """Get a logger for a module.

    Args:
        name: Logger name (usually __name__)

    Returns:
        Configured logger instance

    Example:
        logger = get_logger(__name__)
        logger.info("Processing started")
        logger.debug("Details: %s", data)
        logger.error("Failed to process", exc_info=True)
    """
    return logging.getLogger(name)


def log_with_data(
    logger: logging.Logger,
    level: int,
    message: str,
    data: dict | None = None,
) -> None:
    """Log a message with structured data.

    Useful for adding context that appears in JSON logs.

    Args:
        logger: Logger instance
        level: Log level (e.g., logging.INFO)
        message: Log message
        data: Additional structured data

    Example:
        log_with_data(logger, logging.INFO, "EPG generated", {
            "programmes": 150,
            "duration_ms": 1234,
            "provider": "espn"
        })
    """
    record = logger.makeRecord(
        logger.name,
        level,
        "(unknown)",
        0,
        message,
        (),
        None,
    )
    if data:
        record.extra_data = data
    logger.handle(record)
