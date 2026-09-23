from typing import Set

from app.logging_config import get_logger
from app.validation.engine import FinalValidationEngine as BaseFinalValidationEngine
from app.validation.shared import canonical_numeric_tokens

logger = get_logger("daily.15")

# Canonical numeric tokens helper delegated to app.validation.shared
_canonical_numbers = canonical_numeric_tokens


class FifteenStoryValidationEngine(BaseFinalValidationEngine):
    """Canonical 15-story validation engine preserving robust number validation."""
    pass


def run_daily_fifteen_story() -> int:
    """Execute canonical 15-story daily briefing automation."""
    from run_daily import run_daily_briefing

    return run_daily_briefing()


if __name__ == "__main__":
    raise SystemExit(run_daily_fifteen_story())

