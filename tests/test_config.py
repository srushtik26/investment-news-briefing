"""
Unit tests for configuration system.
"""

from pathlib import Path
import pytest
from pydantic import ValidationError

from config import Settings, get_settings


def test_default_settings():
    """Test that default settings load with expected types and reasonable defaults."""
    settings = get_settings()
    assert settings.APP_NAME == "Investment Committee News Briefing"
    assert settings.APP_ENV in ("development", "testing", "production")
    assert settings.LOG_LEVEL in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
    assert settings.MAX_INDIA_STORIES > 0
    assert settings.MAX_INTERNATIONAL_STORIES > 0
    assert settings.STORY_LOOKBACK_DAYS > 0
    assert settings.MIN_INDEPENDENT_SOURCES >= 1
    assert settings.MAX_GEMINI_CLASSIFICATIONS == 15
    assert settings.MAX_CORROBORATION_SEARCHES == 20
    assert settings.MIN_VERIFIED_INDIA == 5
    assert settings.MIN_VERIFIED_INTL == 5
    assert settings.MAX_DISCOVERY_INDIA == 40
    assert settings.MAX_DISCOVERY_INTL == 40


def test_custom_settings(tmp_path: Path):
    """Test custom instantiation of Settings with isolated paths."""
    custom = Settings(
        APP_NAME="Custom Briefing",
        APP_ENV="testing",
        LOG_LEVEL="debug",
        DATA_PATH=tmp_path / "data",
        LOGS_PATH=tmp_path / "logs",
        MAX_INDIA_STORIES=3,
        MAX_INTERNATIONAL_STORIES=4,
    )
    assert custom.APP_NAME == "Custom Briefing"
    assert custom.APP_ENV == "testing"
    assert custom.LOG_LEVEL == "DEBUG"  # Validated uppercase
    assert custom.MAX_INDIA_STORIES == 3
    assert custom.MAX_INTERNATIONAL_STORIES == 4


def test_settings_directory_creation(tmp_path: Path):
    """Test ensure_directories creates data and log folders."""
    data_dir = tmp_path / "custom_data"
    logs_dir = tmp_path / "custom_logs"
    assert not data_dir.exists()
    assert not logs_dir.exists()

    settings = Settings(DATA_PATH=data_dir, LOGS_PATH=logs_dir)
    settings.ensure_directories()

    assert data_dir.exists()
    assert logs_dir.exists()


def test_invalid_settings_constraints():
    """Test validation errors on improper configuration limits."""
    with pytest.raises(ValidationError):
        Settings(MAX_INDIA_STORIES=0)  # ge=1

    with pytest.raises(ValidationError):
        Settings(REQUEST_TIMEOUT_SECONDS=-5)
