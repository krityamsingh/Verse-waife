"""
🔐 SECURITY FIX FOR: SoulCatcher/modules/economy.py (/pay command)

ISSUE: No rate limiting on /pay - user can spam 1 kakera transfers infinitely
IMPACT: Database thrashing, spam, transaction flooding

FIX: Added cooldown tracking (1 second between /pay commands per user)
"""

import asyncio
import logging
from datetime import datetime, timedelta
from time import time

from pyrogram import filters
from pyrogram.types import Message

from .. import app
from ..database import get_user, get_balance, deduct_balance, add_balance, get_or_create_user
from ..rarity import ECONOMY

log = logging.getLogger("SoulCatcher.economy")

# ────────────────────────────────────────────────────────────────────────────────
# 🔐 RATE LIMITING FOR /pay
# ────────────────────────────────────────────────────────────────────────────────

_pay_cooldown = {}  # {user_id: last_timestamp}

def get_pay_cooldown(user_id: int) -> float:
    """Get remaining cooldown in seconds"""
    if user_id not in _pay_cooldown:
        return 0.0
    return max(0, 1.0 - (time() - _pay_cooldown[user_id]))


# ────────────────────────────────────────────────────────────────────────────────
# DAILY COMMAND (unchanged, shown for reference)
# ────────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("daily"))
async def cmd_daily(_, message: Message):
    """Claim daily kakera with streak bonuses"""
    uid = message.from_user.id
    user = await get_or_create_user(uid, message.from_user.username or "", 
                                     message.from_user.first_name or "", 
                                     message.from_user.last_name or "")
    
    now = datetime.utcnow()
    last = user.get("last_daily")
    
    if last:
        diff = (now - last).total_seconds()
        if diff < 86400:  # 24 hours
            hours_left = int((86400 - diff) / 3600)
            minutes_left = int(((86400 - diff) % 3600) / 60)
            return await message.reply_text(
                f"⏳ **Already claimed today!**\n"
                f"Next claim in: `{hours_left}h {minutes_left}m`"
            )
        # Reset streak if >48 hours passed
        if diff > 172800:
            streak = 0
        else:
            streak = user.get("daily_streak", 0)
    else:
        streak = 0
    
    streak = min(streak + 1, ECONOMY["daily_streak_max"])
    reward = ECONOMY["daily_base"] + (ECONOMY["daily_streak_bonus"] * (streak - 1))
    
    await add_balance(uid, reward)
    from ..database import update_user
    await update_user(uid, {"$set": {
        "last_daily": now,
        "daily_streak": streak,
        "last_seen": now
    }})
    
    await message.reply_text(
        f"💰 **Daily Claimed!**\n"
        f"• Reward: `{reward}` kakera\n"
        f"• Streak: `{streak}/{ECONOMY['daily_streak_max']}` 🔥\n"
        f"• Next claim: Tomorrow"
    )
    log.info(f"DAILY: {uid} claimed {reward} kakera (streak: {streak})")


# ────────────────────────────────────────────────────────────────────────────────
# SPIN COMMAND (unchanged, shown for reference)
# ────────────────────────────────────────────────────────────────────────────────

_spin_cooldown = {}  # {user_id: last_timestamp}

@app.on_message(filters.command("spin"))
async def cmd_spin(_, message: Message):
    """Spin wheel for random kakera (1 hour cooldown)"""
    uid = message.from_user.id
    now = time()
    
    if uid in _spin_cooldown and (now - _spin_cooldown[uid]) < 3:
        remaining = 3 - (now - _spin_cooldown[uid])
        return await message.reply_text(
            f"⏱️ **Wheel on cooldown!**\n"
            f"⏳ Try again in `{remaining:.1f}s`"
        )
    
    import random
    reward = random.randint(50, 500)
    await add_balance(uid, reward)
    _spin_cooldown[uid] = now
    
    emojis = ["🎰", "🎲", "🎯", "🎪"]
    await message.reply_text(
        f"{random.choice(emojis)} **WHEEL SPIN!**\n"
        f"💰 You won: `{reward}` kakera!"
    )
    log.info(f"SPIN: {uid} won {reward} kakera")


# ────────────────────────────────────────────────────────────────────────────────
# PAY COMMAND (🔐 FIXED with rate limiting)
# ────────────────────────────────────────────────────────────────────────────────

async def cmd_pay(_, message: Message):
    """
    🔐 FIXED: Transfer kakera to another user with rate limiting
    Rate limit: 1 transfer per second per user
    Fee: 2% of amount
    """
    uid = message.from_user.id
    now = time()
    
    # Check rate limit
    cooldown = get_pay_cooldown(uid)
    if cooldown > 0:
        return await message.reply_text(
            f"⏱️ **Pay cooldown!**\n"
            f"⏳ Please wait `{cooldown:.1f}` seconds"
        )
    
    # Validate target
    if not message.reply_to_message:
        return await message.reply_text(
            "Reply to a user, then: `/pay <amount>`\n"
            "Example: `/pay 100`"
        )
    
    target = message.reply_to_message.from_user
    if target.is_bot or target.id == uid:
        return await message.reply_text("❌ Can't pay bots or yourself!")
    
    # Parse amount
    args = message.command
    if len(args) < 2:
        return await message.reply_text("Usage: `/pay <amount>` (as reply)")
    
    try:
        amount = int(args[1])
    except ValueError:
        return await message.reply_text("❌ Amount must be a number!")
    
    if amount < 1:
        return await message.reply_text("❌ Amount must be at least 1 kakera!")
    
    # Check sender's balance
    sender_balance = await get_balance(uid)
    transfer_fee = max(1, int(amount * ECONOMY["transfer_fee_pct"] / 100))
    total_cost = amount + transfer_fee
    
    if sender_balance < total_cost:
        return await message.reply_text(
            f"❌ **Insufficient balance!**\n"
            f"• You have: `{sender_balance}` kakera\n"
            f"• Need: `{total_cost}` kakera (incl. `{transfer_fee}` fee)\n"
            f"• Shortfall: `{total_cost - sender_balance}`"
        )
    
    # Ensure target exists
    await get_or_create_user(target.id, target.username or "", 
                             target.first_name or "", target.last_name or "")
    
    # Execute transfer (atomic in real implementation)
    try:
        await deduct_balance(uid, total_cost)
        await add_balance(target.id, amount)
        
        # Record cooldown ONLY on successful transfer
        _pay_cooldown[uid] = now
        
        await message.reply_text(
            f"✅ **Payment Sent!**\n"
            f"• To: [{target.first_name}](tg://user?id={target.id})\n"
            f"• Amount: `{amount}` kakera\n"
            f"• Fee: `{transfer_fee}` kakera (2%)\n"
            f"• Your balance: `{sender_balance - total_cost}`"
        )
        log.info(
            f"PAY: {uid} → {target.id}: {amount} kakera "
            f"(fee: {transfer_fee}, total: {total_cost})"
        )
    except Exception as e:
        log.error(f"PAY error: {uid} → {target.id}: {e}")
        await message.reply_text(f"❌ Transfer failed: {e}")


# ────────────────────────────────────────────────────────────────────────────────
# BALANCE COMMAND
# ────────────────────────────────────────────────────────────────────────────────

async def cmd_bal(_, message: Message):
    """Check your kakera balance"""
    uid = message.from_user.id
    balance = await get_balance(uid)
    
    await message.reply_text(
        f"💰 **Your Balance**\n"
        f"```\n{balance:,} kakera\n```"
    )
