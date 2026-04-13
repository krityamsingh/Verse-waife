"""
SoulCatcher/modules/reward.py
════════════════════════════════════════════════════════════════════════════════
/reward       — One-time verse reward (picks from user's harem as their verse,
                rolls a bonus character and gives it to them)
/resetreward  — Owner only: reset a single user's reward claim
/resetallrewards — Owner only: reset ALL users' reward claims at once
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
    get_user,
    update_user,
    get_all_harem,
    get_random_character,
    add_to_harem,
    reset_reward_claim,
    reset_all_reward_claims,
)
from ..rarity import get_rarity

log = logging.getLogger("SoulCatcher.reward")

OWNER_ID = 6118760915

# Rarity weights for the bonus reward character roll
REWARD_WEIGHTS: dict[str, float] = {
    "common":   0.00,
    "rare":     0.00,
    "cosmos":   0.00,
    "infernal":  0.00,
    "seasonal":  0.00,
    "mythic":    0.00,
    "eternal":   100.00,
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
    Prefer characters with a video_url so the announcement looks great.
    """
    with_video = [c for c in harem if c.get("video_url")]
    pool = with_video if with_video else harem
    return random.choice(pool) if pool else None


def _get_show_name(char: dict) -> str:
    """
    Pull the show/cartoon name from a character doc.
    Characters collection stores it as 'cartoon'.
    Harem (user_characters) stores it as 'anime' (legacy field name).
    Check both so it works from either collection.
    """
    return char.get("cartoon") or char.get("anime") or "Unknown"


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
        f"📖 *{_get_show_name(verse_char)}*\n\n"
        f"🎁 **Reward Received:** {reward_char['name']}\n"
        f"📖 *{_get_show_name(reward_char)}*\n"
        f"{emoji} **{display}**\n"
        f"🆔 Instance: `{instance_id}`\n\n"
        f"✅ *One-time verse reward — claimed for life!*"
    )


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

    # Always fetch fresh so resets are reflected instantly
    user = await get_user(uid)

    if user.get("is_banned"):
        return await message.reply_text("🚫 You are globally banned.")

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

    # Pick a verse character from harem to show in announcement
    verse_char = _pick_verse_char(harem)
    if not verse_char:
        return await message.reply_text("⚠️ Could not select a verse character. Try again.")

    # Roll a reward rarity and fetch a character for it
    rarity_name = _roll_reward_rarity()
    reward_char = await get_random_character(rarity_name)
    if not reward_char:
        # Fallback to common if rolled rarity has no characters
        reward_char = await get_random_character("common")
        if not reward_char:
            return await message.reply_text(
                "⚠️ No characters in the database to reward. Ask an admin to add some!"
            )
        rarity_name = "common"

    # Attempt media delivery FIRST — only lock the claim on success
    user_display = (
        f"@{message.from_user.username}"
        if message.from_user.username
        else message.from_user.first_name or "Someone"
    )

    # We need instance_id for the text but we haven't added to harem yet —
    # generate a preview id, add to harem, then send
    instance_id = await add_to_harem(uid, reward_char)

    text = _build_reward_text(user_display, verse_char, reward_char, rarity_name, instance_id)

    delivered = False
    try:
        if verse_char.get("video_url"):
            await message.reply_video(video=verse_char["video_url"], caption=text)
        elif verse_char.get("img_url"):
            await message.reply_photo(photo=verse_char["img_url"], caption=text)
        else:
            await message.reply_text(text)
        delivered = True
    except Exception as e:
        log.warning(f"REWARD: media send failed uid={uid}: {e}")
        try:
            await message.reply_text(text)
            delivered = True
        except Exception as e2:
            log.error(f"REWARD: text fallback also failed uid={uid}: {e2}")

    if not delivered:
        return

    # Lock the reward
    await update_user(uid, {
        "$set": {
            "reward_claimed":    True,
            "reward_claimed_at": datetime.utcnow(),
        }
    })

    log.info(
        f"REWARD: uid={uid} verse={verse_char['name']!r} "
        f"reward={reward_char['name']!r} rarity={rarity_name} instance={instance_id}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# /resetreward — reset a single user
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
    log.info(f"RESETREWARD: owner={message.from_user.id} reset uid={target_id}")
    await message.reply_text(
        f"✅ Done! Verse reward reset for `{target_id}`.\n"
        f"They can now use `/reward` again."
    )


# ─────────────────────────────────────────────────────────────────────────────
# /resetallrewards — reset ALL users at once
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("resetallrewards"))
async def cmd_reset_all_rewards(_, message: Message):
    if message.from_user.id != OWNER_ID:
        return await message.reply_text("🚫 You are not authorized to use this command.")

    count = await reset_all_reward_claims()
    log.info(f"RESETALLREWARDS: owner={message.from_user.id} reset {count} users")
    await message.reply_text(
        f"✅ Done! Reset verse reward for **{count}** user(s).\n"
        f"Everyone can now use `/reward` again."
    )
