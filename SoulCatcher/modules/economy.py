"""SoulCatcher/modules/economy.py — /daily /spin /pay"""
import random
from datetime import datetime, date, timedelta
from pyrogram import filters
from pyrogram.types import Message
from .. import app
from ..database import get_or_create_user, get_user, add_balance, deduct_balance, update_user
from ..rarity import ECONOMY

SPIN_ANIMS = ["🔁 Spinning...","🎯 Aiming...","🎡 Hold tight...","✨ Lucky?","⏳ Wait for it..."]
REWARD_TIERS  = {"🥉 Minor":(100,300),"🥈 Decent":(400,1000),"🥇 Big":(1200,3000),"💎 JACKPOT":(5000,15000)}
PRIZE_WEIGHTS = ["🥉 Minor"]*45 + ["🥈 Decent"]*30 + ["🥇 Big"]*20 + ["💎 JACKPOT"]*5


@app.on_message(filters.command("daily"))
async def cmd_daily(_, message: Message):
    user = message.from_user
    await get_or_create_user(user.id, user.username or "", user.first_name or "")
    doc  = await get_user(user.id)
    today     = date.today()
    last_d    = doc.get("last_daily")
    streak    = doc.get("daily_streak", 0)
    if last_d:
        last_date = last_d.date() if hasattr(last_d,"date") else last_d
        if last_date == today:
            nxt  = datetime.combine(today+timedelta(days=1), datetime.min.time())
            diff = nxt - datetime.utcnow()
            h, m = int(diff.total_seconds()//3600), int((diff.total_seconds()%3600)//60)
            return await message.reply_text(f"⏰ Already claimed! Come back in **{h}h {m}m**.")
        elif last_date == today-timedelta(days=1): streak = min(streak+1, ECONOMY["daily_streak_max"])
        else: streak = 1
    else: streak = 1
    base  = ECONOMY["daily_base"]
    bonus = ECONOMY["daily_streak_bonus"] * (streak-1)
    total = base+bonus
    await add_balance(user.id, total)
    await update_user(user.id, {"$set": {"last_daily": datetime.utcnow(), "daily_streak": streak}})
    bar = "🔥"*streak + "⬜"*(ECONOMY["daily_streak_max"]-streak)
    await message.reply_text(
        f"🎁 **Daily Reward!**\n\n💰 Base: **{base:,}**\n"
        f"🔥 Streak bonus: **+{bonus:,}** (Day {streak})\n"
        f"✨ Total: **{total:,}** kakera\n\nStreak: {bar} `{streak}/{ECONOMY['daily_streak_max']}`"
    )


@app.on_message(filters.command("spin"))
async def cmd_spin(_, message: Message):
    user = message.from_user
    await get_or_create_user(user.id, user.username or "", user.first_name or "")
    doc  = await get_user(user.id)
    now  = datetime.utcnow()
    last = doc.get("last_spin")
    if last and (now-last).total_seconds() < ECONOMY["spin_cooldown"]:
        rem  = int(ECONOMY["spin_cooldown"]-(now-last).total_seconds())
        m, s = divmod(rem, 60)
        return await message.reply_text(f"⏳ **Cooldown!** Try in **{m}m {s}s**.")
    anim   = await message.reply_text(random.choice(SPIN_ANIMS))
    prize  = random.choice(PRIZE_WEIGHTS)
    lo, hi = REWARD_TIERS[prize]
    amount = random.randint(lo, hi)
    await add_balance(user.id, amount)
    await update_user(user.id, {"$set": {"last_spin": now}})
    await anim.delete()
    await message.reply_text(
        f"🎉 **{user.first_name}'s Spin Result!**\n━━━━━━━━━━━━━━━━━━━\n"
        f"🏅 **{prize}**\n💰 Won: `{amount:,}` kakera\n━━━━━━━━━━━━━━━━━━━\nBack in 1 hour!"
    )


@app.on_message(filters.command("pay"))
async def cmd_pay(_, message: Message):
    if not message.reply_to_message:
        return await message.reply_text("Reply to a user and use `/pay <amount>`")
    args = message.command
    if len(args) < 2: return await message.reply_text("Usage: `/pay <amount>`")
    try:
        amount = int(args[1]); assert amount > 0
    except Exception: return await message.reply_text("❌ Invalid amount.")
    sender = message.from_user; target = message.reply_to_message.from_user
    if sender.id == target.id or target.is_bot: return await message.reply_text("❌ Invalid target.")
    if not await deduct_balance(sender.id, amount): return await message.reply_text("❌ Insufficient kakera.")
    await add_balance(target.id, amount)
    await message.reply_text(f"✅ **{sender.first_name}** sent **{amount:,}** kakera to **{target.first_name}**! 💰")
