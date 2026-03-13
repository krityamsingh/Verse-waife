"""SoulCatcher/modules/topcollector.py

Commands:
  /topcollector  —  top 10 players by total characters owned
  /topc          —  alias
  /tc            —  short alias
"""
from __future__ import annotations
import logging
from pyrogram import filters, enums
from pyrogram.types import Message
from .. import app
from ..database import top_collectors

log  = logging.getLogger("SoulCatcher.topcollector")
HTML = enums.ParseMode.HTML
_DIV    = "━━━━━━━━━━━━━━━━━━━━"
_MEDALS = ["🥇", "🥈", "🥉"] + ["🏅"] * 7

def _fmt(n) -> str:
    try:    return f"{int(n):,}"
    except: return str(n)

def _esc(t: str) -> str:
    return str(t).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")


@app.on_message(filters.command(["topcollector", "topc", "tc"]))
async def cmd_topcollector(_, message: Message):
    log.info("/topcollector triggered by uid=%s", message.from_user.id if message.from_user else "?")
    wait = await message.reply_text("⏳ <i>Loading top collectors…</i>", parse_mode=HTML)
    try:
        results = await top_collectors(10)
        if not results:
            return await wait.edit_text(
                "📊 <i>No collectors found yet. Start claiming characters!</i>",
                parse_mode=HTML,
            )

        lines = [f"🎴 <b>TOP 10 COLLECTORS</b>\n<code>{_DIV}</code>\n"]
        for i, r in enumerate(results):
            uid        = r.get("user_id", 0)
            char_count = r.get("char_count", 0)
            name       = _esc(r.get("first_name") or r.get("username") or f"User {uid}")
            medal      = _MEDALS[i] if i < len(_MEDALS) else f"{i+1}."
            if i == 0:
                lines.append(
                    f"{medal} <a href=\"tg://user?id={uid}\"><b>{name}</b></a>\n"
                    f"  🎴 <b><code>{_fmt(char_count)}</code></b> characters"
                )
            else:
                lines.append(
                    f"{medal} <a href=\"tg://user?id={uid}\">{name}</a>  "
                    f"<code>{_fmt(char_count)}</code> 🎴"
                )

        lines.append(f"\n<code>{_DIV}</code>")
        await wait.edit_text("\n".join(lines), parse_mode=HTML, disable_web_page_preview=True)

    except Exception as e:
        log.error("/topcollector error: %s", e, exc_info=True)
        await wait.edit_text("❌ <b>Failed to load collector leaderboard.</b>", parse_mode=HTML)
