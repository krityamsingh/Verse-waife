"""SoulCatcher/modules/sell.py
Command: /sell
Callbacks: confirm_sell|  cancel_sell

Ported from reference sell.py and adapted to this bot's database layer.
Uses user_characters collection (instance_id based) instead of embedded array.
"""

from __future__ import annotations
import logging
import random

from pyrogram import enums, filters
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup as IKM,
    InlineKeyboardButton as IKB,
    CallbackQuery,
)

from .. import app
from ..config import LOG_CHANNEL_ID
from ..rarity import get_rarity, get_sell_price, can_trade
from ..database import get_or_create_user, remove_from_harem, add_balance, _col

log = logging.getLogger("SoulCatcher.sell")


def _fmt(n) -> str:
    try:
        return f"{int(n):,}"
    except Exception:
        return str(n)


# ─────────────────────────────────────────────────────────────────────────────
# /sell
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("sell"))
async def cmd_sell(client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            "❗ Usage: `/sell <instance_id>`\nExample: `/sell A1B2C3`"
        )

    user = message.from_user
    uid  = user.id
    iid  = message.command[1].upper()

    await get_or_create_user(uid, user.username or "", user.first_name or "", getattr(user, "last_name", "") or "")

    char = await _col("user_characters").find_one({"user_id": uid, "instance_id": iid})
    if not char:
        return await message.reply_text(f"❌ `{iid}` not found in your harem.", parse_mode=enums.ParseMode.MARKDOWN)

    # Check tradeable / sellable
    if not can_trade(char.get("rarity", "")):
        tier = get_rarity(char.get("rarity", ""))
        label = f"{tier.emoji} {tier.display_name}" if tier else char.get("rarity", "?")
        return await message.reply_text(f"❌ **{label}** characters cannot be sold!")

    # Generate a random sell price based on rarity
    price = get_sell_price(char.get("rarity", "common"))
    tier = get_rarity(char.get("rarity", ""))
    rarity_str = f"{tier.emoji} {tier.display_name}" if tier else char.get("rarity", "?")

    text = (
        f"**ᴛᴀᴋᴇ ᴀ ʟᴏᴏᴋ ᴀᴛ** {char['name']} **ᴄʜᴀʀᴀᴄᴛᴇʀ**!\n\n"
        f"📖 {char.get('anime', 'Unknown')}\n"
        f"**ᴄʜᴀʀᴀᴄᴛᴇʀ ɪᴅ**: `{iid}`\n"
        f"**ʀᴀʀɪᴛʏ**: {rarity_str}\n\n"
        f"⚠️ **ᴀʀᴇ ʏᴏᴜ sᴜʀᴇ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ sᴇʟʟ ᴛʜɪs ᴄʜᴀʀᴀᴄᴛᴇʀ ғᴏʀ "
        f"{_fmt(price)} **ᴋᴀᴋᴇʀᴀ**?"
    )

    keyboard = IKM([[
        IKB("✅ ᴄᴏɴғɪʀᴍ", callback_data=f"confirm_sell|{uid}|{iid}|{price}"),
        IKB("❌ ᴄᴀɴᴄᴇʟ",  callback_data=f"cancel_sell|{uid}"),
    ]])

    video_url = char.get("video_url", "")
    img_url   = char.get("img_url", "")
    try:
        if video_url:
            await message.reply_video(video=video_url, caption=text, reply_markup=keyboard)
        elif img_url:
            await message.reply_photo(photo=img_url, caption=text, reply_markup=keyboard)
        else:
            await message.reply_text(text, reply_markup=keyboard)
    except Exception as exc:
        log.warning("Sell preview failed  uid=%d  iid=%s: %s", uid, iid, exc)
        await message.reply_text(text, reply_markup=keyboard)


# ─────────────────────────────────────────────────────────────────────────────
# Callbacks
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^confirm_sell\|"))
async def confirm_sell_cb(client, cb: CallbackQuery):
    try:
        _, uid_s, iid, price_s = cb.data.split("|")
        uid   = int(uid_s)
        price = int(price_s)
    except ValueError:
        return await cb.answer("Invalid data!", show_alert=True)

    if cb.from_user.id != uid:
        return await cb.answer("❌ This isn't your sale!", show_alert=True)

    # Re-fetch char before removing
    char = await _col("user_characters").find_one({"user_id": uid, "instance_id": iid})
    if not char:
        return await cb.answer("❌ Character no longer in your harem!", show_alert=True)

    # Remove from harem and credit balance
    removed = await remove_from_harem(uid, iid)
    if not removed:
        return await cb.answer("❌ Sell failed — character may have moved.", show_alert=True)

    await add_balance(uid, price)
    log.info("SELL: uid=%d sold iid=%s for %d kakera", uid, iid, price)

    tier = get_rarity(char.get("rarity", ""))
    rarity_str = f"{tier.emoji} {tier.display_name}" if tier else char.get("rarity", "?")

    try:
        await cb.message.edit_caption(
            f"✅ **sᴏʟᴅ** {char['name']} **ғᴏʀ** {_fmt(price)} **ᴋᴀᴋᴇʀᴀ**!"
        )
    except Exception:
        pass

    # DM the seller
    try:
        dm_caption = (
            f"🪙 You sold **{char['name']}**!\n"
            f"📺 {char.get('anime', 'Unknown')}\n"
            f"{rarity_str}\n"
            f"💰 Earned: **{_fmt(price)} kakera**"
        )
        video_url = char.get("video_url", "")
        img_url   = char.get("img_url", "")
        if video_url:
            await client.send_video(uid, video_url, caption=dm_caption)
        elif img_url:
            await client.send_photo(uid, img_url, caption=dm_caption)
        else:
            await client.send_message(uid, dm_caption)
    except Exception:
        pass

    # Log to channel
    if LOG_CHANNEL_ID:
        try:
            await client.send_message(
                LOG_CHANNEL_ID,
                f"🧾 {cb.from_user.mention} sold:\n\n"
                f"✨ **{char['name']}**\n"
                f"📺 {char.get('anime', '?')}\n"
                f"{rarity_str}\n"
                f"💰 Price: {_fmt(price)} kakera",
            )
        except Exception:
            pass

    await cb.answer(f"✅ Sold for {_fmt(price)} kakera!")


@app.on_callback_query(filters.regex(r"^cancel_sell\|"))
async def cancel_sell_cb(client, cb: CallbackQuery):
    try:
        uid = int(cb.data.split("|")[1])
    except (IndexError, ValueError):
        return await cb.answer("Invalid.", show_alert=True)

    if cb.from_user.id != uid:
        return await cb.answer("❌ This isn't your sale!", show_alert=True)

    try:
        await cb.message.edit_caption("❌ Sell cancelled.")
    except Exception:
        try:
            await cb.message.edit_text("❌ Sell cancelled.")
        except Exception:
            pass
    await cb.answer("Cancelled.")
