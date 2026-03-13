"""SoulCatcher/modules/rarityinfo.py

Command:
  /rarityinfo          —  full rarity table for all tiers
  /rarityinfo <name>   —  detailed card for a single rarity (e.g. /rarityinfo mythic)
"""
from __future__ import annotations

import logging

from pyrogram import filters
from pyrogram.types import Message

from .. import app
from ..rarity import RARITIES, get_rarity_card
from .profile_helpers import HTML, MD, DIV, esc

log = logging.getLogger("SoulCatcher.rarityinfo")


@app.on_message(filters.command("rarityinfo"))
async def cmd_rarityinfo(_, message: Message):
    try:
        args = message.command

        if len(args) > 1:
            # get_rarity_card returns Markdown — send with MD parse mode
            card = get_rarity_card(args[1].lower())
            return await message.reply_text(card, parse_mode=MD)

        # Full table — build in HTML
        lines = [f"🌸 <b>SOULCATCHER RARITY TABLE</b>\n<code>{DIV}</code>\n"]

        for r in RARITIES.values():
            subs = "  ".join(
                f"{s.emoji} <code>{esc(s.display_name)}</code>"
                for s in r.sub_rarities
            )
            lines.append(
                f"{r.emoji} <b>{esc(r.display_name)}</b>  <i>(Tier {r.id})</i>\n"
                f"  Weight <code>{r.weight}</code>  "
                f"Kakera <code>{r.kakera_reward}</code>  "
                f"Claim <code>{r.claim_window_seconds}s</code>"
                + (f"\n  └ {subs}" if subs else "")
            )

        lines.append(f"\n<code>{DIV}</code>")
        await message.reply_text("\n\n".join(lines), parse_mode=HTML)

    except Exception as e:
        log.error("/rarityinfo error: %s", e)
        await message.reply_text("❌ <b>Failed to load rarity info.</b>", parse_mode=HTML)
