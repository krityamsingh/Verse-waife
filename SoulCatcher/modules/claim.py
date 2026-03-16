"""
SoulCatcher/claim.py
═══════════════════════════════════════════════════════════════════════
Daily character claim module for Pyrogram — professional, sexy, weighted.

Users get one random character every 24 hours. Rarity probability is inversely
proportional to daily drop limits: rarer rarities appear much less often,
exactly as requested ("don't give the 5–7 rarity so much"). Streak tracking
with motivational messages, media fallback, and full error handling.
"""

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Tuple

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode

from SoulCatcher import database as db
from SoulCatcher.rarity import get_drop_limit

log = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------
DAILY_COOLDOWN = timedelta(hours=24)
BASE_WEIGHT_FOR_UNLIMITED = 100.0          # high weight for unlimited rarities


# ----------------------------------------------------------------------
# Weighted rarity selection (core logic)
# ----------------------------------------------------------------------
async def _get_rarity_weights() -> List[Tuple[str, float]]:
    """
    Build list of (rarity_name, normalized_weight) for all enabled rarities.

    Weight = 1 / (drop_limit + 1)   where drop_limit is from rarity.py.
    Rarities with drop_limit = 0 (unlimited) get a fixed high weight.
    """
    # Get distinct rarities from enabled characters
    pipeline = [
        {"$match": {"enabled": True}},
        {"$group": {"_id": "$rarity"}},
    ]
    rarity_docs = await db._col("characters").aggregate(pipeline).to_list(None)
    rarities = [doc["_id"] for doc in rarity_docs if doc["_id"]]

    if not rarities:
        log.error("No rarities found in enabled characters.")
        return []

    raw_weights = []
    for rarity in rarities:
        limit = get_drop_limit(rarity)
        if limit == 0:
            weight = BASE_WEIGHT_FOR_UNLIMITED
        else:
            weight = 1.0 / (limit + 1)
        raw_weights.append((rarity, weight))

    # Normalize
    total = sum(w for _, w in raw_weights)
    if total <= 0:
        log.warning("Total weight <= 0, using equal weights.")
        return [(r, 1.0 / len(raw_weights)) for r, _ in raw_weights]

    return [(r, w / total) for r, w in raw_weights]


async def _select_daily_rarity() -> Optional[str]:
    """Pick a rarity using weighted probability."""
    weighted = await _get_rarity_weights()
    if not weighted:
        return None
    rarities, weights = zip(*weighted)
    return random.choices(rarities, weights=weights, k=1)[0]


def _streak_message(streak: int) -> str:
    """Return a motivational message based on streak length."""
    if streak >= 30:
        return "🔥🔥 **LEGENDARY STREAK!** 🔥🔥"
    if streak >= 14:
        return "🌟 **Incredible streak!** 🌟"
    if streak >= 7:
        return "✨ **Week‑long streak!** ✨"
    if streak >= 3:
        return "⭐ **Three days in a row!** ⭐"
    return "⚡ Keep it up!"


# ----------------------------------------------------------------------
# Command handler
# ----------------------------------------------------------------------
@Client.on_message(filters.command("daily"))
async def daily_claim(client: Client, message: Message) -> None:
    """
    Handle /daily command: award one random character with weighted rarity.
    """
    user = message.from_user
    chat = message.chat
    if not user or not chat:
        return

    user_id = user.id
    first_name = user.first_name or ""
    username = user.username or ""

    # ------------------------------------------------------------------
    # 1. User validation
    # ------------------------------------------------------------------
    try:
        await db.get_or_create_user(user_id, username, first_name)
    except Exception as e:
        log.error(f"Failed to get/create user {user_id}: {e}")
        await message.reply_text("❌ Database error. Try again later.")
        return

    if await db.is_user_banned(user_id):
        await message.reply_text("🚫 You are banned from using the bot.")
        return

    # ------------------------------------------------------------------
    # 2. Cooldown check
    # ------------------------------------------------------------------
    user_data = await db.get_user(user_id)
    if not user_data:
        await message.reply_text("⚠️ Could not load your profile.")
        return

    now = datetime.now(timezone.utc)
    last_daily = user_data.get("last_daily")
    if last_daily:
        if isinstance(last_daily, datetime) and last_daily.tzinfo is None:
            last_daily = last_daily.replace(tzinfo=timezone.utc)
        if last_daily and (now - last_daily) < DAILY_COOLDOWN:
            remaining = DAILY_COOLDOWN - (now - last_daily)
            hours, remainder = divmod(remaining.seconds, 3600)
            minutes = remainder // 60
            await message.reply_text(
                f"⏳ You've already claimed today!\n"
                f"Next claim in **{hours}h {minutes}m**."
            )
            return

    # ------------------------------------------------------------------
    # 3. Streak calculation
    # ------------------------------------------------------------------
    yesterday = now - timedelta(days=1)
    if last_daily and last_daily.date() == yesterday.date():
        new_streak = user_data.get("daily_streak", 0) + 1
    else:
        new_streak = 1

    # ------------------------------------------------------------------
    # 4. Select rarity (weighted)
    # ------------------------------------------------------------------
    rarity = await _select_daily_rarity()
    if not rarity:
        log.error("No rarity could be selected.")
        await message.reply_text("😵 No characters available right now.")
        return

    # ------------------------------------------------------------------
    # 5. Fetch random character of that rarity
    # ------------------------------------------------------------------
    char = await db.get_random_character(rarity)
    if not char:
        log.warning(f"No enabled character for rarity '{rarity}'.")
        await message.reply_text(
            f"⚠️ No **{rarity}** characters available.\n"
            f"Here's **50 gold** as consolation!"
        )
        await db.add_balance(user_id, 50)
        # Still update last_daily to avoid abuse
        await db.update_user(user_id, {
            "$set": {
                "last_daily": now.replace(tzinfo=None),
                "daily_streak": new_streak,
            }
        })
        return

    # ------------------------------------------------------------------
    # 6. Add to harem and update user
    # ------------------------------------------------------------------
    try:
        instance_id = await db.add_to_harem(user_id, char)
    except Exception as e:
        log.error(f"Failed to add character to harem: {e}")
        await message.reply_text("❌ Failed to save character. Please report.")
        return

    await db.update_user(user_id, {
        "$set": {
            "last_daily": now.replace(tzinfo=None),
            "daily_streak": new_streak,
        }
    })

    # ------------------------------------------------------------------
    # 7. Prepare and send the response
    # ------------------------------------------------------------------
    name = char.get("name", "Unknown")
    anime = char.get("anime", "Unknown")
    img_url = char.get("img_url") or char.get("video_url")
    rarity_display = char.get("rarity", rarity)

    caption = (
        f"🎁 **Daily Claim** 🎁\n\n"
        f"**{name}** from **{anime}**\n"
        f"Rarity: **{rarity_display}**\n"
        f"Instance ID: `{instance_id}`\n\n"
        f"🔥 **Streak:** {new_streak}\n"
        f"{_streak_message(new_streak)}"
    )

    try:
        if img_url:
            if char.get("video_url"):
                await message.reply_animation(
                    animation=img_url,
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await message.reply_photo(
                    photo=img_url,
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN
                )
        else:
            await message.reply_text(caption, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        log.warning(f"Failed to send media: {e}")
        await message.reply_text(caption, parse_mode=ParseMode.MARKDOWN)

    log.info(f"Daily claim: user {user_id} got {name} ({rarity}) streak {new_streak}")


# ----------------------------------------------------------------------
# Optional: function to manually register the handler (if not using decorator)
# ----------------------------------------------------------------------
def register(client: Client):
    """Add the daily command handler to the client."""
    client.add_handler(Client.on_message(filters.command("daily"))(daily_claim))
    log.info("✅ Daily claim handler registered.")
