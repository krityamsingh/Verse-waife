"""SoulCatcher/modules/start.py — /start, help pages, bot-added logging.

FIXES:
  [DM]  Intro video is now hardcoded to the correct catbox URL.
        Falls back to plain text only if the video send fails.
  [GC]  Entire start_gc handler is wrapped in try/except so any error
        (DB down, permission issue, bad parse) never silently drops the reply.
        track_group() is also individually guarded — a DB hiccup can no
        longer prevent the welcome message from being sent.
  [ALL] Every send call has explicit parse_mode=MARKDOWN.
        User names are Markdown-escaped so special chars (_*`[]) never
        break entity parsing and silently kill the message.
"""

import re
import time
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
from ..config import LOG_CHANNEL_ID, BOT_NAME, SUPPORT_GROUP, UPDATE_CHANNEL
from ..database import get_or_create_user, track_group

log = logging.getLogger("SoulCatcher.start")

_start_time = time.time()

# ── Hardcoded DM intro video ───────────────────────────────────────────────────
# Change this URL to swap the intro video. Set to None to use text-only welcome.
DM_INTRO_VIDEO = "https://files.catbox.moe/6nqjqk.mp4"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _uptime() -> str:
    s = int(time.time() - _start_time)
    h, m, s = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}h {m}m {s}s"


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")


_MD_SPECIAL = re.compile(r"([_*`\[\]()~>#+\-=|{}.!])")

def _esc(text: str) -> str:
    """Escape MarkdownV2 special chars in user-supplied strings."""
    return _MD_SPECIAL.sub(r"\\\1", str(text))


def _safe_mention(user) -> str:
    """Return a safe MarkdownV2 mention with the display name escaped."""
    if user and user.first_name:
        return f"[{_esc(user.first_name)}](tg://user?id={user.id})"
    if user:
        return f"User\\#{user.id}"
    return "Unknown"


# ── DM WELCOME ────────────────────────────────────────────────────────────────

DM_TEXT = """\
🌸 *Welcome to {bot}, {mention}\!*
━━━━━━━━━━━━━━━━━━━━

I'm your anime soul\-collecting bot\!

*⚡ How to play:*
✦ Characters *auto\-spawn* in groups every \~15 messages
✦ Press ❤️ to *claim* before the timer runs out
✦ Build your *collection*, trade, duel & dominate the leaderboard\!

*7 Rarity Tiers:*
⚫ Common → 🔵 Rare → 🌌 Cosmos → 🔥 Infernal
💎 Crystal → 🔴 Mythic → ✨ *ETERNAL*

✦ Tier 5 Crystal  → 🌸 *Seasonal* sub\-rarity
✦ Tier 6 Mythic   → 🔮 *Limited Edition* sub\-rarity
✦ Tier 7 Eternal  → 🎠 *Cartoon* sub\-rarity _\(video\-only, rarest ever\!\)_

━━━━━━━━━━━━━━━━━━━━
You've been registered\! Start exploring 👇\
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

        # Register user — DB failure must never block the welcome.
        try:
            await get_or_create_user(
                user.id,
                user.username or "",
                user.first_name or "",
                user.last_name or "",
            )
        except Exception as db_err:
            log.warning(f"DB register failed for uid={user.id}: {db_err}")

        bot_me  = await client.get_me()
        mention = _safe_mention(user)
        text    = DM_TEXT.format(mention=mention, bot=_esc(BOT_NAME))
        kb      = _dm_kb(bot_me.username)

        # ── Send intro video (hardcoded URL) ──────────────────────────────
        sent = False
        if DM_INTRO_VIDEO:
            try:
                await client.send_video(
                    message.chat.id,
                    DM_INTRO_VIDEO,
                    caption=text,
                    reply_markup=kb,
                    parse_mode=enums.ParseMode.MARKDOWN_V2,
                )
                sent = True
            except Exception as e:
                log.warning(f"send_video failed for uid={user.id}: {e}")

        # Plain-text fallback if video send fails.
        if not sent:
            await message.reply_text(
                text,
                reply_markup=kb,
                parse_mode=enums.ParseMode.MARKDOWN_V2,
            )

        # Log to channel.
        if LOG_CHANNEL_ID:
            plain = _esc(user.first_name or f"User#{user.id}")
            try:
                await client.send_message(
                    LOG_CHANNEL_ID,
                    f"🟢 */start DM*\n{plain} `{user.id}`\n{_now()}",
                    parse_mode=enums.ParseMode.MARKDOWN_V2,
                )
            except Exception as e:
                log.warning(f"Log channel send failed: {e}")

    except Exception as e:
        log.exception(f"start_dm crashed for uid={getattr(message.from_user, 'id', '?')}: {e}")
        try:
            await message.reply_text("Something went wrong\\. Please try /start again\\!")
        except Exception:
            pass


# ── GROUP WELCOME ─────────────────────────────────────────────────────────────

GC_TEXT = (
    "🌸 *{bot} is now active in this group\\!*\n"
    "━━━━━━━━━━━━━━━\n"
    "📦 Spawns every *15 messages*  \\|  ⏱ Uptime: `{uptime}`\n"
    "━━━━━━━━━━━━━━━\n"
    "Type /drop to force a character spawn\\!"
)

GC_FALLBACK = "🌸 *{bot} is active\\!* Spawns every 15 messages — press ❤️ to claim\\!"


@app.on_message(filters.command("start") & filters.group)
async def start_gc(client, message: Message):
    # FIX: entire handler in try/except — no error can silently swallow the reply.
    try:
        bot_me = await client.get_me()
        text   = GC_TEXT.format(bot=_esc(BOT_NAME), uptime=_uptime())
        kb     = InlineKeyboardMarkup([[
            InlineKeyboardButton("🌸 DM me",   url=f"https://t.me/{bot_me.username}?start=start"),
            InlineKeyboardButton("💬 Support", url=f"https://t.me/{SUPPORT_GROUP}"),
        ]])

        await message.reply_text(
            text,
            reply_markup=kb,
            parse_mode=enums.ParseMode.MARKDOWN_V2,
        )

        # FIX: track_group individually wrapped so DB failure never prevents
        # the welcome message above from reaching the group.
        try:
            await track_group(message.chat.id, getattr(message.chat, "title", ""))
        except Exception as e:
            log.warning(f"track_group failed for chat={message.chat.id}: {e}")

    except Exception as e:
        log.exception(f"start_gc crashed for chat={getattr(message.chat, 'id', '?')}: {e}")
        # Last-resort fallback — minimal message with no DB dependency.
        try:
            await message.reply_text(
                GC_FALLBACK.format(bot=_esc(BOT_NAME)),
                parse_mode=enums.ParseMode.MARKDOWN_V2,
            )
        except Exception:
            pass


# ── BOT ADDED TO GROUP LOG ────────────────────────────────────────────────────

@app.on_chat_member_updated()
async def on_member_update(client, update: ChatMemberUpdated):
    try:
        old_s = getattr(update.old_chat_member, "status", None)
        new_s = getattr(update.new_chat_member, "status", None)

        if old_s in ("left", "kicked", None) and new_s in ("member", "administrator"):
            chat  = update.chat
            actor = update.from_user

            try:
                await track_group(chat.id, getattr(chat, "title", ""))
            except Exception as e:
                log.warning(f"track_group failed on member update: {e}")

            if LOG_CHANNEL_ID:
                try:
                    inv = await client.export_chat_invite_link(chat.id)
                except Exception:
                    inv = "N/A"

                actor_str  = _esc(actor.first_name) if actor and actor.first_name else "Unknown"
                chat_title = _esc(getattr(chat, "title", str(chat.id)))

                try:
                    await client.send_message(
                        LOG_CHANNEL_ID,
                        (
                            f"🔔 *Added to chat*\n"
                            f"{chat_title}\n"
                            f"`{chat.id}`\n"
                            f"By: [{actor_str}](tg://user?id={actor.id if actor else 0})\n"
                            f"{inv}\n{_now()}"
                        ),
                        parse_mode=enums.ParseMode.MARKDOWN_V2,
                    )
                except Exception as e:
                    log.warning(f"Log channel update failed: {e}")

    except Exception as e:
        log.warning(f"on_member_update error: {e}")


# ── HELP PAGES ────────────────────────────────────────────────────────────────

HELP_PAGES = {
    "1": (
        "📚 *SoulCatcher Help \\(1/4\\) — Spawns & Collection*\n\n"
        "`/drop` — Force a spawn \\(group cooldown applies\\)\n"
        "Auto\\-spawns every 15 messages — press ❤️ to claim\\!\n\n"
        "`/harem` — Browse your collection\n"
        "`/view <ID>` — View character card\n"
        "`/setfav <ID>` — Mark favourite ⭐\n"
        "`/burn <ID>` — Sell for kakera 🔥\n"
        "`/sort <rarity|name|anime|recent>` — Sort harem\n"
    ),
    "2": (
        "📚 *SoulCatcher Help \\(2/4\\) — Economy*\n\n"
        "`/daily` — Daily kakera \\(streak bonuses\\!\\)\n"
        "`/bal` — Balance check\n"
        "`/spin` — Spin wheel \\(1h cooldown\\)\n"
        "`/pay <amount>` — Pay a user \\(reply\\)\n"
        "`/shop` — Buy boosts & items\n\n"
        "`/sell <ID> <price>` — List on market\n"
        "`/buy <listingID>` — Buy from market\n"
        "`/market [rarity]` — Browse listings\n"
    ),
    "3": (
        "📚 *SoulCatcher Help \\(3/4\\) — Social & Games*\n\n"
        "`/trade <myID> <theirID>` — Trade \\(reply to user\\)\n"
        "`/gift <ID>` — Gift character \\(reply to user\\)\n"
        "`/marry` — Marry a random character\\!\n"
        "`/propose` — Propose \\(3rd attempt guaranteed\\!\\)\n"
        "`/basket <bet>` — 🏀 Bet with dice\n"
        "`/wish <charID>` — Add to wishlist\n"
        "`/wishlist` — View your wishlist\n"
    ),
    "4": (
        "📚 *SoulCatcher Help \\(4/4\\) — Rankings & Admin*\n\n"
        "`/profile` — Full profile card\n"
        "`/status` — Detailed stats\n"
        "`/rank` — Your global rank\n"
        "`/top` — Top 10 collectors\n"
        "`/toprarity <n>` — Top by rarity\n"
        "`/richest` — Richest 10 players\n"
        "`/rarityinfo` — Full rarity table\n"
        "`/event` — Current game mode\n\n"
        "*Tiers:* ⚫→🔵→🌌→🔥→💎→🔴→✨\n"
        "*Subs:* 🌸 Seasonal · 🔮 Limited · 🎠 Cartoon"
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
        bot_me  = await client.get_me()
        mention = _safe_mention(cb.from_user)
        text    = DM_TEXT.format(mention=mention, bot=_esc(BOT_NAME))
        kb      = _dm_kb(bot_me.username)
        try:
            await cb.message.edit_caption(
                text, reply_markup=kb, parse_mode=enums.ParseMode.MARKDOWN_V2
            )
        except Exception:
            try:
                await cb.message.edit_text(
                    text, reply_markup=kb, parse_mode=enums.ParseMode.MARKDOWN_V2
                )
            except Exception as e:
                log.warning(f"help_cb home edit failed: {e}")
        return await cb.answer()

    if page not in HELP_PAGES:
        return await cb.answer()

    try:
        await cb.message.edit_text(
            HELP_PAGES[page],
            reply_markup=_help_kb(page),
            parse_mode=enums.ParseMode.MARKDOWN_V2,
        )
    except Exception as e:
        log.warning(f"help_cb page={page} edit failed: {e}")

    await cb.answer()


@app.on_callback_query(filters.regex("^noop$"))
async def noop(_, cb: CallbackQuery):
    await cb.answer()
