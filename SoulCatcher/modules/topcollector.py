"""SoulCatcher/modules/topcollector.py

Command:
  /topcollector  —  top 10 players ranked by total characters owned.
                    Uses the top_collectors() aggregation pipeline in
                    database.py (single MongoDB pipeline, no per-user queries).
"""
from __future__ import annotations

import logging

from pyrogram import filters
from pyrogram.types import Message

from .. import app
from ..database import top_collectors
from ._profile_helpers import HTML, DIV, MEDALS, fmt, esc

log = logging.getLogger("SoulCatcher.topcollector")


@app.on_message(filters.command("topcollector"))
async def cmd_topcollector(_, message: Message):
    wait = await message.reply_text("⏳ <i>Loading top collectors…</i>", parse_mode=HTML)

    try:
        results = await top_collectors(10)

        if not results:
            return await wait.edit_text(
                "📊 <i>No collectors found yet. Start claiming characters!</i>",
                parse_mode=HTML,
            )

        lines = [f"🎴 <b>TOP 10 COLLECTORS</b>\n<code>{DIV}</code>\n"]

        for i, r in enumerate(results):
            uid        = r.get("user_id", 0)
            char_count = r.get("char_count", 0)
            name       = esc(r.get("first_name") or r.get("username") or f"User {uid}")
            medal      = MEDALS[i] if i < len(MEDALS) else f"{i + 1}."

            if i == 0:
                lines.append(
                    f"{medal} <a href=\"tg://user?id={uid}\"><b>{name}</b></a>\n"
                    f"  🎴 <b><code>{fmt(char_count)}</code></b> characters"
                )
            else:
                lines.append(
                    f"{medal} <a href=\"tg://user?id={uid}\">{name}</a>  "
                    f"<code>{fmt(char_count)}</code> 🎴"
                )

        lines.append(f"\n<code>{DIV}</code>")
        await wait.edit_text(
            "\n".join(lines), parse_mode=HTML, disable_web_page_preview=True
        )

    except Exception as e:
        log.error("/topcollector error: %s", e)
        await wait.edit_text(
            "❌ <b>Failed to load collector leaderboard.</b>", parse_mode=HTML
        )
