"""SoulCatcher/modules/gift.py
Command : /gift <instance_id>   (reply to the target user)
Callbacks: send_gift:  cancel_gift:

How it works
────────────
1. Sender replies to receiver and runs /gift <id>
2. Bot fetches the sender's FULL harem, finds the character by:
      - instance_id  (the 8-char UUID slug shown in /harem)
      - char_id      (zero-padded 4-digit fallback for legacy docs)
3. Shows a confirmation card with the character media.
4. On ✅ Confirm  — atomic lock-and-transfer:
      • delete from sender's user_characters
      • insert fresh doc for receiver (resets personal fields + obtained_at)
      • increment receiver's total_claimed counter
5. On ❌ Cancel   — unlock the character.

Rarity rules (from rarity.py gift_allowed field):
  gift_allowed=False  → mythic, limited_edition, sports, fantasy,
                        eternal, cartoon  — CANNOT be gifted
  max_per_user > 0    → checked against receiver's current count
"""

from __future__ import annotations

import asyncio
import aiohttp
import logging
import os
import random
import tempfile
from datetime import datetime

from pyrogram import filters, enums
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

# ── Per-(sender_id, instance_id) asyncio locks ──────────────────────────────
_gift_locks: dict[tuple, asyncio.Lock] = {}

def _get_lock(sender_id: int, iid: str) -> asyncio.Lock:
    key = (sender_id, iid)
    if key not in _gift_locks:
        _gift_locks[key] = asyncio.Lock()
    return _gift_locks[key]

def _drop_lock(sender_id: int, iid: str) -> None:
    _gift_locks.pop((sender_id, iid), None)


# ── Media helpers ────────────────────────────────────────────────────────────

async def _download(url: str, suffix: str) -> str:
    """Download a URL to a temp file. Returns file path."""
    async with aiohttp.ClientSession() as sess:
        async with sess.get(
            url,
            timeout=aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT),
            headers={"User-Agent": "Mozilla/5.0"},
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status} fetching {url!r}")
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


# ── Harem lookup ─────────────────────────────────────────────────────────────

async def _find_in_harem(user_id: int, raw_id: str) -> dict | None:
    """
    Fetch the sender's complete harem and find the character by:
      1. instance_id  — 8-char slug (e.g. A1B2C3D4), case-insensitive
      2. char_id      — catalogue ID zero-padded to 4 digits (e.g. 0002)
    Returns the user_characters document, or None if not found.
    """
    iid_upper  = raw_id.strip().upper()
    padded_cid = raw_id.strip().zfill(4)

    all_chars = await _col("user_characters").find(
        {"user_id": user_id}
    ).to_list(None)

    if not all_chars:
        return None

    # Priority 1 — exact instance_id match
    for c in all_chars:
        if (c.get("instance_id") or "").upper() == iid_upper:
            return c

    # Priority 2 — char_id match (legacy/seeded docs without instance_id)
    for c in all_chars:
        if (c.get("char_id") or "") == padded_cid:
            return c

    return None


# ── Abuse-click messages ─────────────────────────────────────────────────────
_ABUSE = [
    "🎁 This gift isn't yours to touch!",
    "⚠️ Only the sender can confirm this gift.",
    "❌ You're not allowed to do that.",
    "🚫 Hands off! This isn't your gift.",
    "😤 Stop pressing random buttons!",
]


# ─────────────────────────────────────────────────────────────────────────────
# /gift  command
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("gift"))
async def cmd_gift(client, message: Message):

    # ── Basic usage validation ────────────────────────────────────────────────
    if not message.reply_to_message:
        return await message.reply_text(
            "🎁 **How to gift a character:**\n\n"
            "1. Open your /harem and copy the character ID\n"
            "2. Reply to the person you want to gift\n"
            "3. Send: `/gift <instance_id>`\n\n"
            "Example: `/gift A1B2C3D4`"
        )
    if len(message.command) < 2:
        return await message.reply_text(
            "❗ **Usage:** `/gift <instance_id>`\n"
            "Example: `/gift A1B2C3D4`\n\n"
            "Use /harem to find your character IDs."
        )

    sender   = message.from_user
    receiver = message.reply_to_message.from_user

    if sender.id == receiver.id:
        return await message.reply_text("💀 You can't gift a character to yourself.")
    if receiver.is_bot:
        return await message.reply_text("🤖 You can't gift a character to a bot.")

    raw_id = message.command[1].strip()

    # ── Ensure sender has a user document ────────────────────────────────────
    await get_or_create_user(
        sender.id,
        sender.username or "",
        sender.first_name or "",
        getattr(sender, "last_name", "") or "",
    )

    # ── Search sender's full harem ────────────────────────────────────────────
    loading = await message.reply_text("🔍 Searching your harem...")
    char = await _find_in_harem(sender.id, raw_id)

    if not char:
        await loading.delete()
        return await message.reply_text(
            f"❌ `{raw_id}` not found in your harem.\n\n"
            "• Use /harem to see your characters and their IDs\n"
            "• IDs are case-insensitive — `a1b2c3d4` = `A1B2C3D4`\n"
            "• If your harem shows a number like `0002`, use that directly"
        )

    # Resolve the real instance_id to use everywhere from here
    iid = char.get("instance_id") or raw_id.strip().upper()

    # ── Rarity: gift_allowed check ────────────────────────────────────────────
    rarity_name = char.get("rarity", "")
    tier = get_rarity(rarity_name)
    if not can_gift(rarity_name):
        label = f"{tier.emoji} {tier.display_name}" if tier else rarity_name or "Unknown"
        await loading.delete()
        return await message.reply_text(
            f"❌ **{label}** characters cannot be gifted!\n\n"
            "Giftable rarities: ⚫ Common, 🔵 Rare, 🌌 Legendry, "
            "🔥 Elite, 💎 Seasonal, 🌸 Festival"
        )

    # ── Rarity: max_per_user check on receiver ────────────────────────────────
    if tier and tier.max_per_user > 0:
        receiver_count = await count_rarity_in_harem(receiver.id, rarity_name)
        if receiver_count >= tier.max_per_user:
            rarity_label = f"{tier.emoji} {tier.display_name}"
            await loading.delete()
            return await message.reply_text(
                f"❌ **{receiver.first_name}** already has the maximum "
                f"(**{tier.max_per_user}**) {rarity_label} characters!"
            )

    # ── Already locked / pending gift? ───────────────────────────────────────
    if char.get("locked"):
        await loading.delete()
        return await message.reply_text(
            f"⚠️ **{char.get('name', iid)}** is already pending a gift.\n"
            "Complete or cancel the existing gift first.",
            reply_markup=IKM([[
                IKB("❌ Cancel Pending Gift", callback_data=f"cancel_gift:{sender.id}:{iid}")
            ]]),
        )

    # ── Lock the character atomically ─────────────────────────────────────────
    lock_result = await _col("user_characters").update_one(
        {"user_id": sender.id, "instance_id": iid, "locked": {"$ne": True}},
        {"$set": {"locked": True, "gift_temp_lock": True}},
    )
    if lock_result.modified_count == 0:
        await loading.delete()
        return await message.reply_text(
            "⚠️ Could not lock this character — it may have been modified. Please try again."
        )

    log.info("Gift initiated  sender=%d  receiver=%d  iid=%s  char=%s",
             sender.id, receiver.id, iid, char.get("name"))

    # ── Build confirmation card ───────────────────────────────────────────────
    rarity_display = f"{tier.emoji} {tier.display_name}" if tier else rarity_name or "Unknown"
    caption = (
        f"🎁 <b>Gift Confirmation</b>\n\n"
        f"<b>{sender.first_name}</b> ➜ <b>{receiver.first_name}</b>\n\n"
        f"<blockquote>"
        f"🏷 <b>Name:</b> {char['name']}\n"
        f"📖 <b>Anime:</b> {char.get('anime', 'Unknown')}\n"
        f"⭐ <b>Rarity:</b> {rarity_display}\n"
        f"🆔 <b>Instance:</b> <code>{iid}</code>"
        f"</blockquote>\n\n"
        f"Confirm this transfer?"
    )
    keyboard = IKM([
        [IKB("✅ Confirm Gift", callback_data=f"send_gift:{sender.id}:{receiver.id}:{iid}")],
        [IKB("❌ Cancel",       callback_data=f"cancel_gift:{sender.id}:{iid}")],
    ])

    # ── Send confirmation with media (or text fallback) ───────────────────────
    video_url = char.get("video_url") or ""
    img_url   = char.get("img_url") or ""

    await loading.delete()
    tmp = None
    try:
        if video_url:
            tmp = await _download(video_url, ".mp4")
            with open(tmp, "rb") as fh:
                await message.reply_video(fh, caption=caption, parse_mode=enums.ParseMode.HTML, reply_markup=keyboard)
        elif img_url:
            tmp = await _download(img_url, ".jpg")
            with open(tmp, "rb") as fh:
                await message.reply_photo(fh, caption=caption, parse_mode=enums.ParseMode.HTML, reply_markup=keyboard)
        else:
            # No media — text confirmation is fine
            await message.reply_text(caption, parse_mode=enums.ParseMode.HTML, reply_markup=keyboard)
    except Exception:
        log.exception("Gift confirmation send failed  sender=%d  iid=%s", sender.id, iid)
        # Always unlock on failure so the character isn't stuck
        await _col("user_characters").update_one(
            {"user_id": sender.id, "instance_id": iid},
            {"$unset": {"locked": "", "gift_temp_lock": ""}},
        )
        await message.reply_text(
            "❌ Couldn't send the confirmation card. Character unlocked. Try again."
        )
    finally:
        _rm(tmp)


# ─────────────────────────────────────────────────────────────────────────────
# Callbacks: ✅ Confirm  /  ❌ Cancel
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^(send_gift|cancel_gift):"))
async def gift_cb(client, cb):
    parts     = cb.data.split(":")
    action    = parts[0]
    sender_id = int(parts[1])

    # cancel_gift : sender_id : iid
    # send_gift   : sender_id : receiver_id : iid
    iid = parts[2] if action == "cancel_gift" else parts[3]

    # Only the original sender may press these buttons
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
            await cb.message.edit_text("❌ Gift cancelled. Character is back in your harem.")
        except Exception:
            pass
        return await cb.answer("Cancelled.")

    # ── CONFIRM ───────────────────────────────────────────────────────────────
    receiver_id = int(parts[2])

    gift_lock = _get_lock(sender_id, iid)
    if gift_lock.locked():
        return await cb.answer("⏳ Already processing — please wait...", show_alert=True)

    async with gift_lock:
        # Atomic double-tap guard: grab AND clear gift_temp_lock in one op.
        # Returns None if already confirmed (second press / race condition).
        claimed = await _col("user_characters").find_one_and_update(
            {"user_id": sender_id, "instance_id": iid, "gift_temp_lock": True},
            {"$unset": {"gift_temp_lock": ""}},
        )
        if not claimed:
            _drop_lock(sender_id, iid)
            return await cb.answer("⚠️ Already processed — check your harem.", show_alert=True)

        # Re-fetch clean doc (gift_temp_lock cleared, locked still True)
        char = await _col("user_characters").find_one(
            {"user_id": sender_id, "instance_id": iid, "locked": True}
        )
        if not char:
            _drop_lock(sender_id, iid)
            try:
                await cb.message.edit_text("⚠️ Character no longer available.")
            except Exception:
                pass
            return await cb.answer()

        # Ensure receiver user doc exists BEFORE the transfer
        await get_or_create_user(receiver_id)

        # ── Build receiver's new doc ──────────────────────────────────────────
        # Explicitly copy only the fields defined in database.py add_to_harem()
        # so no lock fields, no _id, and no sender-personal data leaks through.
        new_doc = {
            "instance_id": char["instance_id"],
            "user_id":     receiver_id,
            "char_id":     char.get("char_id", ""),
            "name":        char.get("name", "Unknown"),
            "anime":       char.get("anime", "Unknown"),
            "rarity":      char.get("rarity", "common"),
            "img_url":     char.get("img_url", ""),
            "video_url":   char.get("video_url", ""),
            "is_favorite": False,             # reset — receiver starts fresh
            "note":        "",                # reset — don't carry sender's notes
            "obtained_at": datetime.utcnow(), # receiver's own timestamp
        }

        # ── Atomic transfer ───────────────────────────────────────────────────
        await _col("user_characters").delete_one(
            {"user_id": sender_id, "instance_id": iid}
        )
        await _col("user_characters").insert_one(new_doc)

        # Increment receiver's total_claimed (skipped add_to_harem so do it here)
        await _col("users").update_one(
            {"user_id": receiver_id},
            {"$inc": {"total_claimed": 1}},
            upsert=True,
        )

    _drop_lock(sender_id, iid)
    log.info("Gift complete  sender=%d  receiver=%d  iid=%s  char=%s",
             sender_id, receiver_id, iid, new_doc.get("name"))

    # ── Edit confirmation message ─────────────────────────────────────────────
    tier           = get_rarity(new_doc["rarity"])
    rarity_display = f"{tier.emoji} {tier.display_name}" if tier else new_doc["rarity"]
    try:
        sender_user   = await client.get_users(sender_id)
        receiver_user = await client.get_users(receiver_id)
        await cb.message.edit_text(
            f"✅ <b>Gift Complete!</b>\n\n"
            f"{sender_user.mention} gifted <b>{new_doc['name']}</b> "
            f"to {receiver_user.mention}!\n\n"
            f"<blockquote>"
            f"⭐ {rarity_display}\n"
            f"📖 {new_doc.get('anime', 'Unknown')}\n"
            f"🆔 <code>{iid}</code>"
            f"</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as exc:
        log.warning("Could not edit gift confirmation: %s", exc)

    await cb.answer("✅ Gift sent!")

    # ── DM the receiver ───────────────────────────────────────────────────────
    dm_text = (
        f"🎁 <b>You received a character!</b>\n\n"
        f"<blockquote>"
        f"🏷 <b>Name:</b> {new_doc['name']}\n"
        f"📖 <b>Anime:</b> {new_doc.get('anime', 'Unknown')}\n"
        f"⭐ <b>Rarity:</b> {rarity_display}\n"
        f"🆔 <b>Instance:</b> <code>{iid}</code>"
        f"</blockquote>\n\n"
        f"Use /harem to see your full collection! 🌸"
    )
    video_url = new_doc.get("video_url") or ""
    img_url   = new_doc.get("img_url") or ""
    tmp = None
    try:
        if video_url:
            tmp = await _download(video_url, ".mp4")
            with open(tmp, "rb") as fh:
                await client.send_video(receiver_id, fh, caption=dm_text, parse_mode=enums.ParseMode.HTML)
        elif img_url:
            tmp = await _download(img_url, ".jpg")
            with open(tmp, "rb") as fh:
                await client.send_photo(receiver_id, fh, caption=dm_text, parse_mode=enums.ParseMode.HTML)
        else:
            await client.send_message(receiver_id, dm_text, parse_mode=enums.ParseMode.HTML)
    except Exception as exc:
        log.warning("Receiver DM failed  receiver=%d: %s", receiver_id, exc)
    finally:
        _rm(tmp)
