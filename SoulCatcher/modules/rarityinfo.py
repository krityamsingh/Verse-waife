"""SoulCatcher/modules/rarityinfo.py

Commands:
  /rarityinfo         —  full rarity table
  /rarityinfo <name>  —  detailed card for one rarity (e.g. /rarityinfo mythic)
"""
from __future__ import annotations
import logging
from pyrogram import filters, enums
from pyrogram.types import Message
from .. import app
from ..rarity import RARITIES, get_rarity_card

log  = logging.getLogger("SoulCatcher.rarityinfo")
HTML = enums.ParseMode.HTML
MD   = enums.ParseMode.MARKDOWN
_DIV = "━━━━━━━━━━━━━━━━━━━━"

def _esc(t: str) -> str:
    return str(t).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")


@app.on_message(filters.command("rarityinfo"))
async def cmd_rarityinfo(_, message: Message):
    try:
        args = message.command

        if len(args) > 1:
            card = get_rarity_card(args[1].lower())
            return await message.reply_text(card, parse_mode=MD)

        lines = [f"🌸 <b>SOULCATCHER RARITY TABLE</b>\n<code>{_DIV}</code>\n"]
        for r in RARITIES.values():
            subs = "  ".join(
                f"{s.emoji} <code>{_esc(s.display_name)}</code>"
                for s in r.sub_rarities
            )
            lines.append(
                f"{r.emoji} <b>{_esc(r.display_name)}</b>  <i>(Tier {r.id})</i>\n"
                f"  Weight <code>{r.weight}</code>  "
                f"Kakera <code>{r.kakera_reward}</code>  "
                f"Claim <code>{r.claim_window_seconds}s</code>"
                + (f"\n  └ {subs}" if subs else "")
            )

        lines.append(f"\n<code>{_DIV}</code>")
        await message.reply_text("\n\n".join(lines), parse_mode=HTML)

    except Exception as e:
        log.error("/rarityinfo error: %s", e)
        await message.reply_text("❌ <b>Failed to load rarity info.</b>", parse_mode=HTML)
