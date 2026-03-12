"""SoulCatcher/modules/burn.py
Command: /burn
Callbacks: burn:

Split from collection.py.
"""

from __future__ import annotations
import logging

from pyrogram import filters
from pyrogram.types import Message, InlineKeyboardMarkup as IKM, InlineKeyboardButton as IKB

from .. import app
from ..rarity import get_rarity, get_sell_price
from ..database import get_harem_char, remove_from_harem, add_balance

log = logging.getLogger("SoulCatcher.burn")


def _fmt(n) -> str:
    try:
        return f"{int(n):,}"
    except Exception:
        return str(n)


# ─────────────────────────────────────────────────────────────────────────────
# /burn
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("burn"))
async def cmd_burn(_, message: Message):
    args = message.command
    if len(args) < 2:
        return await message.reply_text("Usage: `/burn <instance_id>`")

    uid  = message.from_user.id
    iid  = args[1].upper()
    char = await get_harem_char(uid, iid)
    if not char:
        return await message.reply_text("❌ Character not found in your harem.")

    if char.get("is_favorite"):
        return await message.reply_text(
            "⭐ This character is a favourite! Use `/setfav` to unmark it first."
        )

    price      = get_sell_price(char.get("rarity", "common"))
    tier       = get_rarity(char.get("rarity", ""))
    rarity_str = f"{tier.emoji} {tier.display_name}" if tier else char.get("rarity", "?")

    kb = IKM([[
        IKB("🔥 Burn!", callback_data=f"burn:{uid}:{iid}:{price}"),
        IKB("❌ Cancel", callback_data=f"burn:cancel:{uid}"),
    ]])

    text = (
        f"🔥 **Burn Confirm**\n\n"
        f"**{char['name']}** (`{iid}`)\n"
        f"{rarity_str}\n\n"
        f"You'll receive **{_fmt(price)} kakera**. Continue?"
    )
    try:
        vid = char.get("video_url", "")
        img = char.get("img_url", "")
        if vid:
            await message.reply_video(vid, caption=text, reply_markup=kb)
        elif img:
            await message.reply_photo(img, caption=text, reply_markup=kb)
        else:
            await message.reply_text(text, reply_markup=kb)
    except Exception:
        await message.reply_text(text, reply_markup=kb)


# ─────────────────────────────────────────────────────────────────────────────
# Callback
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^burn:"))
async def burn_cb(_, cb):
    parts = cb.data.split(":")

    if parts[1] == "cancel":
        uid = int(parts[2]) if len(parts) > 2 else cb.from_user.id
        if cb.from_user.id != uid:
            return await cb.answer("Not your burn!", show_alert=True)
        try:
            await cb.message.edit_text("❌ Burn cancelled.")
        except Exception:
            pass
        return await cb.answer()

    uid, iid, price = int(parts[1]), parts[2], int(parts[3])

    if cb.from_user.id != uid:
        return await cb.answer("Not your character!", show_alert=True)

    char = await get_harem_char(uid, iid)
    if not char:
        try:
            await cb.message.edit_text("❌ Character already gone.")
        except Exception:
            pass
        return await cb.answer()

    removed = await remove_from_harem(uid, iid)
    if removed:
        await add_balance(uid, price)
        log.info("BURN: uid=%d burned iid=%s for %d kakera", uid, iid, price)
        try:
            await cb.message.edit_text(
                f"🔥 **{char['name']}** burned for **{_fmt(price)} kakera**!"
            )
        except Exception:
            pass
    else:
        try:
            await cb.message.edit_text("❌ Burn failed — character may have moved.")
        except Exception:
            pass

    await cb.answer()
