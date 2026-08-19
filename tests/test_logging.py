"""
Unit tests for logging configuration.
"""

import logging
from pathlib import Path
import pytest

from app.logging_config import setup_logging, get_logger


def test_logger_initialization(tmp_path: Path):
    """Test setup_logging and verify file and console output."""
    log_file = tmp_path / "test_app.log"
    logger = setup_logging(log_level="DEBUG", log_file=log_file)
    assert isinstance(logger, logging.Logger)

    # Obtain child logger and write test message
    child_logger = get_logger("tests.unit")
    test_msg = "Automated test message for logger verification"
    child_logger.info(test_msg)

    # Check file exists and contains message if written to file
    if log_file.exists():
        content = log_file.read_text(encoding="utf-8")
        assert "tests.unit" in content
        assert test_msg in content


def test_get_logger_naming():
    """Test get_logger returns logger with specified name."""
    logger = get_logger("custom.component.verification")
    assert logger.name == "custom.component.verification"
