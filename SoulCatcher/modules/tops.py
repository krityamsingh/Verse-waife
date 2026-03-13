"""SoulCatcher/modules/tops.py

Commands:
  /ktop   — top 10 kakera holders
  /ctop   — top 10 character collectors

Uses top_richest() and top_collectors() from database.py.
Those functions do batch queries against the real MongoDB schema.
"""

from __future__ import annotations
import logging

from pyrogram import filters, enums
from pyrogram.types import Message

from .. import app
from ..database import top_richest, top_collectors

log  = logging.getLogger("SoulCatcher.tops")
HTML = enums.ParseMode.HTML

_DIV   = "━━━━━━━━━━━━━━━━━━━━━━━━"
MEDALS = ["🥇", "🥈", "🥉", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"]


# ── helpers ───────────────────────────────────────────────────────────────────

def _fmt(n) -> str:
    """Format a number with thousands separators, e.g. 1234567 → '1,234,567'."""
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def _esc(s) -> str:
    """Escape HTML special characters so names never break message markup."""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _display_name(row: dict) -> str:
    """
    Build the best display name from what the DB returned.
    Priority: first_name (+ last_name if present) → @username → 'User <id>'
    """
    uid      = row.get("user_id", "?")
    first    = (row.get("first_name") or "").strip()
    last     = (row.get("last_name")  or "").strip()
    username = (row.get("username")   or "").strip()

    if first:
        full = f"{first} {last}".strip()
        return _esc(full)
    if username:
        return _esc(f"@{username}")
    return _esc(f"User {uid}")


def _link(row: dict) -> str:
    """Telegram inline mention: clickable name that opens the user's profile."""
    uid = row.get("user_id", 0)
    return f'<a href="tg://user?id={uid}"><b>{_display_name(row)}</b></a>'


def _medal(i: int) -> str:
    """Return the medal/circled-number emoji for position i (0-based)."""
    return MEDALS[i] if i < len(MEDALS) else f"{i + 1}."


# ── /ktop ─────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("ktop"))
async def cmd_ktop(_, message: Message):
    """Top 10 users by kakera balance."""
    wait = await message.reply_text(
        "⏳ <i>Loading kakera leaderboard…</i>", parse_mode=HTML
    )

    try:
        rows = await top_richest(limit=10, exclude_banned=True)
    except Exception as exc:
        log.error("ktop db error: %s", exc, exc_info=True)
        await wait.edit_text(
            f"❌ <b>Database error:</b>\n<code>{_esc(str(exc)[:300])}</code>",
            parse_mode=HTML,
        )
        return

    if not rows:
        await wait.edit_text(
            "📊 <i>No kakera holders yet. Start claiming characters!</i>",
            parse_mode=HTML,
        )
        return

    lines = [
        "🌸 <b>KAKERA TOP 10</b> 🌸",
        f"<code>{_DIV}</code>",
        "",
    ]

    for i, row in enumerate(rows):
        bal = row.get("balance", 0)
        if i == 0:
            lines += [
                f"{_medal(i)} {_link(row)}",
                f"   🌸 <b><code>{_fmt(bal)}</code></b> kakera",
                "",
            ]
        else:
            lines.append(
                f"{_medal(i)} {_link(row)}  —  <code>{_fmt(bal)}</code> 🌸"
            )

    lines += ["", f"<code>{_DIV}</code>"]

    await wait.edit_text(
        "\n".join(lines),
        parse_mode=HTML,
        disable_web_page_preview=True,
    )


# ── /ctop ─────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("ctop"))
async def cmd_ctop(_, message: Message):
    """Top 10 users by total characters owned (unique count as tiebreaker)."""
    wait = await message.reply_text(
        "⏳ <i>Loading collector leaderboard…</i>", parse_mode=HTML
    )

    try:
        rows = await top_collectors(limit=10, exclude_banned=True)
    except Exception as exc:
        log.error("ctop db error: %s", exc, exc_info=True)
        await wait.edit_text(
            f"❌ <b>Database error:</b>\n<code>{_esc(str(exc)[:300])}</code>",
            parse_mode=HTML,
        )
        return

    if not rows:
        await wait.edit_text(
            "📊 <i>No collectors yet. Start claiming characters!</i>",
            parse_mode=HTML,
        )
        return

    lines = [
        "🃏 <b>CHARACTER TOP 10</b> 🃏",
        f"<code>{_DIV}</code>",
        "",
    ]

    for i, row in enumerate(rows):
        total  = row.get("total",  0)
        unique = row.get("unique", 0)
        if i == 0:
            lines += [
                f"{_medal(i)} {_link(row)}",
                f"   📦 <b><code>{_fmt(total)}</code></b> total  ✨ <b><code>{_fmt(unique)}</code></b> unique",
                "",
            ]
        else:
            lines.append(
                f"{_medal(i)} {_link(row)}  —  "
                f"<code>{_fmt(total)}</code> total · <code>{_fmt(unique)}</code> unique"
            )

    lines += ["", f"<code>{_DIV}</code>"]

    await wait.edit_text(
        "\n".join(lines),
        parse_mode=HTML,
        disable_web_page_preview=True,
    )
