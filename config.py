"""
Application Configuration Module.

Provides centralized, type-safe settings management using Pydantic Settings
and loads configuration values from environment variables or .env files.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Project Root Directory
BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    """Application configuration settings loaded from environment or .env."""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Application Info
    APP_NAME: str = Field(default="Investment Committee News Briefing", description="Name of the application")
    APP_ENV: Literal["development", "testing", "production"] = Field(
        default="development", description="Current operating environment"
    )
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", description="Application log level"
    )

    # Storage Paths
    BASE_PATH: Path = Field(default=BASE_DIR, description="Root directory path")
    DATA_PATH: Path = Field(default=BASE_DIR / "data", description="Data storage directory")
    LOGS_PATH: Path = Field(default=BASE_DIR / "logs", description="Logs directory")

    # Gemini AI Configuration
    GEMINI_API_KEY: Optional[str] = Field(default=None, description="Google Gemini API Key")
    GEMINI_MODEL: str = Field(default="gemini-3.6-flash", description="Default Gemini model name")

    # Database Configuration (SQLite default, PostgreSQL ready)
    DATABASE_URL: str = Field(
        default="sqlite:///./data/briefings.db",
        description="Database connection URL",
    )

    # Briefing Business Rules
    MAX_INDIA_STORIES: int = Field(default=5, ge=1, le=20, description="Max stories in India section")
    MAX_INTERNATIONAL_STORIES: int = Field(default=5, ge=1, le=20, description="Max stories in International section")
    STORY_LOOKBACK_DAYS: int = Field(default=3, ge=1, le=30, description="Days to look back to prevent repeat stories")
    MIN_INDEPENDENT_SOURCES: int = Field(
        default=2, ge=1, le=10, description="Minimum independent sources required for major event verification"
    )

    # Pipeline Execution & Resource Limits
    MAX_GEMINI_CLASSIFICATIONS: int = Field(
        default=15, ge=1, le=100, description="Max live Gemini classification calls per run"
    )
    MAX_CORROBORATION_SEARCHES: int = Field(
        default=20, ge=1, le=100, description="Max corroboration RSS searches per run"
    )
    MIN_VERIFIED_INDIA: int = Field(
        default=5, ge=1, le=20, description="Minimum verified India events required for sufficiency gate"
    )
    MIN_VERIFIED_INTL: int = Field(
        default=5, ge=1, le=20, description="Minimum verified International events required for sufficiency gate"
    )
    MAX_DISCOVERY_INDIA: int = Field(
        default=40, ge=5, le=200, description="Target discovery count for India articles"
    )
    MAX_DISCOVERY_INTL: int = Field(
        default=40, ge=5, le=200, description="Target discovery count for International articles"
    )

    # SerpAPI Configuration (Optional Corroboration Fallback)
    SERPAPI_API_KEY: Optional[str] = Field(default=None, description="Optional SerpAPI Key for secondary corroboration fallback")
    MAX_SERPAPI_SEARCHES_PER_RUN: int = Field(
        default=8, ge=0, le=50, description="Max SerpAPI searches per pipeline run"
    )

    # Extraction & Networking
    REQUEST_TIMEOUT_SECONDS: int = Field(default=15, ge=1, le=120, description="HTTP request timeout in seconds")
    USER_AGENT: str = Field(
        default="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        description="User-Agent header for HTTP extraction requests",
    )

    @field_validator("LOG_LEVEL", mode="before")
    @classmethod
    def normalize_log_level(cls, v: str) -> str:
        """Ensure log level string is uppercase."""
        if isinstance(v, str):
            return v.strip().upper()
        return v

    def ensure_directories(self) -> None:
        """Create data and log directories if they do not exist."""
        self.DATA_PATH.mkdir(parents=True, exist_ok=True)
        self.LOGS_PATH.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Get cached application settings instance.
    
    Ensures storage directories exist on initial load.
    """
    settings = Settings()
    settings.ensure_directories()
    return settings
