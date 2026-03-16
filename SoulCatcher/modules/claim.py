"""
SoulCatcher/modules/claim.py
════════════════════════════════════════════════════════════════════════════════
/claim  —  Daily character claim
  • One free character every 24 hours
  • Rarity is rolled with CLAIM-specific weights (tiers 5-7 are rare treats,
    not guaranteed drops — see CLAIM_WEIGHTS below)
  • Character is pulled at random from the DB for the rolled rarity
  • Added straight to the user's harem
════════════════════════════════════════════════════════════════════════════════
"""

import logging
import random
from datetime import datetime, timedelta

from pyrogram import filters
from pyrogram.types import Message

from .. import app
from ..database import (
    get_or_create_user,
    update_user,
    get_random_character,
    add_to_harem,
    count_rarity_in_harem,
)
from ..rarity import RARITIES, get_rarity, roll_sub_rarity

log = logging.getLogger("SoulCatcher.claim")

# ─────────────────────────────────────────────────────────────────────────────
# CLAIM-SPECIFIC RARITY WEIGHTS
# These are intentionally different from spawn weights.
# Tiers 5 / 6 / 7 are drastically reduced — they should feel like jackpots,
# not something you reliably collect every few days.
#
#  Spawn weights (for reference):
#    common=55  rare=22  cosmos=10  infernal=5  seasonal=2.5  mythic=0.8  eternal=0.10
#
#  Claim weights (this file):
#    common=60  rare=26  cosmos=9.5  infernal=3.8  seasonal=0.45  mythic=0.20  eternal=0.05
# ─────────────────────────────────────────────────────────────────────────────

CLAIM_WEIGHTS: dict[str, float] = {
    "common":   60.00,   # ⚫ Tier 1 — bulk of daily pulls
    "rare":     26.00,   # 🔵 Tier 2 — comfortable runner-up
    "cosmos":    9.50,   # 🌌 Tier 3 — Legendry, still reachable
    "infernal":  3.80,   # 🔥 Tier 4 — Elite, exciting but not common
    "seasonal":  0.45,   # 💎 Tier 5 — Seasonal, notably rare
    "mythic":    0.20,   # 💀 Tier 6 — Mythic, very rare
    "eternal":   0.05,   # ✨ Tier 7 — Eternal, near-impossible
}

# Max copies a user may obtain via /claim for capped rarities (0 = unlimited)
CLAIM_MAX_PER_USER: dict[str, int] = {
    "common":   0,
    "rare":     0,
    "cosmos":   0,
    "infernal": 0,
    "seasonal": 5,
    "mythic":   3,
    "eternal":  1,
}

CLAIM_COOLDOWN_SECONDS = 86_400  # 24 hours


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _roll_claim_rarity():
    """Roll a rarity tier using claim-specific weights."""
    names   = list(CLAIM_WEIGHTS.keys())
    weights = [CLAIM_WEIGHTS[n] for n in names]
    return RARITIES[random.choices(names, weights=weights, k=1)[0]]


def _build_card(char: dict, rarity_name: str, instance_id: str, is_sub: bool) -> str:
    r       = get_rarity(rarity_name)
    emoji   = r.emoji        if r else "❓"
    display = r.display_name if r else rarity_name.title()
    sub_tag = " *(sub-rarity!)*" if is_sub else ""
    return (
        f"🎁 **Daily Character Claimed!**\n\n"
        f"**{char['name']}**\n"
        f"📖 *{char.get('anime', 'Unknown')}*\n\n"
        f"{emoji} **{display}**{sub_tag}\n"
        f"🆔 Instance: `{instance_id}`\n\n"
        f"⏳ Next claim in **24 hours**"
    )


# ─────────────────────────────────────────────────────────────────────────────
# /claim
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("claim"))
async def cmd_claim(_, message: Message):
    """Claim your free daily character."""
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

    # Cooldown check
    now        = datetime.utcnow()
    last_claim = user.get("last_claim")
    if last_claim:
        elapsed = (now - last_claim).total_seconds()
        if elapsed < CLAIM_COOLDOWN_SECONDS:
            remaining  = CLAIM_COOLDOWN_SECONDS - elapsed
            h = int(remaining // 3600)
            m = int((remaining % 3600) // 60)
            return await message.reply_text(
                f"⏳ **Already claimed today!**\n"
                f"Come back in `{h}h {m}m` for your next character."
            )

    # Roll rarity
    base_rarity = _roll_claim_rarity()
    sub         = roll_sub_rarity(base_rarity.name)
    rarity_name = sub.name if sub else base_rarity.name
    is_sub      = sub is not None

    # Per-user cap: if capped, fall back to Legendry
    max_copies = CLAIM_MAX_PER_USER.get(base_rarity.name, 0)
    if max_copies > 0:
        owned = await count_rarity_in_harem(uid, rarity_name)
        if owned >= max_copies:
            rarity_name = "cosmos"
            is_sub      = False
            log.info(f"CLAIM: {uid} capped on {base_rarity.name}, fell back to cosmos")

    # Fetch a character for the rolled rarity
    char = await get_random_character(rarity_name)
    if not char:
        char = await get_random_character("common")
        if not char:
            return await message.reply_text(
                "⚠️ No characters in the database yet. Ask an admin to add some!"
            )
        rarity_name = "common"
        is_sub      = False
        log.warning(f"CLAIM: no chars for rarity={rarity_name}, fell back to common")

    # Add to harem & stamp cooldown
    instance_id = await add_to_harem(uid, char)
    await update_user(uid, {"$set": {"last_claim": now}})

    # Send reply with media if available
    card = _build_card(char, rarity_name, instance_id, is_sub)
    try:
        if char.get("video_url"):
            await message.reply_video(video=char["video_url"], caption=card)
        elif char.get("img_url"):
            await message.reply_photo(photo=char["img_url"], caption=card)
        else:
            await message.reply_text(card)
    except Exception:
        await message.reply_text(card)

    log.info(
        f"CLAIM: uid={uid} char={char['name']!r} "
        f"rarity={rarity_name} instance={instance_id} sub={is_sub}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# /claiminfo  — shows cooldown status
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("claiminfo"))
async def cmd_claiminfo(_, message: Message):
    """Show when you can next use /claim."""
    uid  = message.from_user.id
    user = await get_or_create_user(
        uid,
        message.from_user.username   or "",
        message.from_user.first_name or "",
        message.from_user.last_name  or "",
    )

    now        = datetime.utcnow()
    last_claim = user.get("last_claim")

    if not last_claim or (now - last_claim).total_seconds() >= CLAIM_COOLDOWN_SECONDS:
        return await message.reply_text(
            "✅ **Ready to claim!**\nUse `/claim` to get your free character."
        )

    elapsed    = (now - last_claim).total_seconds()
    remaining  = CLAIM_COOLDOWN_SECONDS - elapsed
    h          = int(remaining // 3600)
    m          = int((remaining % 3600) // 60)
    next_time  = last_claim + timedelta(seconds=CLAIM_COOLDOWN_SECONDS)
    pct        = int((elapsed / CLAIM_COOLDOWN_SECONDS) * 24)
    bar        = "█" * pct + "░" * (24 - pct)

    await message.reply_text(
        f"⏳ **Claim Cooldown**\n\n"
        f"`{bar}` {int(elapsed / CLAIM_COOLDOWN_SECONDS * 100)}%\n\n"
        f"• Last claimed : `{last_claim.strftime('%H:%M UTC')}`\n"
        f"• Next claim   : `{next_time.strftime('%H:%M UTC')}`\n"
        f"• Remaining    : `{h}h {m}m`"
    )
