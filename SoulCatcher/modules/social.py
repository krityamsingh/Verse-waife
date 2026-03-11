"""SoulCatcher/modules/social.py — /marry /propose /epropose /basket"""
import asyncio, random, time
from datetime import datetime, timedelta
from pyrogram import filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from .. import app
from ..database import get_or_create_user, get_user, add_balance, deduct_balance, get_balance, add_to_harem, get_random_character, add_xp
from ..rarity import ECONOMY

# ── MARRY ─────────────────────────────────────────────────────────────────────

_marry_cds:     dict[int,float] = {}
_marry_streaks: dict[int,int]   = {}

MARRY_SUCCESS = [
    "**{mention}** and **{name}** from *{anime}* are now together under the stars! 💞",
    "**{mention}** got a yes from **{name}**! True love found! 🌸",
    "**{mention}** and **{name}** walked into the sunset together. Love wins! 🌅",
    "A shooting star — **{name}** from *{anime}* said yes to **{mention}**! 💖",
]
MARRY_FAIL = [
    "**{name}** from *{anime}* turned **{mention}** down gently... 💔",
    "**{mention}**, **{name}** sees you more as a friend. 🙃",
    "**{name}** smiled and walked away from **{mention}**... 😢",
]
STREAK_MSGS = {
    5:  "🔥 **5 marriages!** You're becoming a romance legend!",
    10: "💫 **10 streak!** The anime world loves you!",
    20: "👑 **20 streak!** You are the ultimate soul collector!",
}

async def _marry_char():
    for r in ["rare", "cosmos", "common"]:
        c = await get_random_character(r)
        if c: return c
    return None

async def _delete_after(msg, delay=600):
    await asyncio.sleep(delay)
    try: await msg.delete()
    except Exception: pass

@app.on_message(filters.command("marry"))
async def cmd_marry(_, message: Message):
    user = message.from_user; uid = user.id; now = time.time()
    remaining = 60-(now-_marry_cds.get(uid,0))
    if remaining>0:
        m = await message.reply_text(f"⏳ Wait **{int(remaining)}s** before proposing again!")
        asyncio.create_task(_delete_after(message)); asyncio.create_task(_delete_after(m)); return
    _marry_cds[uid] = now
    roll = random.randint(1,6)
    if roll in [1,3,6]:
        char = await _marry_char()
        if not char:
            m = await message.reply_text("🌌 No eligible characters. Try later!")
            asyncio.create_task(_delete_after(message)); asyncio.create_task(_delete_after(m)); return
        await add_to_harem(uid, char)
        from ..database import update_user
        await update_user(uid, {"$inc":{"marriage_count":1,"total_married":1}})
        _marry_streaks[uid] = _marry_streaks.get(uid,0)+1
        streak = _marry_streaks[uid]
        caption = random.choice(MARRY_SUCCESS).format(mention=user.mention,name=char["name"],anime=char.get("anime","?"))
        media = char.get("img_url") or char.get("video_url")
        pm = await (message.reply_photo(media,caption=caption) if char.get("img_url") else message.reply_text(caption))
        asyncio.create_task(_delete_after(pm)); asyncio.create_task(_delete_after(message))
        if streak in STREAK_MSGS:
            sm = await message.reply_text(f"{user.mention} {STREAK_MSGS[streak]}")
            asyncio.create_task(_delete_after(sm))
    else:
        _marry_streaks[uid] = 0
        char = await _marry_char()
        fail = random.choice(MARRY_FAIL).format(mention=user.mention,name=char["name"] if char else "?",anime=char.get("anime","?") if char else "?")
        fm = await message.reply_text(fail)
        asyncio.create_task(_delete_after(fm)); asyncio.create_task(_delete_after(message))


# ── PROPOSE ───────────────────────────────────────────────────────────────────

_propose_cds:      dict[int,datetime] = {}
_propose_attempts: dict[int,dict]     = {}
_active_proposals: dict[int,dict]     = {}

PROPOSE_CD = timedelta(minutes=5)
LOVE_SUCCESS = [
    "✨ **{name} blushed deeply...** *\"I've been waiting for you\"* ❤️",
    "💫 **{name}'s eyes sparkled...** *\"I accept your heart\"* 💞",
    "🌸 **Petals swirled as {name} whispered...** *\"Yes, forever\"* 💍",
    "🌠 **{name} kissed your cheek...** *\"My answer is yes\"* 💘",
]
LOVE_FAIL = [
    "🍂 **{name} looked away...** *\"My heart belongs to another\"* 💔",
    "🌧️ **{name} shook their head...** *\"Not this time\"* ☔",
    "❄️ **\"You deserve better\"** {name} said before disappearing... 🌨️",
]

@app.on_message(filters.command("propose"))
async def cmd_propose(_, message: Message):
    user = message.from_user; uid = user.id; now = datetime.now()
    if uid in _active_proposals:
        return await message.reply_text("🌹 **Finish your current encounter first!**")
    if uid in _propose_cds:
        rem = PROPOSE_CD-(now-_propose_cds[uid])
        if rem.total_seconds()>0:
            m,s = divmod(int(rem.total_seconds()),60)
            return await message.reply_text(f"⏳ **Rest your heart...** `{m}m {s}s`")
    char = await get_random_character("cosmos") or await get_random_character("rare") or await get_random_character("common")
    if not char: return await message.reply_text("🌌 No candidates found. Try later!")
    _active_proposals[uid] = char
    _propose_attempts.setdefault(uid,{"date":now.date(),"count":0})
    if _propose_attempts[uid]["date"]!=now.date(): _propose_attempts[uid]={"date":now.date(),"count":0}
    caption = (f"🌠 **A Fateful Encounter...**\n\n💖 **{char['name']}** stands before you\n"
               f"_{char.get('anime','Unknown')}_\n\n**Will you confess?**")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💌 Confess", callback_data=f"propose:yes:{uid}")],
        [InlineKeyboardButton("🌌 Walk Away", callback_data=f"propose:no:{uid}")],
    ])
    if char.get("img_url"): await message.reply_photo(char["img_url"],caption=caption,reply_markup=kb)
    else:                   await message.reply_text(caption,reply_markup=kb)

@app.on_callback_query(filters.regex(r"^propose:"))
async def propose_cb(_, cb):
    _,action,uid_str = cb.data.split(":"); uid = int(uid_str)
    if cb.from_user.id!=uid: return await cb.answer("🔞 Not your encounter!", show_alert=True)
    char = _active_proposals.pop(uid,None)
    if not char: return await cb.message.edit_caption("⏳ The moment has passed...")
    _propose_cds[uid] = datetime.now()
    if action=="no":
        try: await cb.message.delete()
        except Exception: pass
        return await cb.message.reply_text("🌫️ You walked away silently...")
    _propose_attempts[uid]["count"]+=1
    guaranteed = _propose_attempts[uid]["count"]>=3
    outcome    = "yes" if guaranteed else random.choices(["yes","no"],weights=[65,35])[0]
    name       = char.get("name","?")
    if outcome=="yes":
        await add_to_harem(uid,char)
        _propose_attempts[uid]["count"]=0
        resp = random.choice(LOVE_SUCCESS).format(name=name)
        resp += f"\n\n💞 **{name} added to your collection!**"
    else:
        resp = random.choice(LOVE_FAIL).format(name=name)
        resp += "\n\n💫 *3rd attempt is guaranteed to succeed!*"
    try: await cb.message.edit_caption(resp, reply_markup=None)
    except Exception: pass

@app.on_message(filters.command("epropose"))
async def cmd_epropose(_, message: Message):
    if _active_proposals.pop(message.from_user.id,None): await message.reply_text("🌪️ Encounter cancelled.")
    else: await message.reply_text("🌌 No active encounter.")


# ── BASKETBALL ────────────────────────────────────────────────────────────────

_basket_cds: dict[int,float] = {}

@app.on_message(filters.command(["basket","basketball"]))
async def cmd_basket(client, message: Message):
    uid = message.from_user.id; now = time.time()
    last = _basket_cds.get(uid)
    if last and now-last < ECONOMY["basket_cooldown"]:
        wait = int(ECONOMY["basket_cooldown"]-(now-last))
        return await message.reply_text(f"⏳ **Too fast!** Wait `{wait}s`・o・")
    try:
        bet = int(message.command[1])
    except (IndexError,ValueError):
        return await message.reply_text("❌ Use: `/basket <amount>`")
    balance = await get_balance(uid)
    if balance is None: return await message.reply_text("⚠️ Use /start first.")
    min_bet = max(50, int(balance*ECONOMY["basket_min_bet_pct"]))
    if bet<min_bet:  return await message.reply_text(f"💢 Min bet: `{min_bet}` coins")
    if bet>balance:  return await message.reply_text("💸 Not enough coins!")
    dice  = await client.send_dice(message.chat.id,"🏀")
    val   = dice.dice.value
    _basket_cds[uid] = now
    if val==6:
        win = bet*2; await add_balance(uid,win); await add_xp(uid,5)
        await message.reply_text(f"✨ **SUPER SLAM DUNK!!**\n╰┈➤ 🏆 +`{win:,}` coins\n╰┈➤ 🌟 +5 xp\n\nLegendary! (•̀ᴗ•́)و")
    elif val in [4,5]:
        win = int(bet*1.5); await add_balance(uid,win); await add_xp(uid,3)
        await message.reply_text(f"🎯 **Nice Shot!**\n╰┈➤ 💰 +`{win:,}` coins\n╰┈➤ ✨ +3 xp\n\nKeep going! ٩(◕‿◕｡)۶")
    elif val in [2,3]:
        loss = int(bet*0.5); await deduct_balance(uid,loss); await add_xp(uid,-2)
        await message.reply_text(f"💢 **Close Miss!**\n╰┈➤ 🩹 -`{loss:,}` coins\n╰┈➤ 📉 -2 xp\n\nNext time! (╥﹏╥)")
    else:
        await deduct_balance(uid,bet); await add_xp(uid,-3)
        await message.reply_text(f"💀 **AIRBALL!**\n╰┈➤ ☠️ -`{bet:,}` coins\n╰┈➤ ❌ -3 xp\n\nDisaster lol (≧﹏≦)")
