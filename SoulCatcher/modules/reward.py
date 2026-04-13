"""
SoulCatcher/modules/reward.py
════════════════════════════════════════════════════════════════════════════════
/reward  —  One-time Verse character reward
  • User runs /reward in the GC
  • Bot pulls a random 🎠 Verse (cartoon) character from the DB
    — these are rarity="cartoon", video_only=True, must have video_url
  • Character is added to the user's harem
  • Announcement is sent IN THE GC with the character's video
  • Each user can only claim ONCE, ever (lifetime lock)
════════════════════════════════════════════════════════════════════════════════
"""

import logging
from datetime import datetime

from pyrogram import filters
from pyrogram.types import Message

from .. import app
from ..database import (
    get_or_create_user,
    update_user,
    get_random_character,
    add_to_harem,
)
from ..rarity import get_rarity

log = logging.getLogger("SoulCatcher.reward")

# The Verse sub-rarity name (defined in rarity.py as SUB_RARITIES["cartoon"])
_VERSE_RARITY = "verse"


# ─────────────────────────────────────────────────────────────────────────────
# /reward
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("reward"))
async def cmd_reward(_, message: Message):
    uid  = message.from_user.id
    user = await get_or_create_user(
        uid,
        message.from_user.username   or "",
        message.from_user.first_name or "",
        message.from_user.last_name  or "",
    )

    # ── Ban check ─────────────────────────────────────────────────────────────
    if user.get("is_banned"):
        return await message.reply_text("🚫 You are globally banned.")

    # ── One-time lifetime check ───────────────────────────────────────────────
    if user.get("reward_claimed"):
        claimed_at = user.get("reward_claimed_at")
        date_str   = claimed_at.strftime("%d %b %Y") if claimed_at else "a while ago"
        return await message.reply_text(
            f"❌ **Already Claimed!**\n\n"
            f"You claimed your Verse reward on `{date_str}`.\n"
            f"This reward can only be claimed **once per account** — forever."
        )

    # ── Fetch a Verse (cartoon) character — VIDEO ONLY ────────────────────────
    # get_random_character("cartoon") already enforces video_url via is_video_only()
    verse_char = await get_random_character(_VERSE_RARITY)
    if not verse_char:
        return await message.reply_text(
            "⚠️ No Verse characters in the database yet.\n"
            "Ask an admin to add some `cartoon` rarity characters with a `video_url`!"
        )

    # ── Add to harem & lock the reward ───────────────────────────────────────
    instance_id = await add_to_harem(uid, verse_char)
    now         = datetime.utcnow()
    await update_user(uid, {
        "$set": {
            "reward_claimed":    True,
            "reward_claimed_at": now,
        }
    })

    # ── Build announcement ────────────────────────────────────────────────────
    r           = get_rarity(_VERSE_RARITY)          # SUB_RARITIES["cartoon"]
    emoji       = r.emoji        if r else "🎠"
    display     = r.display_name if r else "Verse"
    user_mention = (
        f"@{message.from_user.username}"
        if message.from_user.username
        else message.from_user.first_name or "Someone"
    )

    text = (
        f"🎠 **VERSE REWARD CLAIMED!**\n\n"
        f"👤 {user_mention} just claimed their one-time Verse reward!\n\n"
        f"✨ **{verse_char['name']}**\n"
        f"📖 *{verse_char.get('anime', 'Unknown')}*\n"
        f"{emoji} **{display}**\n"
        f"🆔 Instance: `{instance_id}`\n\n"
        f"🔒 *This reward is claimed once — forever.*"
    )

    # ── Send in GC with the character's video ────────────────────────────────
    try:
        await message.reply_video(
            video=verse_char["video_url"],
            caption=text,
        )
    except Exception as e:
        log.warning(f"REWARD: video send failed uid={uid}: {e}")
        # fallback to image or plain text
        try:
            if verse_char.get("img_url"):
                await message.reply_photo(photo=verse_char["img_url"], caption=text)
            else:
                await message.reply_text(text)
        except Exception as e2:
            log.error(f"REWARD: fallback also failed uid={uid}: {e2}")
            await message.reply_text(text)

    log.info(
        f"REWARD: uid={uid} char={verse_char['name']!r} "
        f"rarity={_VERSE_RARITY} instance={instance_id}"
    )
