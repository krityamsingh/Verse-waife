"""SoulCatcher/modules/event.py

Command:
  /event  —  shows the active game mode and its multipliers.
"""
from __future__ import annotations
import logging
from pyrogram import filters, enums
from pyrogram.types import Message
from .. import app

log  = logging.getLogger("SoulCatcher.event")
HTML = enums.ParseMode.HTML
_SDIV = "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄"

def _esc(t: str) -> str:
    return str(t).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")


@app.on_message(filters.command("event"))
async def cmd_event(_, message: Message):
    try:
        import SoulCatcher.rarity as _mod
        mode = _mod.GAME_MODES.get(_mod.CURRENT_MODE, _mod.GAME_MODES["normal"])
        await message.reply_text(
            f"🎮 <b>CURRENT GAME MODE</b>\n"
            f"<code>{_SDIV}</code>\n"
            f"✦ <b>{_esc(mode['label'])}</b>\n"
            f"<code>{_SDIV}</code>\n"
            f"⚡ Spawn weight   <code>{mode['weight_mult']}×</code>\n"
            f"🌸 Kakera reward  <code>{mode['kakera_mult']}×</code>\n"
            f"<code>{_SDIV}</code>",
            parse_mode=HTML,
        )
    except Exception as e:
        log.error("/event error: %s", e)
        await message.reply_text("❌ <b>Failed to load event info.</b>", parse_mode=HTML)
