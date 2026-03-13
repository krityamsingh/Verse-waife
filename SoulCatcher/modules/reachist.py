"""SoulCatcher/modules/richest.py

Command:
  /richest  —  top 10 players by kakera balance.
"""
from __future__ import annotations
import logging
from pyrogram import filters, enums
from pyrogram.types import Message
from .. import app
from ..database import top_richest

log  = logging.getLogger("SoulCatcher.richest")
HTML = enums.ParseMode.HTML
_DIV    = "━━━━━━━━━━━━━━━━━━━━"
_MEDALS = ["🥇", "🥈", "🥉"] + ["🏅"] * 7

def _fmt(n) -> str:
    try:    return f"{int(n):,}"
    except: return str(n)

def _esc(t: str) -> str:
    return str(t).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")


@app.on_message(filters.command("richest"))
async def cmd_richest(_, message: Message):
    wait = await message.reply_text("⏳ <i>Loading richest players…</i>", parse_mode=HTML)
    try:
        results = await top_richest(10)
        results = [r for r in results if r.get("balance", 0) > 0]
        if not results:
            return await wait.edit_text("📊 <i>No wealthy players yet.</i>", parse_mode=HTML)

        lines = [f"💰 <b>TOP 10 RICHEST PLAYERS</b>\n<code>{_DIV}</code>\n"]
        for i, r in enumerate(results):
            uid   = r.get("user_id", 0)
            name  = _esc(r.get("first_name") or r.get("username") or f"User {uid}")
            bal   = r.get("balance", 0)
            medal = _MEDALS[i] if i < len(_MEDALS) else f"{i+1}."
            if i == 0:
                lines.append(
                    f"{medal} <a href=\"tg://user?id={uid}\"><b>{name}</b></a>\n"
                    f"  🌸 <b><code>{_fmt(bal)}</code></b> kakera"
                )
            else:
                lines.append(
                    f"{medal} <a href=\"tg://user?id={uid}\">{name}</a>  "
                    f"<code>{_fmt(bal)}</code> 🌸"
                )

        lines.append(f"\n<code>{_DIV}</code>")
        await wait.edit_text("\n".join(lines), parse_mode=HTML, disable_web_page_preview=True)

    except Exception as e:
        log.error("/richest error: %s", e)
        await wait.edit_text("❌ <b>Failed to load leaderboard.</b>", parse_mode=HTML)
