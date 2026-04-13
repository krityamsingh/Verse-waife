"""
SoulCatcher/modules/reward.py
════════════════════════════════════════════════════════════════════════════════
/reward  —  Verse character reward
  • Each user can only claim ONCE ever (lifetime, not daily)
  • The reward character must already be in the user's harem
    (i.e. it must be a character the user actually owns from their verse)
  • On claim, the bot announces in the group chat with the character's
    video (or image fallback) so everyone can see
════════════════════════════════════════════════════════════════════════════════
"""

import logging
import random
from datetime import datetime

from pyrogram import filters
from pyrogram.types import Message

from .. import app
from ..database import (
    get_or_create_user,
    update_user,
    get_all_harem,
    get_random_character,
    add_to_harem,
    get_user,
)
from ..rarity import get_rarity

log = logging.getLogger("SoulCatcher.reward")

# ─────────────────────────────────────────────────────────────────────────────
# REWARD CONFIG
# ─────────────────────────────────────────────────────────────────────────────

# Rarity weights for the bonus reward character roll
REWARD_WEIGHTS: dict[str, float] = {
    "common":   40.00,
    "rare":     30.00,
    "cosmos":   17.00,
    "infernal":  8.00,
    "seasonal":  3.00,
    "mythic":    1.50,
    "eternal":   0.50,
}


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _roll_reward_rarity() -> str:
    names   = list(REWARD_WEIGHTS.keys())
    weights = [REWARD_WEIGHTS[n] for n in names]
    return random.choices(names, weights=weights, k=1)[0]


def _pick_verse_char(harem: list[dict]) -> dict | None:
    """
    Pick a random character from the user's harem to represent their 'verse'.
    Prefer characters with a video_url so the GC announcement looks great.
    """
    with_video = [c for c in harem if c.get("video_url")]
    pool = with_video if with_video else harem
    return random.choice(pool) if pool else None


def _build_reward_text(
    user_display: str,
    verse_char: dict,
    reward_char: dict,
    rarity_name: str,
    instance_id: str,
) -> str:
    r       = get_rarity(rarity_name)
    emoji   = r.emoji        if r else "🎁"
    display = r.display_name if r else rarity_name.title()
    return (
        f"🎉 **VERSE REWARD CLAIMED!** 🎉\n\n"
        f"👤 **{user_display}** has claimed their verse reward!\n\n"
        f"🌟 **Verse Character:** {verse_char['name']}\n"
        f"📖 *{verse_char.get('anime', 'Unknown')}*\n\n"
        f"🎁 **Reward Received:** {reward_char['name']}\n"
        f"📖 *{reward_char.get('anime', 'Unknown')}*\n"
        f"{emoji} **{display}**\n"
        f"🆔 Instance: `{instance_id}`\n\n"
        f"✅ *One-time verse reward — claimed for life!*"
    )


# ─────────────────────────────────────────────────────────────────────────────
# /reward
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("reward"))
async def cmd_reward(_, message: Message):
    """
    Claim your one-time verse reward.
    - You must have at least one character in your harem.
    - Can only be claimed ONCE per user (lifetime).
    - Announces in the group with your verse character's video.
    """
    uid  = message.from_user.id
    user = await get_or_create_user(
        uid,
        message.from_user.username   or "",
        message.from_user.first_name or "",
        message.from_user.last_name  or "",
    )

    # Ban check
    if user.get("is_banned"):
        return await message.reply_text("🚫 You are globally banned.")

    # One-time claim check
    if user.get("reward_claimed"):
        claimed_at = user.get("reward_claimed_at")
        date_str   = claimed_at.strftime("%Y-%m-%d") if claimed_at else "unknown date"
        return await message.reply_text(
            f"❌ **Already Claimed!**\n\n"
            f"You already redeemed your one-time verse reward on `{date_str}`.\n"
            f"Each user can only claim this reward **once** — forever."
        )

    # Must have characters in harem (their 'verse')
    harem = await get_all_harem(uid)
    if not harem:
        return await message.reply_text(
            "❌ **No Verse Found!**\n\n"
            "You don't have any characters yet!\n"
            "Catch or claim some characters first — they become your verse.\n"
            "Then use `/reward` to claim your one-time bonus!"
        )

    # Pick a verse character to represent the user
    verse_char = _pick_verse_char(harem)
    if not verse_char:
        return await message.reply_text("⚠️ Could not select a verse character. Try again.")

    # Roll a reward character
    rarity_name  = _roll_reward_rarity()
    reward_char  = await get_random_character(rarity_name)
    if not reward_char:
        reward_char = await get_random_character("common")
        if not reward_char:
            return await message.reply_text(
                "⚠️ No characters in the database to reward. Ask an admin to add some!"
            )
        rarity_name = "common"

    # Add reward character to harem
    instance_id = await add_to_harem(uid, reward_char)

    # Mark user as having claimed their reward
    now = datetime.utcnow()
    await update_user(uid, {
        "$set": {
            "reward_claimed":    True,
            "reward_claimed_at": now,
        }
    })

    # Build the announcement text
    user_display = (
        f"@{message.from_user.username}"
        if message.from_user.username
        else message.from_user.first_name or "Someone"
    )
    text = _build_reward_text(user_display, verse_char, reward_char, rarity_name, instance_id)

    # Send to the group (current chat) with the verse character's video/image
    try:
        if verse_char.get("video_url"):
            await message.reply_video(
                video=verse_char["video_url"],
                caption=text,
            )
        elif verse_char.get("img_url"):
            await message.reply_photo(
                photo=verse_char["img_url"],
                caption=text,
            )
        else:
            await message.reply_text(text)
    except Exception as e:
        log.warning(f"REWARD: media send failed for uid={uid}: {e}")
        await message.reply_text(text)

    log.info(
        f"REWARD: uid={uid} verse_char={verse_char['name']!r} "
        f"reward={reward_char['name']!r} rarity={rarity_name} instance={instance_id}"
    )
