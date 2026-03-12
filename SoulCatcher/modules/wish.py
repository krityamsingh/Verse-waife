"""SoulCatcher/modules/wish.py
Commands: /wish  /wishlist  /unwish

Split from collection.py.
"""

from __future__ import annotations
import logging

from pyrogram import filters
from pyrogram.types import Message

from .. import app
from ..rarity import get_rarity
from ..database import get_character, add_wish, remove_wish, get_wishlist

log = logging.getLogger("SoulCatcher.wish")


@app.on_message(filters.command("wish"))
async def cmd_wish(_, message: Message):
    args = message.command
    if len(args) < 2:
        return await message.reply_text(
            "Usage: `/wish <char_id>`  (global character ID, not instance ID)"
        )
    char = await get_character(args[1])
    if not char:
        return await message.reply_text(f"❌ Character `{args[1]}` not found in database.")

    added = await add_wish(
        message.from_user.id, args[1],
        char.get("name", "?"), char.get("rarity", "common"),
    )
    if added:
        tier       = get_rarity(char.get("rarity", ""))
        rarity_str = f"{tier.emoji} {tier.display_name}" if tier else char.get("rarity", "?")
        await message.reply_text(f"💛 **{char['name']}** added to your wishlist!\n{rarity_str}")
    else:
        await message.reply_text("❌ Already on wishlist, or wishlist is full (max 25).")


@app.on_message(filters.command("wishlist"))
async def cmd_wishlist(_, message: Message):
    items = await get_wishlist(message.from_user.id)
    if not items:
        return await message.reply_text("💛 Your wishlist is empty! Use `/wish <char_id>` to add.")

    lines = ["💛 **Your Wishlist**\n"]
    for i, item in enumerate(items, 1):
        tier  = get_rarity(item.get("rarity", ""))
        emoji = tier.emoji if tier else "❓"
        lines.append(f"`{i}.` {emoji} **{item['char_name']}** `{item['char_id']}`")
    lines.append(f"\n`{len(items)}/25` slots used")
    await message.reply_text("\n".join(lines))


@app.on_message(filters.command("unwish"))
async def cmd_unwish(_, message: Message):
    args = message.command
    if len(args) < 2:
        return await message.reply_text("Usage: `/unwish <char_id>`")
    removed = await remove_wish(message.from_user.id, args[1])
    if removed:
        await message.reply_text(f"💛 `{args[1]}` removed from your wishlist.")
    else:
        await message.reply_text("❌ Character not found in your wishlist.")
