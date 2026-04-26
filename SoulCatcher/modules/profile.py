"""SoulCatcher/modules/profile.py — /profile, /balance, /level, /daily, /spin."""
from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta

import SoulCatcher as _soul
from pyrogram import filters
from pyrogram.types import Message

from SoulCatcher.database import (
    get_or_create_user,
    get_user,
    update_user,
    get_balance,
    add_balance,
    add_xp,
    get_harem_count,
    get_harem_rarity_counts,
    xp_for_level,
)
from SoulCatcher.rarity import ECONOMY, LEVEL_REWARDS, rarity_display, RARITIES, SUB_RARITIES

log = logging.getLogger("SoulCatcher.profile")


def _user_mention(u) -> str:
    return f"[{u.first_name}](tg://user?id={u.id})"


# ── /profile ──────────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command(["profile", "me", "p"]))
async def profile_cmd(_, m: Message):
    target = m.reply_to_message.from_user if m.reply_to_message else m.from_user
    u_data = await get_or_create_user(target.id, target.username or "", target.first_name or "", target.last_name or "")

    harem_count = await get_harem_count(target.id)
    balance     = u_data.get("balance", 0)
    level       = u_data.get("level", 1)
    xp          = u_data.get("xp", 0)
    next_xp     = xp_for_level(level + 1)
    streak      = u_data.get("daily_streak", 0)
    badges      = u_data.get("badges", [])
    married     = u_data.get("total_married", 0)
    claimed     = u_data.get("total_claimed", 0)

    # XP bar
    prev_xp  = xp_for_level(level)
    xp_range = max(next_xp - prev_xp, 1)
    xp_now   = xp - prev_xp
    filled   = int((xp_now / xp_range) * 10)
    bar      = "█" * filled + "░" * (10 - filled)

    badge_str = " ".join(badges[-5:]) if badges else "None"

    text = (
        f"🌸 **{target.first_name}'s Profile**\n\n"
        f"💰 Kakera:     `{balance:,}`\n"
        f"🎴 Collection: `{harem_count:,}` characters\n"
        f"⭐ Level:      `{level}` | XP: `{xp:,}/{next_xp:,}`\n"
        f"   `[{bar}]`\n"
        f"🔥 Streak:     `{streak}` days\n"
        f"💍 Marriages:  `{married}`\n"
        f"✅ Claimed:    `{claimed:,}`\n"
        f"🏅 Badges:     {badge_str}"
    )

    await m.reply(text)


# ── /balance ──────────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command(["balance", "bal", "kakera"]))
async def balance_cmd(_, m: Message):
    target = m.reply_to_message.from_user if m.reply_to_message else m.from_user
    bal    = await get_balance(target.id)
    await m.reply(f"💰 **{target.first_name}** has `{bal:,}` kakera.")


# ── /level ────────────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command(["level", "xp", "rank"]))
async def level_cmd(_, m: Message):
    target = m.reply_to_message.from_user if m.reply_to_message else m.from_user
    u_data = await get_user(target.id)
    if not u_data:
        await m.reply("No profile found. Send a message first!")
        return

    level    = u_data.get("level", 1)
    xp       = u_data.get("xp", 0)
    next_xp  = xp_for_level(level + 1)
    prev_xp  = xp_for_level(level)
    xp_range = max(next_xp - prev_xp, 1)
    xp_now   = xp - prev_xp
    filled   = int((xp_now / xp_range) * 20)
    bar      = "█" * filled + "░" * (20 - filled)
    pct      = int((xp_now / xp_range) * 100)

    # Next milestone
    milestones = sorted(LEVEL_REWARDS.keys())
    next_ms    = next((l for l in milestones if l > level), None)
    ms_text    = f"\n🎯 Next milestone: Level **{next_ms}** ({LEVEL_REWARDS[next_ms]['badge']})" if next_ms else ""

    await m.reply(
        f"⭐ **{target.first_name}'s Level**\n\n"
        f"Level: **{level}**\n"
        f"`[{bar}]` {pct}%\n"
        f"XP: `{xp:,}` / `{next_xp:,}`"
        f"{ms_text}"
    )


# ── /daily ────────────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command("daily"))
async def daily_cmd(_, m: Message):
    uid    = m.from_user.id
    u_data = await get_or_create_user(uid, m.from_user.username or "", m.from_user.first_name or "", m.from_user.last_name or "")

    now       = datetime.utcnow()
    last_d    = u_data.get("last_daily")
    streak    = u_data.get("daily_streak", 0)

    if last_d:
        if isinstance(last_d, str):
            last_d = datetime.fromisoformat(last_d)
        diff = (now - last_d).total_seconds()
        if diff < 86400:  # 24h
            remaining = 86400 - diff
            h, r      = divmod(int(remaining), 3600)
            mins      = r // 60
            await m.reply(f"⏳ Daily already claimed! Come back in **{h}h {mins}m**.")
            return
        # Check streak continuity (within 48h)
        if diff < 172800:
            streak = min(streak + 1, ECONOMY["daily_streak_max"])
        else:
            streak = 1
    else:
        streak = 1

    base   = ECONOMY["daily_base"]
    bonus  = ECONOMY["daily_streak_bonus"] * (streak - 1)
    reward = base + bonus

    await add_balance(uid, reward)
    await add_xp(uid, 50)
    await update_user(uid, {"$set": {"last_daily": now, "daily_streak": streak}})

    streak_text = f"\n🔥 **Streak:** {streak} day{'s' if streak > 1 else ''} (+{bonus:,} bonus)" if streak > 1 else ""
    await m.reply(
        f"✅ **Daily claimed!**\n\n"
        f"💰 +{reward:,} kakera\n"
        f"⭐ +50 XP"
        f"{streak_text}"
    )


# ── /spin ─────────────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command("spin"))
async def spin_cmd(_, m: Message):
    uid    = m.from_user.id
    u_data = await get_or_create_user(uid, m.from_user.username or "", m.from_user.first_name or "", m.from_user.last_name or "")

    now    = datetime.utcnow()
    last_s = u_data.get("last_spin")

    if last_s:
        if isinstance(last_s, str):
            last_s = datetime.fromisoformat(last_s)
        diff = (now - last_s).total_seconds()
        if diff < ECONOMY["spin_cooldown"]:
            remaining = ECONOMY["spin_cooldown"] - diff
            h, r      = divmod(int(remaining), 3600)
            mins      = r // 60
            await m.reply(f"⏳ Spin on cooldown! Try again in **{h}h {mins}m**.")
            return

    reward  = random.randint(ECONOMY["spin_min"], ECONOMY["spin_max"])
    symbols = ["🍒", "🍋", "🍊", "🍇", "💎", "⭐", "🌸", "✨", "🎰", "💰"]
    reels   = [random.choice(symbols) for _ in range(3)]
    display = " | ".join(reels)

    # Jackpot?
    if len(set(reels)) == 1:
        reward *= 5
        result_text = f"🎰 **JACKPOT!** {display}\n💰 **{reward:,} kakera!** 🎉"
    elif len(set(reels)) == 2:
        reward = int(reward * 1.5)
        result_text = f"🎰 **{display}**\n💰 +{reward:,} kakera (2 match!)"
    else:
        result_text = f"🎰 **{display}**\n💰 +{reward:,} kakera"

    await add_balance(uid, reward)
    await update_user(uid, {"$set": {"last_spin": now}})
    await m.reply(result_text)


# ── /pay ─────────────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command("pay"))
async def pay_cmd(_, m: Message):
    if not m.reply_to_message:
        await m.reply("↩️ Reply to a user to pay them.")
        return

    parts = m.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await m.reply("Usage: `/pay <amount>` (reply to user)")
        return

    amount = int(parts[1])
    minimum = ECONOMY["pay_minimum"]
    if amount < minimum:
        await m.reply(f"❌ Minimum payment is **{minimum}** kakera.")
        return

    sender = m.from_user
    target = m.reply_to_message.from_user
    if target.id == sender.id:
        await m.reply("❌ You can't pay yourself!")
        return
    if target.is_bot:
        await m.reply("❌ You can't pay a bot.")
        return

    from SoulCatcher.database import deduct_balance
    fee     = max(1, int(amount * ECONOMY["transfer_fee_pct"] / 100))
    total   = amount + fee
    success = await deduct_balance(sender.id, total)

    if not success:
        bal = await get_balance(sender.id)
        await m.reply(f"❌ Insufficient kakera. You have `{bal:,}`, need `{total:,}` (incl. {fee} fee).")
        return

    await add_balance(target.id, amount)
    await m.reply(
        f"💸 **{sender.first_name}** paid **{target.first_name}** `{amount:,}` kakera!\n"
        f"🏦 Transfer fee: `{fee:,}` kakera"
    )
