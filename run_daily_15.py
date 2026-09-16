import re
from typing import Set

from app.logging_config import get_logger
from app.validation.engine import FinalValidationEngine as BaseFinalValidationEngine

logger = get_logger("daily.15")

_NUMBER_RE = re.compile(r"(?<!\w)(?:₹|\$)?\d[\d,]*(?:\.\d+)?%?(?!\w)")


def _canonical_numbers(text: str) -> Set[str]:
    """Extract whole numeric tokens and normalize grouping/symbols for exact comparison."""
    values: Set[str] = set()
    for token in _NUMBER_RE.findall(text or ""):
        normalized = (
            token.replace("₹", "")
            .replace("$", "")
            .replace(",", "")
            .replace("%", "")
            .strip()
        )
        if len(normalized) >= 2:
            values.add(normalized)
    return values


class FifteenStoryValidationEngine(BaseFinalValidationEngine):
    """Canonical 15-story validation engine preserving robust number validation."""
    pass


def run_daily_fifteen_story() -> int:
    """Execute canonical 15-story daily briefing automation."""
    from run_daily import run_daily_briefing

    return run_daily_briefing()


if __name__ == "__main__":
    raise SystemExit(run_daily_fifteen_story())

