"""SoulCatcher/modules/event.py

Command:
  /event  —  shows the currently active game mode with its spawn weight
             multiplier and kakera reward multiplier.
"""
from __future__ import annotations

import logging

from pyrogram import filters
from pyrogram.types import Message

from .. import app
from ._profile_helpers import HTML, SDIV, esc

log = logging.getLogger("SoulCatcher.event")


@app.on_message(filters.command("event"))
async def cmd_event(_, message: Message):
    try:
        import SoulCatcher.rarity as _mod
        mode = _mod.GAME_MODES.get(_mod.CURRENT_MODE, _mod.GAME_MODES["normal"])

        await message.reply_text(
            f"🎮 <b>CURRENT GAME MODE</b>\n"
            f"<code>{SDIV}</code>\n"
            f"✦ <b>{esc(mode['label'])}</b>\n"
            f"<code>{SDIV}</code>\n"
            f"⚡ Spawn weight   <code>{mode['weight_mult']}×</code>\n"
            f"🌸 Kakera reward  <code>{mode['kakera_mult']}×</code>\n"
            f"<code>{SDIV}</code>",
            parse_mode=HTML,
        )

    except Exception as e:
        log.error("/event error: %s", e)
        await message.reply_text("❌ <b>Failed to load event info.</b>", parse_mode=HTML)
