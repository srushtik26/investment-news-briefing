"""
Formatting package.
Responsible for assembling final WhatsApp-ready briefing texts, copy/paste versions, and reports.
"""

from app.formatting.formatter import BriefingFormatter, FormattedBriefing, build_copy_paste_text

__all__ = ["BriefingFormatter", "FormattedBriefing", "build_copy_paste_text"]

