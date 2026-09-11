"""
Shared text patterns and regular expressions for headline and title normalization.
"""

import re

# Trailing site-title / publication suffixes to safely strip for display
TITLE_SUFFIX_PATTERN = re.compile(
    r"\s*(?:[-–—|]\s*(?:Moneycontrol(?:\.com)?|India News|Business News|Reuters|Bloomberg|Mint|Livemint|The Economic Times|Economic Times|Business Standard|The Hindu|NDTV Profit|NDTV|Financial Express|Times of India|The Times of India|CNBC|BBC News|BBC|AP News|Associated Press|MarketWatch|Financial Times|WSJ|Wall Street Journal))\s*$",
    re.IGNORECASE,
)
