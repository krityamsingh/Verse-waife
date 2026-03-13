"""SoulCatcher/modules/profile_helpers.py

Shared constants and pure utility functions used by every split
profile command module (status.py, bal.py, profile.py, richest.py,
topcollector.py, rarityinfo.py, event.py).

Nothing in this file registers any Pyrogram handler — it is
import-only, so it adds zero overhead at bot startup beyond a
single module load that is shared (cached) by all consumers.
"""
from __future__ import annotations

from pyrogram import enums

HTML = enums.ParseMode.HTML
MD   = enums.ParseMode.MARKDOWN

# ── Dividers ──────────────────────────────────────────────────────────────────
DIV  = "━━━━━━━━━━━━━━━━━━━━"
SDIV = "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄"

# ── Medal sequence for leaderboards ──────────────────────────────────────────
MEDALS = ["🥇", "🥈", "🥉"] + ["🏅"] * 7


# ── Formatting helpers ────────────────────────────────────────────────────────

def fmt(n) -> str:
    """Comma-format a number. Falls back to str() on any error."""
    try:
        return f"{int(n):,}"
    except Exception:
        return str(n)


def bar(pct: float, w: int = 12) -> str:
    """Return a █/░ progress bar string for a 0–1 percentage."""
    filled = round(max(0.0, min(1.0, pct)) * w)
    return "█" * filled + "░" * (w - filled)


def wealth(n: int) -> str:
    """Map a kakera balance to a human-readable wealth title."""
    for thr, lbl in reversed([
        (0,          "Lost Soul"),
        (1_000,      "Traveler"),
        (5_000,      "Merchant"),
        (20_000,     "Guild Master"),
        (50_000,     "Lord"),
        (150_000,    "Duke"),
        (500_000,    "Prince"),
        (1_000_000,  "King"),
        (5_000_000,  "Emperor"),
        (10_000_000, "Soul Lord"),
    ]):
        if n >= thr:
            return lbl
    return "Lost Soul"


def esc(text: str) -> str:
    """Escape HTML special characters."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def mention(name: str, uid: int) -> str:
    """Return a clickable Telegram HTML mention."""
    return f'<a href="tg://user?id={uid}"><b>{esc(name)}</b></a>'
