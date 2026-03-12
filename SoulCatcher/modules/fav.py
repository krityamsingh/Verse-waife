"""SoulCatcher/modules/fav.py
Commands: /setfav  /view

Split from collection.py.
"""

from __future__ import annotations
import logging

from pyrogram import filters
from pyrogram.types import Message

from .. import app
from ..rarity import get_rarity
from ..database import get_harem_char, _col

log = logging.getLogger("SoulCatcher.fav")


# ─────────────────────────────────────────────────────────────────────────────
# /setfav — toggle a character as favourite
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("setfav"))
async def cmd_setfav(_, message: Message):
    args = message.command
    if len(args) < 2:
        return await message.reply_text("Usage: `/setfav <instance_id>`")

    uid  = message.from_user.id
    iid  = args[1].upper()
    char = await get_harem_char(uid, iid)
    if not char:
        return await message.reply_text("❌ Character not found in your harem.")

    new_val = not char.get("is_favorite", False)
    await _col("user_characters").update_one(
        {"user_id": uid, "instance_id": iid},
        {"$set": {"is_favorite": new_val}},
    )

    status = "⭐ marked as favourite" if new_val else "☆ removed from favourites"
    await message.reply_text(f"**{char['name']}** has been {status}!")


# ─────────────────────────────────────────────────────────────────────────────
# /view — inspect a single character from your harem
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("view"))
async def cmd_view(_, message: Message):
    args = message.command
    if len(args) < 2:
        return await message.reply_text("Usage: `/view <instance_id>`")

    uid  = message.from_user.id
    iid  = args[1].upper()
    char = await get_harem_char(uid, iid)
    if not char:
        return await message.reply_text("❌ Character not found in your harem.")

    tier       = get_rarity(char.get("rarity", ""))
    rarity_str = f"{tier.emoji} {tier.display_name}" if tier else char.get("rarity", "?")
    fav_line   = "⭐ **Favourite**\n" if char.get("is_favorite") else ""
    note_line  = f"\n📝 Note: _{char['note']}_" if char.get("note") else ""

    text = (
        f"{fav_line}"
        f"**{char['name']}** (`{iid}`)\n"
        f"📖 _{char.get('anime', 'Unknown')}_\n"
        f"{rarity_str}\n"
        f"🕒 Obtained: `{str(char.get('obtained_at', '?'))[:10]}`"
        f"{note_line}"
    )

    video_url = char.get("video_url", "")
    img_url   = char.get("img_url", "")
    try:
        if video_url:
            await message.reply_video(video_url, caption=text)
        elif img_url:
            await message.reply_photo(img_url, caption=text)
        else:
            await message.reply_text(text)
    except Exception:
        await message.reply_text(text)
