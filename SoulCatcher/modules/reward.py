"""
SoulCatcher/modules/reward.py
════════════════════════════════════════════════════════════════════════════════
/reward       — One-time Verse character reward (any user, in GC)
/resetreward  — Owner only: reset a user's reward claim
════════════════════════════════════════════════════════════════════════════════
"""

import logging
from datetime import datetime

from pyrogram import filters
from pyrogram.types import Message

from .. import app
from ..database import (
    get_or_create_user,
    get_user,
    update_user,
    get_random_character,
    add_to_harem,
    reset_reward_claim,
)
from ..rarity import get_rarity

log = logging.getLogger("SoulCatcher.reward")

_VERSE_RARITY = "verse"
OWNER_ID = 6118760915


# ─────────────────────────────────────────────────────────────────────────────
# /reward
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("reward"))
async def cmd_reward(_, message: Message):
    uid = message.from_user.id

    await get_or_create_user(
        uid,
        message.from_user.username   or "",
        message.from_user.first_name or "",
        message.from_user.last_name  or "",
    )

    # Always fetch fresh from DB so resets are reflected instantly
    user = await get_user(uid)

    if user.get("is_banned"):
        return await message.reply_text("🚫 You are globally banned.")

    # One-time lifetime lock
    if user.get("reward_claimed"):
        return await message.reply_text(
            "❌ **Already Claimed!**\n\n"
            "You have already received your Verse reward.\n"
            "Each account can only claim this **once**."
        )

    # Fetch a Verse character that actually has a video_url — retry up to 10 times
    verse_char = None
    for _ in range(10):
        candidate = await get_random_character(_VERSE_RARITY)
        if not candidate:
            break
        if candidate.get("video_url"):
            verse_char = candidate
            break

    if not verse_char:
        return await message.reply_text(
            "⚠️ No Verse characters with a video are available yet.\n"
            "Ask an admin to add `verse` rarity characters with a `video_url`!"
        )

    r            = get_rarity(_VERSE_RARITY)
    emoji        = r.emoji        if r else "🎠"
    display      = r.display_name if r else "Verse"
    user_mention = (
        f"@{message.from_user.username}"
        if message.from_user.username
        else message.from_user.first_name or "Someone"
    )

    # Use 'cartoon' field from MongoDB (falls back to 'Unknown' if missing)
    cartoon = verse_char.get("cartoon", "Unknown")

    text = (
        f"🎠 **VERSE REWARD CLAIMED!**\n\n"
        f"👤 {user_mention} just claimed their Verse reward!\n\n"
        f"✨ **{verse_char['name']}**\n"
        f"📖 *{cartoon}*\n"
        f"{emoji} **{display}**\n"
    )

    # Attempt delivery FIRST — only lock the reward if delivery succeeds
    delivered = False
    try:
        await message.reply_video(video=verse_char["video_url"], caption=text)
        delivered = True
    except Exception as e:
        log.warning(f"REWARD: video send failed uid={uid}: {e}")
        try:
            if verse_char.get("img_url"):
                await message.reply_photo(photo=verse_char["img_url"], caption=text)
                delivered = True
            else:
                await message.reply_text(text)
                delivered = True
        except Exception as e2:
            log.error(f"REWARD: all fallbacks failed uid={uid}: {e2}")

    if not delivered:
        return await message.reply_text(
            "⚠️ Could not send your reward. Please try again — nothing was charged."
        )

    # Delivery confirmed — now add to harem and lock the claim
    instance_id = await add_to_harem(uid, verse_char)
    await update_user(uid, {
        "$set": {
            "reward_claimed":    True,
            "reward_claimed_at": datetime.utcnow(),
        }
    })

    log.info(f"REWARD: uid={uid} char={verse_char['name']!r} cartoon={cartoon!r} instance={instance_id}")


# ─────────────────────────────────────────────────────────────────────────────
# /resetreward  — owner only
# Usage: /resetreward <user_id>  OR  reply to user's message + /resetreward
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("resetreward"))
async def cmd_reset_reward(_, message: Message):
    if message.from_user.id != OWNER_ID:
        return await message.reply_text("🚫 You are not authorized to use this command.")

    target_id = None

    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
    elif len(message.command) > 1:
        try:
            target_id = int(message.command[1])
        except ValueError:
            return await message.reply_text(
                "❌ Invalid user ID.\n"
                "Usage: `/resetreward <user_id>` or reply to a user's message."
            )

    if not target_id:
        return await message.reply_text(
            "❌ Reply to a user or provide a user ID.\n"
            "Usage: `/resetreward <user_id>`"
        )

    user = await get_user(target_id)
    if not user:
        return await message.reply_text(f"❌ User `{target_id}` not found in database.")

    if not user.get("reward_claimed"):
        return await message.reply_text(
            f"ℹ️ User `{target_id}` hasn't claimed their reward yet — nothing to reset."
        )

    await reset_reward_claim(target_id)
    log.info(f"RESETREWARD: owner={message.from_user.id} reset reward for uid={target_id}")
    await message.reply_text(
        f"✅ Done! Verse reward reset for `{target_id}`.\n"
        f"They can now use `/reward` again."
    )
