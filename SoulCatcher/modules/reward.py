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

from .. import app, owner_filter
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

    # Fetch a Verse character (rarity="verse", video_only, must have video_url)
    verse_char = await get_random_character(_VERSE_RARITY)
    if not verse_char:
        return await message.reply_text(
            "⚠️ No Verse characters in the database yet.\n"
            "Ask an admin to add `verse` rarity characters with a `video_url`!"
        )

    # Add to harem & lock the reward
    instance_id = await add_to_harem(uid, verse_char)
    await update_user(uid, {
        "$set": {
            "reward_claimed":    True,
            "reward_claimed_at": datetime.utcnow(),
        }
    })

    r            = get_rarity(_VERSE_RARITY)
    emoji        = r.emoji        if r else "🎠"
    display      = r.display_name if r else "Verse"
    user_mention = (
        f"@{message.from_user.username}"
        if message.from_user.username
        else message.from_user.first_name or "Someone"
    )

    text = (
        f"🎠 **VERSE REWARD CLAIMED!**\n\n"
        f"👤 {user_mention} just claimed their Verse reward!\n\n"
        f"✨ **{verse_char['name']}**\n"
        f"📖 *{verse_char.get('anime', 'Unknown')}*\n"
        f"{emoji} **{display}**\n"
        f"🆔 Instance: `{instance_id}`"
    )

    try:
        await message.reply_video(video=verse_char["video_url"], caption=text)
    except Exception as e:
        log.warning(f"REWARD: video send failed uid={uid}: {e}")
        try:
            if verse_char.get("img_url"):
                await message.reply_photo(photo=verse_char["img_url"], caption=text)
            else:
                await message.reply_text(text)
        except Exception as e2:
            log.error(f"REWARD: fallback failed uid={uid}: {e2}")
            await message.reply_text(text)

    log.info(f"REWARD: uid={uid} char={verse_char['name']!r} instance={instance_id}")


# ─────────────────────────────────────────────────────────────────────────────
# /resetreward  — owner only
# Usage: /resetreward <user_id>  OR  reply to user's message + /resetreward
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("resetreward") & owner_filter)
async def cmd_reset_reward(_, message: Message):
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
