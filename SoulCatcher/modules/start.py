"""SoulCatcher/modules/start.py — /start, help pages, bot-added logging."""
import time, random
from datetime import datetime
from pyrogram import filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ChatMemberUpdated
from .. import app
from ..config import START_IMAGE_URL, START_VIDEO_URLS, START_STICKER_ID, LOG_CHANNEL_ID, BOT_NAME, SUPPORT_GROUP, UPDATE_CHANNEL
from ..database import get_or_create_user, track_group

_start_time = time.time()

def _uptime():
    s = int(time.time()-_start_time); h,m,s = s//3600,(s%3600)//60,s%60
    return f"{h}h {m}m {s}s"

def _now(): return datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")


# ── DM WELCOME ────────────────────────────────────────────────────────────────

DM_TEXT = """\
🌸 **Welcome to {bot}, {mention}!**
━━━━━━━━━━━━━━━━━━━━

I'm your anime soul-collecting bot!

**⚡ How to play:**
✦ Characters **auto-spawn** in groups every ~15 messages
✦ Press ❤️ to **claim** before the timer runs out
✦ Build your **collection**, trade, duel & dominate the leaderboard!

**7 Rarity Tiers:**
⚫ Common → 🔵 Rare → 🌌 Cosmos → 🔥 Infernal
💎 Crystal → 🔴 Mythic → ✨ **ETERNAL**

✦ Tier 5 Crystal  → 🌸 **Seasonal** sub-rarity
✦ Tier 6 Mythic   → 🔮 **Limited Edition** sub-rarity
✦ Tier 7 Eternal  → 🎠 **Cartoon** sub-rarity _(video-only, rarest ever!)_

━━━━━━━━━━━━━━━━━━━━
You've been registered! Start exploring 👇
"""

def _dm_kb(bot_username):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add to Group", url=f"https://t.me/{bot_username}?startgroup=true")],
        [
            InlineKeyboardButton("💬 Support",  url=f"https://t.me/{SUPPORT_GROUP}"),
            InlineKeyboardButton("📢 Updates",  url=f"https://t.me/{UPDATE_CHANNEL}"),
        ],
        [InlineKeyboardButton("📚 Help & Commands", callback_data="help:1")],
    ])

@app.on_message(filters.command("start") & filters.private)
async def start_dm(client, message: Message):
    try:
        user = message.from_user
        try:
            await get_or_create_user(user.id, user.username or "", user.first_name or "", user.last_name or "")
        except Exception:
            pass  # DB failure should not block the welcome message

        bot_me = await client.get_me()
        mention = user.mention if user.first_name else f"User#{user.id}"
        text    = DM_TEXT.format(mention=mention, bot=BOT_NAME)
        kb      = _dm_kb(bot_me.username)

        if START_STICKER_ID:
            try: await message.reply_sticker(START_STICKER_ID)
            except Exception: pass

        sent = False
        if START_IMAGE_URL:
            try:
                await client.send_photo(message.chat.id, START_IMAGE_URL, caption=text, reply_markup=kb)
                sent = True
            except Exception:
                pass

        if not sent and START_VIDEO_URLS:
            try:
                await client.send_video(message.chat.id, random.choice(START_VIDEO_URLS), caption=text, reply_markup=kb)
                sent = True
            except Exception:
                pass

        if not sent:
            await message.reply_text(text, reply_markup=kb)

        if LOG_CHANNEL_ID:
            try: await client.send_message(LOG_CHANNEL_ID, f"🟢 **/start DM**\n{mention} `{user.id}`\n{_now()}")
            except Exception: pass

    except Exception as e:
        import logging
        logging.getLogger("SoulCatcher.start").exception(f"start_dm crashed: {e}")
        try: await message.reply_text("Something went wrong. Please try /start again!")
        except Exception: pass


# ── GC WELCOME ────────────────────────────────────────────────────────────────

GC_TEXT = """\
🌸 **{bot} is now active in this group!**
━━━━━━━━━━━━━━━
📦 Spawns every **15 messages**  |  ⏱ Uptime: `{uptime}`
━━━━━━━━━━━━━━━
Type `/drop` to force a character spawn!
"""

@app.on_message(filters.command("start") & filters.group)
async def start_gc(client, message: Message):
    bot_me = await client.get_me()
    text   = GC_TEXT.format(bot=BOT_NAME, uptime=_uptime())
    kb     = InlineKeyboardMarkup([[
        InlineKeyboardButton("🌸 DM me", url=f"https://t.me/{bot_me.username}?start=start"),
        InlineKeyboardButton("💬 Support", url=f"https://t.me/{SUPPORT_GROUP}"),
    ]])
    try:    await message.reply_photo(START_IMAGE_URL, caption=text, reply_markup=kb)
    except Exception: await message.reply_text(text, reply_markup=kb)
    await track_group(message.chat.id, getattr(message.chat,"title",""))


# ── BOT ADDED LOG ─────────────────────────────────────────────────────────────

@app.on_chat_member_updated()
async def on_member_update(client, update: ChatMemberUpdated):
    try:
        old_s = getattr(update.old_chat_member,"status",None)
        new_s = getattr(update.new_chat_member,"status",None)
        if old_s in ("left","kicked",None) and new_s in ("member","administrator"):
            chat  = update.chat; actor = update.from_user
            await track_group(chat.id, getattr(chat,"title",""))
            if LOG_CHANNEL_ID:
                try:    inv = await client.export_chat_invite_link(chat.id)
                except: inv = "N/A"
                await client.send_message(LOG_CHANNEL_ID,
                    f"🔔 **Added to chat**\n{getattr(chat,'title',chat.id)}\n`{chat.id}`\nBy: {actor.mention if actor else '?'}\n{inv}\n{_now()}")
    except Exception: pass


# ── HELP PAGES ────────────────────────────────────────────────────────────────

HELP_PAGES = {
    "1": (
        "📚 **SoulCatcher Help (1/4) — Spawns & Collection**\n\n"
        "`/drop` — Force a spawn (group cooldown applies)\n"
        "Auto-spawns every 15 messages — press ❤️ to claim!\n\n"
        "`/harem` — Browse your collection\n"
        "`/view <ID>` — View character card\n"
        "`/setfav <ID>` — Mark favourite ⭐\n"
        "`/burn <ID>` — Sell for kakera 🔥\n"
        "`/sort <rarity|name|anime|recent>` — Sort harem\n"
    ),
    "2": (
        "📚 **SoulCatcher Help (2/4) — Economy**\n\n"
        "`/daily` — Daily kakera (streak bonuses!)\n"
        "`/bal` — Balance check\n"
        "`/spin` — Spin wheel (1h cooldown)\n"
        "`/pay <amount>` — Pay a user (reply)\n"
        "`/shop` — Buy boosts & items\n\n"
        "`/sell <ID> <price>` — List on market\n"
        "`/buy <listingID>` — Buy from market\n"
        "`/market [rarity]` — Browse listings\n"
    ),
    "3": (
        "📚 **SoulCatcher Help (3/4) — Social & Games**\n\n"
        "`/trade <myID> <theirID>` — Trade (reply to user)\n"
        "`/gift <ID>` — Gift character (reply to user)\n"
        "`/marry` — Marry a random character!\n"
        "`/propose` — Propose (3rd attempt guaranteed!)\n"
        "`/basket <bet>` — 🏀 Bet with dice\n"
        "`/wish <charID>` — Add to wishlist\n"
        "`/wishlist` — View your wishlist\n"
    ),
    "4": (
        "📚 **SoulCatcher Help (4/4) — Rankings & Admin**\n\n"
        "`/profile` — Full profile card\n"
        "`/status` — Detailed stats\n"
        "`/rank` — Your global rank\n"
        "`/top` — Top 10 collectors\n"
        "`/toprarity <name>` — Top by rarity\n"
        "`/richest` — Richest 10 players\n"
        "`/rarityinfo` — Full rarity table\n"
        "`/event` — Current game mode\n\n"
        "**Tiers:** ⚫→🔵→🌌→🔥→💎→🔴→✨\n"
        "**Subs:** 🌸 Seasonal · 🔮 Limited · 🎠 Cartoon"
    ),
}

def _help_kb(page):
    pages = list(HELP_PAGES.keys()); idx = pages.index(page)
    nav   = []
    if idx > 0:            nav.append(InlineKeyboardButton("◀️", callback_data=f"help:{pages[idx-1]}"))
    nav.append(InlineKeyboardButton(f"{int(page)}/{len(pages)}", callback_data="noop"))
    if idx < len(pages)-1: nav.append(InlineKeyboardButton("▶️", callback_data=f"help:{pages[idx+1]}"))
    return InlineKeyboardMarkup([nav,[InlineKeyboardButton("🏠 Home", callback_data="help:home")]])

@app.on_callback_query(filters.regex(r"^help:"))
async def help_cb(client, cb: CallbackQuery):
    page = cb.data.split(":")[1]
    if page == "home":
        bot_me = await client.get_me()
        text   = DM_TEXT.format(mention=cb.from_user.mention, bot=BOT_NAME)
        try:    await cb.message.edit_caption(text, reply_markup=_dm_kb(bot_me.username))
        except: await cb.message.edit_text(text,   reply_markup=_dm_kb(bot_me.username))
        return await cb.answer()
    if page not in HELP_PAGES: return await cb.answer()
    try: await cb.message.edit_text(HELP_PAGES[page], reply_markup=_help_kb(page))
    except Exception: pass
    await cb.answer()

@app.on_callback_query(filters.regex("^noop$"))
async def noop(_, cb): await cb.answer()
