"""SoulCatcher/modules/start.py — /start, help pages, bot-added logging.

FIXES APPLIED:
  [BUG-4] Always pass parse_mode=MARKDOWN to every send call so Telegram
          never falls back to an ambiguous default.
  [BUG-5] _safe_mention() escapes Markdown special chars in user's first_name
          so names like "Mike_Dev" or "[Bot]" never break entity parsing.
  [BUG-6] Sticker is sent AFTER the main welcome message succeeds so a bad
          sticker ID can never mask the real delivery error.
  [BUG-7] help_cb "Home" path now uses _safe_mention() instead of raw
          user.mention, matching the same fix applied to start_dm.
  [MISC]  Log message in start_dm now uses the plain (unformatted) name so
          special chars don't corrupt the log channel message either.
  [MISC]  start_gc reply_photo also gets explicit parse_mode.
  [MISC]  on_member_update log message uses plain name, not user.mention.
"""

import re
import time
import random
import logging
from datetime import datetime

from pyrogram import filters, enums
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ChatMemberUpdated,
)

from .. import app
from ..config import (
    START_IMAGE_URL,
    START_VIDEO_URLS,
    START_STICKER_ID,
    LOG_CHANNEL_ID,
    BOT_NAME,
    SUPPORT_GROUP,
    UPDATE_CHANNEL,
)
from ..database import get_or_create_user, track_group

log = logging.getLogger("SoulCatcher.start")

_start_time = time.time()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _uptime() -> str:
    s = int(time.time() - _start_time)
    h, m, s = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}h {m}m {s}s"


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")


# FIX [BUG-5 / BUG-7]: Escape Telegram Markdown v1 special chars that appear
# inside user-supplied strings (names, titles).  Without this, a first_name
# like "Mike_Dev" produces an unclosed italic tag and Telegram rejects the
# whole message with "can't parse entities", which the outer try/except then
# catches and replies with "Something went wrong."
_MD_SPECIAL = re.compile(r"([_*`\[\]])")

def _esc(text: str) -> str:
    """Escape Markdown v1 special characters in a plain-text string."""
    return _MD_SPECIAL.sub(r"\\\1", str(text))


def _safe_mention(user) -> str:
    """
    Return a safe Markdown mention for *user*.
    Uses the display name if available, otherwise falls back to the numeric ID.
    The name is escaped so special chars don't corrupt the parse tree.
    """
    if user and user.first_name:
        name = _esc(user.first_name)
        return f"[{name}](tg://user?id={user.id})"
    if user:
        return f"User#{user.id}"
    return "Unknown"


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


def _dm_kb(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add to Group", url=f"https://t.me/{bot_username}?startgroup=true")],
        [
            InlineKeyboardButton("💬 Support", url=f"https://t.me/{SUPPORT_GROUP}"),
            InlineKeyboardButton("📢 Updates", url=f"https://t.me/{UPDATE_CHANNEL}"),
        ],
        [InlineKeyboardButton("📚 Help & Commands", callback_data="help:1")],
    ])


@app.on_message(filters.command("start") & filters.private)
async def start_dm(client, message: Message):
    try:
        user = message.from_user

        # Register user in DB — failure must never block the welcome message.
        try:
            await get_or_create_user(
                user.id,
                user.username or "",
                user.first_name or "",
                user.last_name or "",
            )
        except Exception as db_err:
            log.warning(f"DB register failed for {user.id}: {db_err}")

        bot_me  = await client.get_me()
        # FIX [BUG-5]: use _safe_mention() so names with _, *, ` or [ don't
        # break Telegram's Markdown parser and silently kill the whole send.
        mention = _safe_mention(user)
        text    = DM_TEXT.format(mention=mention, bot=_esc(BOT_NAME))
        kb      = _dm_kb(bot_me.username)

        # ── Attempt to send welcome with media ────────────────────────────
        # FIX [BUG-4]: always specify parse_mode explicitly on every call.
        # Without this, Pyrogram 2.x uses its client-level default which can
        # differ between send_photo/send_video and reply_text, causing
        # inconsistent behaviour.

        sent = False

        if START_IMAGE_URL:
            try:
                await client.send_photo(
                    message.chat.id,
                    START_IMAGE_URL,
                    caption=text,
                    reply_markup=kb,
                    parse_mode=enums.ParseMode.MARKDOWN,
                )
                sent = True
            except Exception as e:
                log.warning(f"send_photo failed for {user.id}: {e}")

        if not sent and START_VIDEO_URLS:
            try:
                await client.send_video(
                    message.chat.id,
                    random.choice(START_VIDEO_URLS),
                    caption=text,
                    reply_markup=kb,
                    parse_mode=enums.ParseMode.MARKDOWN,
                )
                sent = True
            except Exception as e:
                log.warning(f"send_video failed for {user.id}: {e}")

        # Plain-text fallback — always attempted when media paths fail.
        if not sent:
            await message.reply_text(
                text,
                reply_markup=kb,
                parse_mode=enums.ParseMode.MARKDOWN,
            )

        # FIX [BUG-6]: Send sticker AFTER the main welcome message has been
        # delivered successfully.  If the sticker file_id is stale or wrong,
        # the error is isolated and doesn't prevent the user from seeing the
        # welcome text.
        if START_STICKER_ID:
            try:
                await message.reply_sticker(START_STICKER_ID)
            except Exception as e:
                log.warning(f"reply_sticker failed: {e}")

        # Log to channel using plain (unformatted) name so special chars in
        # the user's name don't corrupt the log message either.
        if LOG_CHANNEL_ID:
            plain_name = user.first_name or f"User#{user.id}"
            try:
                await client.send_message(
                    LOG_CHANNEL_ID,
                    f"🟢 **/start DM**\n{_esc(plain_name)} `{user.id}`\n{_now()}",
                    parse_mode=enums.ParseMode.MARKDOWN,
                )
            except Exception as e:
                log.warning(f"Log channel send failed: {e}")

    except Exception as e:
        log.exception(f"start_dm crashed for user {getattr(message.from_user, 'id', '?')}: {e}")
        try:
            await message.reply_text(
                "Something went wrong. Please try /start again!",
                parse_mode=enums.ParseMode.MARKDOWN,
            )
        except Exception:
            pass


# ── GROUP WELCOME ─────────────────────────────────────────────────────────────

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
    text   = GC_TEXT.format(bot=_esc(BOT_NAME), uptime=_uptime())
    kb     = InlineKeyboardMarkup([[
        InlineKeyboardButton("🌸 DM me",    url=f"https://t.me/{bot_me.username}?start=start"),
        InlineKeyboardButton("💬 Support",  url=f"https://t.me/{SUPPORT_GROUP}"),
    ]])

    # FIX [BUG-4]: explicit parse_mode on group reply as well.
    try:
        await message.reply_photo(
            START_IMAGE_URL,
            caption=text,
            reply_markup=kb,
            parse_mode=enums.ParseMode.MARKDOWN,
        )
    except Exception:
        await message.reply_text(
            text,
            reply_markup=kb,
            parse_mode=enums.ParseMode.MARKDOWN,
        )

    await track_group(message.chat.id, getattr(message.chat, "title", ""))


# ── BOT ADDED LOG ─────────────────────────────────────────────────────────────

@app.on_chat_member_updated()
async def on_member_update(client, update: ChatMemberUpdated):
    try:
        old_s = getattr(update.old_chat_member, "status", None)
        new_s = getattr(update.new_chat_member, "status", None)

        if old_s in ("left", "kicked", None) and new_s in ("member", "administrator"):
            chat  = update.chat
            actor = update.from_user

            await track_group(chat.id, getattr(chat, "title", ""))

            if LOG_CHANNEL_ID:
                try:
                    inv = await client.export_chat_invite_link(chat.id)
                except Exception:
                    inv = "N/A"

                # FIX [MISC]: use plain escaped name rather than user.mention
                # so special chars in the actor's name don't break the log msg.
                actor_display = _esc(actor.first_name) if actor and actor.first_name else "Unknown"
                chat_title    = _esc(getattr(chat, "title", str(chat.id)))

                await client.send_message(
                    LOG_CHANNEL_ID,
                    (
                        f"🔔 **Added to chat**\n"
                        f"{chat_title}\n"
                        f"`{chat.id}`\n"
                        f"By: [{actor_display}](tg://user?id={actor.id if actor else 0})\n"
                        f"{inv}\n"
                        f"{_now()}"
                    ),
                    parse_mode=enums.ParseMode.MARKDOWN,
                )
    except Exception as e:
        log.warning(f"on_member_update error: {e}")


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
        "`/toprarity <n>` — Top by rarity\n"
        "`/richest` — Richest 10 players\n"
        "`/rarityinfo` — Full rarity table\n"
        "`/event` — Current game mode\n\n"
        "**Tiers:** ⚫→🔵→🌌→🔥→💎→🔴→✨\n"
        "**Subs:** 🌸 Seasonal · 🔮 Limited · 🎠 Cartoon"
    ),
}


def _help_kb(page: str) -> InlineKeyboardMarkup:
    pages = list(HELP_PAGES.keys())
    idx   = pages.index(page)
    nav   = []
    if idx > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"help:{pages[idx - 1]}"))
    nav.append(InlineKeyboardButton(f"{int(page)}/{len(pages)}", callback_data="noop"))
    if idx < len(pages) - 1:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"help:{pages[idx + 1]}"))
    return InlineKeyboardMarkup([nav, [InlineKeyboardButton("🏠 Home", callback_data="help:home")]])


@app.on_callback_query(filters.regex(r"^help:"))
async def help_cb(client, cb: CallbackQuery):
    page = cb.data.split(":")[1]

    if page == "home":
        bot_me = await client.get_me()
        # FIX [BUG-7]: _safe_mention() here for the same reason as start_dm —
        # raw user.mention breaks when the user's name has Markdown chars.
        mention = _safe_mention(cb.from_user)
        text    = DM_TEXT.format(mention=mention, bot=_esc(BOT_NAME))
        kb      = _dm_kb(bot_me.username)
        try:
            await cb.message.edit_caption(
                text,
                reply_markup=kb,
                parse_mode=enums.ParseMode.MARKDOWN,
            )
        except Exception:
            await cb.message.edit_text(
                text,
                reply_markup=kb,
                parse_mode=enums.ParseMode.MARKDOWN,
            )
        return await cb.answer()

    if page not in HELP_PAGES:
        return await cb.answer()

    try:
        await cb.message.edit_text(
            HELP_PAGES[page],
            reply_markup=_help_kb(page),
            parse_mode=enums.ParseMode.MARKDOWN,
        )
    except Exception as e:
        log.warning(f"help_cb edit_text failed: {e}")

    await cb.answer()


@app.on_callback_query(filters.regex("^noop$"))
async def noop(_, cb: CallbackQuery):
    await cb.answer()
