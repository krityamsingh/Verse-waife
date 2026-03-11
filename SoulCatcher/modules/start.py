import random, logging
from pyrogram import filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from .. import app
from ..config import START_VIDEO_URLS, LOG_CHANNEL_ID, BOT_NAME, SUPPORT_GROUP
from ..database import get_or_create_user, track_group

log = logging.getLogger("SoulCatcher.start")

# ── DM /start ─────────────────────────────────────────────────────────────────

DM_TEXT = """\
🌸 **Welcome to SoulCatcher!**

Collect anime souls, build your harem & rule the leaderboard.

Press **Help** to see all commands 👇
"""

def _dm_kb(bot_username):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📚 Help", callback_data="help:1")],
        [InlineKeyboardButton("💬 Support", url=f"https://t.me/{SUPPORT_GROUP}")],
        [InlineKeyboardButton("➕ Add to Group", url=f"https://t.me/{bot_username}?startgroup=true")],
    ])

@app.on_message(filters.command("start") & filters.private)
async def start_dm(client, message: Message):
    user = message.from_user
    try:
        await get_or_create_user(user.id, user.username or "", user.first_name or "", user.last_name or "")
    except Exception:
        pass

    bot_me = await client.get_me()
    kb = _dm_kb(bot_me.username)

    sent = False
    if START_VIDEO_URLS:
        try:
            await client.send_video(
                message.chat.id,
                random.choice(START_VIDEO_URLS),
                caption=DM_TEXT,
                reply_markup=kb,
                parse_mode=enums.ParseMode.MARKDOWN,
            )
            sent = True
        except Exception as e:
            log.warning(f"send_video failed: {e}")

    if not sent:
        await message.reply_text(DM_TEXT, reply_markup=kb, parse_mode=enums.ParseMode.MARKDOWN)

    if LOG_CHANNEL_ID:
        try:
            name = user.first_name or f"User#{user.id}"
            await client.send_message(LOG_CHANNEL_ID, f"🟢 **/start** — {name} `{user.id}`", parse_mode=enums.ParseMode.MARKDOWN)
        except Exception:
            pass


# ── GC /start ─────────────────────────────────────────────────────────────────

@app.on_message(filters.command("start") & filters.group)
async def start_gc(client, message: Message):
    bot_me = await client.get_me()
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🌸 DM me", url=f"https://t.me/{bot_me.username}?start=start"),
        InlineKeyboardButton("💬 Support", url=f"https://t.me/{SUPPORT_GROUP}"),
    ]])
    await message.reply_text(
        f"🌸 **{BOT_NAME} is active!** Spawns every 15 messages — press ❤️ to claim!",
        reply_markup=kb,
        parse_mode=enums.ParseMode.MARKDOWN,
    )
    await track_group(message.chat.id, getattr(message.chat, "title", ""))


# ── Help pages ────────────────────────────────────────────────────────────────

HELP_PAGES = {
    "1": (
        "📚 **Help (1/4) — Spawns & Collection**\n\n"
        "`/drop` — Force a spawn\n"
        "`/harem` — Browse your collection\n"
        "`/view <ID>` — View character card\n"
        "`/setfav <ID>` — Mark favourite ⭐\n"
        "`/burn <ID>` — Sell for kakera 🔥\n"
        "`/sort <rarity|name|anime|recent>` — Sort harem\n"
    ),
    "2": (
        "📚 **Help (2/4) — Economy**\n\n"
        "`/daily` — Daily kakera\n"
        "`/bal` — Balance\n"
        "`/spin` — Spin wheel (1h cooldown)\n"
        "`/pay <amount>` — Pay a user (reply)\n"
        "`/sell <ID> <price>` — List on market\n"
        "`/buy <listingID>` — Buy from market\n"
        "`/market [rarity]` — Browse listings\n"
    ),
    "3": (
        "📚 **Help (3/4) — Social**\n\n"
        "`/trade <myID> <theirID>` — Trade (reply)\n"
        "`/gift <ID>` — Gift character (reply)\n"
        "`/marry` — Marry a character\n"
        "`/basket <bet>` — 🏀 Bet with dice\n"
        "`/wish <charID>` — Add to wishlist\n"
        "`/wishlist` — View wishlist\n"
    ),
    "4": (
        "📚 **Help (4/4) — Rankings**\n\n"
        "`/profile` — Profile card\n"
        "`/rank` — Your global rank\n"
        "`/top` — Top 10 collectors\n"
        "`/richest` — Richest 10 players\n"
        "`/rarityinfo` — Rarity table\n\n"
        "**Tiers:** ⚫→🔵→🌌→🔥→💎→🔴→✨"
    ),
}

def _help_kb(page):
    pages = list(HELP_PAGES.keys())
    idx = pages.index(page)
    nav = []
    if idx > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"help:{pages[idx-1]}"))
    nav.append(InlineKeyboardButton(f"{int(page)}/{len(pages)}", callback_data="noop"))
    if idx < len(pages) - 1:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"help:{pages[idx+1]}"))
    return InlineKeyboardMarkup([nav, [InlineKeyboardButton("🏠 Home", callback_data="help:home")]])

@app.on_callback_query(filters.regex(r"^help:"))
async def help_cb(client, cb: CallbackQuery):
    page = cb.data.split(":")[1]
    if page == "home":
        bot_me = await client.get_me()
        try:
            await cb.message.edit_caption(DM_TEXT, reply_markup=_dm_kb(bot_me.username), parse_mode=enums.ParseMode.MARKDOWN)
        except Exception:
            await cb.message.edit_text(DM_TEXT, reply_markup=_dm_kb(bot_me.username), parse_mode=enums.ParseMode.MARKDOWN)
        return await cb.answer()
    if page not in HELP_PAGES:
        return await cb.answer()
    try:
        await cb.message.edit_text(HELP_PAGES[page], reply_markup=_help_kb(page), parse_mode=enums.ParseMode.MARKDOWN)
    except Exception:
        pass
    await cb.answer()

@app.on_callback_query(filters.regex("^noop$"))
async def noop(_, cb): await cb.answer()
