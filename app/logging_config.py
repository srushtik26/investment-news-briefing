"""
Logging configuration module.

Configures formatted console logging and rotating file logging
for all components across the application.
"""

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Optional

from config import get_settings


_LOGGER_INITIALIZED = False


def setup_logging(
    log_level: Optional[str] = None,
    log_file: Optional[Path] = None,
) -> logging.Logger:
    """
    Initialize and configure the root logger and application logger.

    Args:
        log_level: Optional override for log level (e.g., 'DEBUG', 'INFO').
        log_file: Optional path to log file. Defaults to `logs/app.log`.

    Returns:
        The configured root logger.
    """
    global _LOGGER_INITIALIZED

    settings = get_settings()
    level_str = (log_level or settings.LOG_LEVEL).upper()
    numeric_level = getattr(logging, level_str, logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Avoid adding duplicate handlers on multiple setup calls
    if _LOGGER_INITIALIZED:
        return root_logger

    # Clear existing handlers if any
    root_logger.handlers.clear()

    # Formatter for structured logs
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 1. Console Handler (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 2. Rotating File Handler
    target_log_file = log_file or (settings.LOGS_PATH / "app.log")
    target_log_file.parent.mkdir(parents=True, exist_ok=True)

    file_handler = logging.handlers.RotatingFileHandler(
        filename=str(target_log_file),
        maxBytes=10 * 1024 * 1024,  # 10 MB per file
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(numeric_level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    _LOGGER_INITIALIZED = True
    root_logger.debug("Logging initialized at %s level writing to %s", level_str, target_log_file)
    return root_logger


def get_logger(name: str) -> logging.Logger:
    """
    Obtain a child logger for a specific module or component.

    Args:
        name: Name of the logger, typically `__name__`.

    Returns:
        logging.Logger configured instance.
    """
    if not _LOGGER_INITIALIZED:
        setup_logging()
    return logging.getLogger(name)
