"""SoulCatcher/modules/wishlist.py — /wish, /wishlist, /unwish."""
from __future__ import annotations

import SoulCatcher as _soul
from pyrogram import filters
from pyrogram.types import Message

from SoulCatcher.database import (
    add_to_wishlist,
    remove_from_wishlist,
    get_wishlist,
    is_in_wishlist,
    get_character,
)
from SoulCatcher.rarity import rarity_display


@_soul.app.on_message(filters.command(["wish", "addwish"]))
async def wish_cmd(_, m: Message):
    parts = m.text.split()
    if len(parts) < 2:
        await m.reply("Usage: `/wish <charID>`")
        return

    char_id = parts[1].zfill(4)
    uid     = m.from_user.id
    char    = await get_character(char_id)

    if not char:
        await m.reply(f"❌ No character with ID `{char_id}`.")
        return

    if await is_in_wishlist(uid, char_id):
        await m.reply(f"⭐ **{char['name']}** is already in your wishlist!")
        return

    success = await add_to_wishlist(uid, char_id)
    if success:
        r_str = rarity_display(char["rarity"])
        await m.reply(f"⭐ Added **{char['name']}** ({r_str}) to your wishlist!")
    else:
        await m.reply("❌ Wishlist is full (max 25). Use `/unwish <charID>` to remove one.")


@_soul.app.on_message(filters.command(["unwish", "removewish"]))
async def unwish_cmd(_, m: Message):
    parts = m.text.split()
    if len(parts) < 2:
        await m.reply("Usage: `/unwish <charID>`")
        return

    char_id = parts[1].zfill(4)
    uid     = m.from_user.id
    removed = await remove_from_wishlist(uid, char_id)

    if removed:
        char = await get_character(char_id)
        name = char["name"] if char else char_id
        await m.reply(f"🗑 Removed **{name}** from your wishlist.")
    else:
        await m.reply(f"❌ `{char_id}` is not in your wishlist.")


@_soul.app.on_message(filters.command(["wishlist", "wl", "wishes"]))
async def wishlist_cmd(_, m: Message):
    target   = m.reply_to_message.from_user if m.reply_to_message else m.from_user
    char_ids = await get_wishlist(target.id)

    if not char_ids:
        await m.reply(f"📭 **{target.first_name}** has no wishlisted characters.")
        return

    lines = []
    for cid in char_ids:
        char = await get_character(cid)
        if char:
            r_str = rarity_display(char["rarity"])
            lines.append(f"🆔 `{cid}` — **{char['name']}** | {r_str}")
        else:
            lines.append(f"🆔 `{cid}` — *Character removed*")

    await m.reply(
        f"⭐ **{target.first_name}'s Wishlist** ({len(char_ids)}/25)\n\n"
        + "\n".join(lines)
    )
