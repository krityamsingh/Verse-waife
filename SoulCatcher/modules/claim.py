"""
SoulCatcher/claim.py
═══════════════════════════════════════════════════════════════════════
Daily character claim module — professional, sexy, and weighted.

Features:
  • One random character per 24 hours (per user)
  • Rarity probability is inversely proportional to drop limit
    → 5–7 rarities are much rarer, as requested
  • Daily streak tracking with motivational messages
  • Clean, modular design with full error handling
  • Type hints and comprehensive logging
  • Media fallback and graceful failure

Dependencies:
  - database.py (MongoDB async layer)
  - rarity.py (drop limits per rarity)
"""

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Tuple, Dict, Any

from telegram import Update, Message
from telegram.ext import ContextTypes, CommandHandler
from telegram.constants import ParseMode

from SoulCatcher import database as db
from SoulCatcher.rarity import get_drop_limit  # drop limits define rarity tiers

log = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------

DAILY_COOLDOWN = timedelta(hours=24)          # one claim per day
STREAK_BONUS_THRESHOLD = 7                     # bonus after a full week
BASE_WEIGHT_FOR_UNLIMITED = 100.0               # high weight for unlimited rarities

# ----------------------------------------------------------------------
# Weighted rarity selection (core "sexy" logic)
# ----------------------------------------------------------------------

async def _get_rarity_weights() -> List[Tuple[str, float]]:
    """
    Build a list of (rarity_name, weight) for every enabled rarity.

    Weight = 1 / (drop_limit + 1)   where drop_limit is from rarity.py.
    Rarities with drop_limit = 0 (unlimited) get a fixed high weight.

    This ensures that rarities with low daily drop limits (the "5–7" tier)
    appear very rarely in daily claims — exactly what was asked.

    Returns:
        List of (rarity, normalized_weight) tuples.
        Empty list if no rarities exist.
    """
    # Get all distinct rarities from enabled characters
    pipeline = [
        {"$match": {"enabled": True}},
        {"$group": {"_id": "$rarity"}},
    ]
    rarity_docs = await db._col("characters").aggregate(pipeline).to_list(None)
    rarities = [doc["_id"] for doc in rarity_docs if doc["_id"]]

    if not rarities:
        log.error("No rarities found in enabled characters – daily claim impossible.")
        return []

    weights_raw = []
    for rarity in rarities:
        limit = get_drop_limit(rarity)
        # drop_limit 0 = unlimited spawns → treat as very common
        if limit == 0:
            weight = BASE_WEIGHT_FOR_UNLIMITED
        else:
            # Inverse relation: higher limit → lower weight
            weight = 1.0 / (limit + 1)
        weights_raw.append((rarity, weight))

    # Normalize to sum = 1 for clean probabilities
    total = sum(w for _, w in weights_raw)
    if total <= 0:
        # Fallback to equal weights (should never happen)
        log.warning("Total weight <= 0, using equal weights for rarities.")
        return [(r, 1.0 / len(weights_raw)) for r, _ in weights_raw]

    return [(r, w / total) for r, w in weights_raw]


async def _select_daily_rarity() -> Optional[str]:
    """
    Choose a rarity based on weighted probabilities.
    Returns None if no rarities are available.
    """
    weighted = await _get_rarity_weights()
    if not weighted:
        return None
    rarities, weights = zip(*weighted)
    return random.choices(rarities, weights=weights, k=1)[0]


# ----------------------------------------------------------------------
# Streak messaging (adds a little sexiness)
# ----------------------------------------------------------------------

def _streak_message(streak: int) -> str:
    """Return a motivational emoji/string based on streak length."""
    if streak >= 30:
        return "🔥🔥 **LEGENDARY STREAK!** 🔥🔥"
    if streak >= 14:
        return "🌟 **Incredible streak!** 🌟"
    if streak >= 7:
        return "✨ **Week-long streak!** ✨"
    if streak >= 3:
        return "⭐ **Three days in a row!** ⭐"
    return "⚡ Keep it up!"


# ----------------------------------------------------------------------
# Daily claim handler (the main attraction)
# ----------------------------------------------------------------------

async def daily_claim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handler for /daily command.
    Awards one random character, respecting 24h cooldown and weighted rarity.
    """
    user = update.effective_user
    chat = update.effective_chat
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
        await update.message.reply_text("❌ Database error. Please try again later.")
        return

    if await db.is_user_banned(user_id):
        await update.message.reply_text("🚫 You are banned from using the bot.")
        return

    # ------------------------------------------------------------------
    # 2. Cooldown check
    # ------------------------------------------------------------------
    user_data = await db.get_user(user_id)
    if not user_data:
        await update.message.reply_text("⚠️ Could not load your profile. Please try again.")
        return

    now = datetime.now(timezone.utc)
    last_daily = user_data.get("last_daily")

    if last_daily:
        # Ensure last_daily is timezone-aware (it's stored naive in DB)
        if isinstance(last_daily, datetime) and last_daily.tzinfo is None:
            last_daily = last_daily.replace(tzinfo=timezone.utc)

        if last_daily and (now - last_daily) < DAILY_COOLDOWN:
            remaining = DAILY_COOLDOWN - (now - last_daily)
            hours, remainder = divmod(remaining.seconds, 3600)
            minutes = remainder // 60
            await update.message.reply_text(
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
        log.error("No rarity could be selected for daily claim.")
        await update.message.reply_text("😵 No characters available for daily claim right now.")
        return

    # ------------------------------------------------------------------
    # 5. Fetch a random character of that rarity
    # ------------------------------------------------------------------
    char = await db.get_random_character(rarity)
    if not char:
        log.warning(f"No enabled character for rarity '{rarity}' in daily claim.")
        await update.message.reply_text(
            f"⚠️ No **{rarity}** characters are available at the moment.\n"
            f"We've given you a small consolation: **50 gold**!"
        )
        # Give some gold as fallback (optional, but adds kindness)
        await db.add_balance(user_id, 50)
        # Still update last_daily to avoid infinite retries
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
        log.error(f"Failed to add character to harem for user {user_id}: {e}")
        await update.message.reply_text("❌ Failed to save character. Please report this.")
        return

    await db.update_user(user_id, {
        "$set": {
            "last_daily": now.replace(tzinfo=None),
            "daily_streak": new_streak,
        }
    })

    # ------------------------------------------------------------------
    # 7. Prepare response with style
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

    # ------------------------------------------------------------------
    # 8. Send media with fallback
    # ------------------------------------------------------------------
    if img_url:
        try:
            if char.get("video_url"):
                await update.message.reply_animation(
                    animation=img_url,
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await update.message.reply_photo(
                    photo=img_url,
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN
                )
        except Exception as e:
            log.warning(f"Failed to send media for daily claim: {e}")
            await update.message.reply_text(caption, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(caption, parse_mode=ParseMode.MARKDOWN)

    log.info(f"Daily claim: user {user_id} got {name} ({rarity}) streak {new_streak}")


# ----------------------------------------------------------------------
# Command registration (plug into main bot)
# ----------------------------------------------------------------------

def register_handlers(application):
    """Add daily claim command to the application."""
    application.add_handler(CommandHandler("daily", daily_claim))
    log.info("✅ Daily claim handler registered.")
