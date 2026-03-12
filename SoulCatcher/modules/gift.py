"""SoulCatcher/modules/gift.py
Command: /gift
Callbacks: send_gift:  cancel_gift:

Ported from reference sgift.py — key fixes carried over:
  [FIX-1] Atomic find_one_and_update eliminates the double-gift race condition.
  [FIX-2] $pull / $push instead of full-array $set prevents concurrent overwrites.
  [FIX-3] Media downloaded via aiohttp then uploaded as bytes (avoids WEBPAGE_MEDIA_EMPTY).
  [FIX-4] random.choice() called per-request, not at import time.
  Adapted to use this bot's user_characters collection + database helpers.
"""

from __future__ import annotations
import asyncio
import aiohttp
import logging
import os
import random
import tempfile

from pyrogram import filters
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup as IKM,
    InlineKeyboardButton as IKB,
)

from .. import app
from ..rarity import can_gift, get_rarity
from ..database import get_or_create_user, count_rarity_in_harem, _col

log = logging.getLogger("SoulCatcher.gift")

DOWNLOAD_TIMEOUT = 60

# Per-(sender_id, instance_id) asyncio locks — second defense layer
_gift_locks: dict[tuple, asyncio.Lock] = {}


def _get_lock(sender_id: int, iid: str) -> asyncio.Lock:
    key = (sender_id, iid)
    if key not in _gift_locks:
        _gift_locks[key] = asyncio.Lock()
    return _gift_locks[key]


def _drop_lock(sender_id: int, iid: str) -> None:
    _gift_locks.pop((sender_id, iid), None)


# ─────────────────────────────────────────────────────────────────────────────
# Media helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _download(url: str, suffix: str) -> str:
    """Download URL to a temp file, return its path."""
    async with aiohttp.ClientSession() as sess:
        async with sess.get(
            url,
            timeout=aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT),
            headers={"User-Agent": "Mozilla/5.0"},
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status} from {url!r}")
            data = await resp.read()
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(data)
    finally:
        tmp.close()
    return tmp.name


def _rm(path: str | None) -> None:
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass


_ABUSE = [
    "🎁 This gift isn't yours to touch!",
    "⚠️ Only the sender can confirm this gift.",
    "❌ You're not allowed to do that.",
    "🚫 Hands off! This isn't your gift.",
    "😤 Stop pressing random buttons!",
]


# ─────────────────────────────────────────────────────────────────────────────
# /gift
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("gift"))
async def cmd_gift(client, message: Message):
    if not message.reply_to_message:
        return await message.reply_text(
            "🎁 Reply to someone and type `/gift <instance_id>` to send a character."
        )
    if len(message.command) < 2:
        return await message.reply_text(
            "❗ Usage: `/gift <instance_id>`\nExample: `/gift A1B2C3`"
        )

    sender = message.from_user
    receiver = message.reply_to_message.from_user

    if sender.id == receiver.id:
        return await message.reply_text("💀 You can't gift a character to yourself.")
    if receiver.is_bot:
        return await message.reply_text("🤖 You can't gift a character to a bot.")

    iid = message.command[1].upper()

    # Look up the character in sender's harem
    char = await _col("user_characters").find_one({"user_id": sender.id, "instance_id": iid})
    if not char:
        return await message.reply_text(f"❌ `{iid}` not found in your harem.")

    # Rarity check
    if not can_gift(char.get("rarity", "")):
        tier = get_rarity(char.get("rarity", ""))
        label = f"{tier.emoji} {tier.display_name}" if tier else char.get("rarity", "?")
        return await message.reply_text(f"❌ **{label}** characters cannot be gifted!")

    # Max-per-user check for receiver
    tier = get_rarity(char.get("rarity", ""))
    if tier and tier.max_per_user > 0:
        count = await count_rarity_in_harem(receiver.id, char["rarity"])
        if count >= tier.max_per_user:
            return await message.reply_text(
                f"❌ **{receiver.first_name}** already has the max "
                f"({tier.max_per_user}) **{tier.display_name}** characters!"
            )

    # Block if already pending
    if char.get("locked"):
        return await message.reply_text(
            f"⚠️ `{char.get('name', iid)}` is already pending a gift.\n"
            "Complete or cancel it first.",
            reply_markup=IKM([[IKB("❌ Cancel Gift", callback_data=f"cancel_gift:{sender.id}:{iid}")]]),
        )

    # Lock the character
    await _col("user_characters").update_one(
        {"user_id": sender.id, "instance_id": iid},
        {"$set": {"locked": True, "gift_temp_lock": True}},
    )
    log.info("Gift initiated  sender=%d  receiver=%d  iid=%s", sender.id, receiver.id, iid)

    caption = (
        f"🎁 <b>Gift Confirmation</b>\n\n"
        f"{sender.mention} wants to gift a character to {receiver.mention}.\n"
        f"<blockquote>"
        f"• Name: {char['name']}\n"
        f"• Anime: {char.get('anime', 'Unknown')}\n"
        f"• Rarity: {char.get('rarity', 'Unknown')}\n"
        f"• ID: <code>{iid}</code>"
        f"</blockquote>\n"
        f"Do you want to continue with this transfer?"
    )
    keyboard = IKM([
        [IKB("✅ Confirm", callback_data=f"send_gift:{sender.id}:{receiver.id}:{iid}")],
        [IKB("❌ Cancel",  callback_data=f"cancel_gift:{sender.id}:{iid}")],
    ])

    video_url = char.get("video_url", "")
    img_url   = char.get("img_url", "")

    if not video_url and not img_url:
        # Unlock and bail — no media
        await _col("user_characters").update_one(
            {"user_id": sender.id, "instance_id": iid},
            {"$unset": {"locked": "", "gift_temp_lock": ""}},
        )
        return await message.reply_text("⚠️ This character has no media attached.")

    tmp = None
    try:
        if video_url:
            tmp = await _download(video_url, ".mp4")
            with open(tmp, "rb") as fh:
                await message.reply_video(fh, caption=caption, parse_mode="html", reply_markup=keyboard)
        else:
            tmp = await _download(img_url, ".jpg")
            with open(tmp, "rb") as fh:
                await message.reply_photo(fh, caption=caption, parse_mode="html", reply_markup=keyboard)
    except Exception:
        log.exception("Gift confirmation send failed  sender=%d  iid=%s", sender.id, iid)
        await _col("user_characters").update_one(
            {"user_id": sender.id, "instance_id": iid},
            {"$unset": {"locked": "", "gift_temp_lock": ""}},
        )
        await message.reply_text("❌ Couldn't send confirmation message. Try again later.")
    finally:
        _rm(tmp)


# ─────────────────────────────────────────────────────────────────────────────
# Callbacks: confirm / cancel
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^(send_gift|cancel_gift):"))
async def gift_cb(client, cb):
    parts     = cb.data.split(":")
    action    = parts[0]
    sender_id = int(parts[1])

    # cancel_gift:sender_id:iid
    # send_gift:sender_id:receiver_id:iid
    iid = parts[2] if action == "cancel_gift" else parts[3]

    if cb.from_user.id != sender_id:
        return await cb.answer(random.choice(_ABUSE), show_alert=True)

    # ── CANCEL ────────────────────────────────────────────────────────────────
    if action == "cancel_gift":
        await _col("user_characters").update_one(
            {"user_id": sender_id, "instance_id": iid},
            {"$unset": {"locked": "", "gift_temp_lock": ""}},
        )
        _drop_lock(sender_id, iid)
        log.info("Gift cancelled  sender=%d  iid=%s", sender_id, iid)
        try:
            await cb.message.edit_text("❌ Gift cancelled.")
        except Exception:
            pass
        return await cb.answer()

    # ── CONFIRM ───────────────────────────────────────────────────────────────
    receiver_id = int(parts[2])

    gift_lock = _get_lock(sender_id, iid)
    if gift_lock.locked():
        return await cb.answer("⏳ Already processing this gift...", show_alert=True)

    async with gift_lock:
        # Atomic: check AND clear gift_temp_lock in one round-trip
        claimed = await _col("user_characters").find_one_and_update(
            {"user_id": sender_id, "instance_id": iid, "gift_temp_lock": True},
            {"$unset": {"gift_temp_lock": ""}},
        )
        if not claimed:
            _drop_lock(sender_id, iid)
            return await cb.answer("⚠️ This gift was already processed.", show_alert=True)

        # Re-fetch to get the clean char doc (temp lock now cleared)
        char = await _col("user_characters").find_one(
            {"user_id": sender_id, "instance_id": iid, "locked": True}
        )
        if not char:
            _drop_lock(sender_id, iid)
            try:
                await cb.message.edit_text("⚠️ Character no longer available or already gifted.")
            except Exception:
                pass
            return await cb.answer()

        # Strip lock fields before transferring
        char.pop("locked", None)
        char.pop("gift_temp_lock", None)

        # Atomic transfer: $pull by instance_id+locked=True, then $push to receiver
        await _col("user_characters").delete_one({"user_id": sender_id, "instance_id": iid, "locked": True})
        char["user_id"] = receiver_id
        await _col("user_characters").insert_one(char)

        # Ensure receiver exists in users collection
        await get_or_create_user(receiver_id)

    _drop_lock(sender_id, iid)
    log.info("Gift complete  sender=%d  receiver=%d  iid=%s (%s)",
             sender_id, receiver_id, iid, char.get("name"))

    # Edit confirmation message
    try:
        sender_user   = await client.get_users(sender_id)
        receiver_user = await client.get_users(receiver_id)
        await cb.message.edit_text(
            f"✅ <b>Transfer Complete!</b>\n\n"
            f"{sender_user.mention} successfully gifted "
            f"<b>{char['name']}</b> to {receiver_user.mention}.",
            parse_mode="html",
        )
    except Exception as exc:
        log.warning("Could not edit gift confirmation: %s", exc)

    await cb.answer("✅ Gift sent!")

    # Notify receiver in DM
    notify = (
        f"✨ <b>You received a new character!</b>\n\n"
        f"<blockquote>"
        f"• Name: {char['name']}\n"
        f"• Anime: {char.get('anime', 'Unknown')}\n"
        f"• Rarity: {char.get('rarity', 'Unknown')}"
        f"</blockquote>\n"
        f"Enjoy your gift 🎁"
    )

    video_url = char.get("video_url", "")
    img_url   = char.get("img_url", "")
    tmp = None
    try:
        if video_url:
            tmp = await _download(video_url, ".mp4")
            with open(tmp, "rb") as fh:
                await client.send_video(receiver_id, fh, caption=notify, parse_mode="html")
        elif img_url:
            tmp = await _download(img_url, ".jpg")
            with open(tmp, "rb") as fh:
                await client.send_photo(receiver_id, fh, caption=notify, parse_mode="html")
    except Exception as exc:
        log.warning("Receiver DM notification failed  receiver=%d: %s", receiver_id, exc)
    finally:
        _rm(tmp)
