"""SoulCatcher/modules/start.py — /start, /help, bot-added logging.

Pyrogram 2.0.106 ParseMode values: DEFAULT | MARKDOWN | HTML | DISABLED
MARKDOWN_V2 does NOT exist in this version — all parse_mode calls use MARKDOWN.

Markdown v1 special chars that MUST be escaped inside user-supplied text:
  _ * ` [  (only these four break the v1 parser)
Everything else (hyphens, dots, parens, !) is safe to use raw in v1.
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
DM_INTRO_VIDEO = "https://files.catbox.moe/6nqjqk.mp4"

MD = enums.ParseMode.MARKDOWN


# ── Helpers ───────────────────────────────────────────────────────────────────

def _uptime() -> str:
    s = int(time.time() - _start_time)
    h, m, s = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}h {m}m {s}s"


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")


_MD1 = re.compile(r"([_*`\[\]])")

def _esc(text: str) -> str:
    return _MD1.sub(r"\\\1", str(text))


def _safe_mention(user) -> str:
    if user and user.first_name:
        return f"[{_esc(user.first_name)}](tg://user?id={user.id})"
    if user:
        return f"User#{user.id}"
    return "Unknown"


# ── Welcome text ──────────────────────────────────────────────────────────────

DM_TEXT = """\
🌸 *Welcome to {bot}, {mention}!*
━━━━━━━━━━━━━━━━━━━━
I'm your anime soul-collecting bot!
━━━━━━━━━━━━━━━━━━━━
You've been registered! Start exploring 👇\
"""


def _dm_kb(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add to Group", url=f"https://t.me/{bot_username}?startgroup=true")],
        [
            InlineKeyboardButton("💬 Support",  url=f"https://t.me/{SUPPORT_GROUP}"),
            InlineKeyboardButton("📢 Updates",  url=f"https://t.me/{UPDATE_CHANNEL}"),
        ],
        [InlineKeyboardButton("📚 Help & Commands", callback_data="help:main")],
    ])


# ── /start DM ─────────────────────────────────────────────────────────────────

@app.on_message(filters.command("start") & filters.private)
async def start_dm(client, message: Message):
    try:
        user = message.from_user
        try:
            await get_or_create_user(
                user.id,
                user.username or "",
                user.first_name or "",
                user.last_name or "",
            )
        except Exception as db_err:
            log.warning(f"DB register failed uid={user.id}: {db_err}")

        bot_me  = await client.get_me()
        mention = _safe_mention(user)
        text    = DM_TEXT.format(mention=mention, bot=_esc(BOT_NAME))
        kb      = _dm_kb(bot_me.username)

        sent = False
        if DM_INTRO_VIDEO:
            try:
                await client.send_video(
                    message.chat.id, DM_INTRO_VIDEO,
                    caption=text, reply_markup=kb, parse_mode=MD,
                )
                sent = True
            except Exception as e:
                log.warning(f"send_video failed uid={user.id}: {e}")

        if not sent:
            await message.reply_text(text, reply_markup=kb, parse_mode=MD)

        if LOG_CHANNEL_ID:
            try:
                await client.send_message(
                    LOG_CHANNEL_ID,
                    f"🟢 */start DM*\n{_esc(user.first_name or f'User#{user.id}')} `{user.id}`\n{_now()}",
                    parse_mode=MD,
                )
            except Exception as e:
                log.warning(f"Log channel failed: {e}")

    except Exception as e:
        log.exception(f"start_dm crashed uid={getattr(message.from_user, 'id', '?')}: {e}")
        try:
            await message.reply_text("Something went wrong. Please try /start again!")
        except Exception:
            pass


# ── /start Group ──────────────────────────────────────────────────────────────

GC_TEXT = (
    "🌸 *{bot} is now active in this group!*\n"
    "━━━━━━━━━━━━━━━\n"
    "📦 Spawns every *15 messages*  |  ⏱ Uptime: `{uptime}`\n"
    "━━━━━━━━━━━━━━━\n"
    "Type `/drop` to force a character spawn!"
)


@app.on_message(filters.command("start") & filters.group)
async def start_gc(client, message: Message):
    try:
        bot_me = await client.get_me()
        text   = GC_TEXT.format(bot=_esc(BOT_NAME), uptime=_uptime())
        kb     = InlineKeyboardMarkup([[
            InlineKeyboardButton("🌸 DM me",    url=f"https://t.me/{bot_me.username}?start=start"),
            InlineKeyboardButton("💬 Support",  url=f"https://t.me/{SUPPORT_GROUP}"),
        ]])
        await message.reply_text(text, reply_markup=kb, parse_mode=MD)
        try:
            await track_group(message.chat.id, getattr(message.chat, "title", ""))
        except Exception as e:
            log.warning(f"track_group failed chat={message.chat.id}: {e}")
    except Exception as e:
        log.exception(f"start_gc crashed chat={getattr(message.chat, 'id', '?')}: {e}")
        try:
            await message.reply_text(
                f"🌸 *{_esc(BOT_NAME)} is active!* Spawns every 15 messages — press ❤️ to claim!",
                parse_mode=MD,
            )
        except Exception:
            pass


# ── Bot added to group log ────────────────────────────────────────────────────

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
                            f"{chat_title}\n`{chat.id}`\n"
                            f"By: [{actor_str}](tg://user?id={actor.id if actor else 0})\n"
                            f"{inv}\n{_now()}"
                        ),
                        parse_mode=MD,
                    )
                except Exception as e:
                    log.warning(f"Log channel update failed: {e}")
    except Exception as e:
        log.warning(f"on_member_update error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# HELP PAGES
# All user-facing commands, sourced from every module in the bot.
# ─────────────────────────────────────────────────────────────────────────────

# Page structure: (title, list of (cmd, description))
_PAGES: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "🎴 Collection",
        [
            ("/harem",                   "Browse your full character collection"),
            ("/collection",              "Alias for /harem"),
            ("/view <id>",               "View a character card from your harem"),
            ("/setfav <id>",             "Mark a character as favourite ⭐ (shown as cover)"),
            ("/burn <id>",               "Burn a character for kakera 🔥"),
            ("/sort <rarity|name|anime|recent>", "Change harem sort order"),
            ("/cmode",                   "Set collection display mode (filter by rarity/anime/etc)"),
            ("/all",                     "Full breakdown of all uploaded characters by rarity"),
            ("/check",                   "Browse the global character database"),
            ("/check <char_id>",         "View a specific character card + ownership stats"),
        ],
    ),
    (
        "⚔️ Spawns & Claiming",
        [
            ("/drop",                    "Force a character spawn (group cooldown applies)"),
            ("/spawn",                   "Alias for /drop"),
            ("❤️ button",                "Press to claim a spawned character"),
            ("/wish <char_id>",          "Add a character to your wishlist — get pinged on spawn"),
            ("/wishlist",                "View your wishlist (max 25 characters)"),
            ("/unwish <char_id>",        "Remove a character from your wishlist"),
        ],
    ),
    (
        "💰 Economy",
        [
            ("/daily",                   "Claim daily kakera reward (streak bonuses apply!)"),
            ("/spin",                    "Spin the wheel for random kakera (1h cooldown)"),
            ("/bal",                     "Check your kakera balance"),
            ("/bal @user",               "Check someone else's balance (reply to them)"),
            ("/pay <amount>",            "Send kakera to a user (reply to them, 2% fee)"),
            ("/cheque <amount> [note]",  "Send a collectible cheque card (reply to user)"),
            ("/cashcheque <id>",         "Cash a cheque you've received"),
        ],
    ),
    (
        "🛒 Market",
        [
            ("/sell <id>",               "Sell a character directly for kakera (instant)"),
            ("/list <id> <price>",       "List a character on the player market"),
            ("/mlist <id> <price>",      "Alias for /list"),
            ("/buy <listing_id>",        "Buy a listing from the market"),
            ("/market",                  "Browse all active market listings"),
            ("/market <rarity>",         "Browse listings filtered by rarity"),
        ],
    ),
    (
        "🤝 Social & Trading",
        [
            ("/trade <my_id> <their_id>","Propose a character trade (reply to user)"),
            ("/gift <id>",               "Gift a character to someone (reply to user)"),
            ("/marry",                   "Marry a random character from the database"),
            ("/propose",                 "Propose to a character (3rd attempt guaranteed!)"),
            ("/epropose",                "Extended propose sequence"),
            ("/basket <bet>",            "🏀 Bet kakera on a dice game"),
        ],
    ),
    (
        "📊 Rankings & Stats",
        [
            ("/profile",                 "View your full profile card"),
            ("/status",                  "Detailed stats: collection, economy, rarity breakdown"),
            ("/rank",                    "Your current global collector rank"),
            ("/top",                     "Top 10 collectors by character count"),
            ("/ktop",                    "Top 10 richest players by kakera"),
            ("/ctop",                    "Top 10 collectors by total character copies"),
            ("/toprarity <rarity>",      "Top 10 collectors for a specific rarity"),
            ("/richest",                 "Top 10 wealthiest players"),
            ("/rarityinfo",              "Full rarity table with drop rates & values"),
            ("/rarityinfo <name>",       "Detailed card for a specific rarity tier"),
            ("/event",                   "Current game mode (normal / happy hour / blitz etc)"),
        ],
    ),
]

# Rarity quick-reference shown at the bottom of the last page
_RARITY_REF = (
    "\n*Rarity Tiers (low → high):*\n"
    "⚫ Common · 🔵 Rare · 🌌 Legendry · 🔥 Elite\n"
    "💎 Seasonal 🌸 Festival · 💀 Mythic 🔮 Limited 🏆 Sports 🧝 Fantasy\n"
    "✨ Eternal 🎠 Verse _(video only — rarest)_"
)

# Pre-render each page as a Markdown string
def _render_pages() -> dict[str, str]:
    pages = {}
    total = len(_PAGES)
    for i, (title, cmds) in enumerate(_PAGES, 1):
        lines = [f"📚 *SoulCatcher Help ({i}/{total}) — {title}*\n"]
        for cmd, desc in cmds:
            lines.append(f"`{cmd}` — {desc}")
        if i == total:
            lines.append(_RARITY_REF)
        pages[str(i)] = "\n".join(lines)
    return pages


HELP_PAGES = _render_pages()


def _help_kb(page: str) -> InlineKeyboardMarkup:
    pages = [str(i) for i in range(1, len(_PAGES) + 1)]
    idx   = pages.index(page)
    nav   = []
    if idx > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"help:{pages[idx - 1]}"))
    nav.append(InlineKeyboardButton(f"{int(page)}/{len(pages)}", callback_data="noop"))
    if idx < len(pages) - 1:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"help:{pages[idx + 1]}"))
    return InlineKeyboardMarkup([
        nav,
        [InlineKeyboardButton("🏠 Home", callback_data="help:home")],
    ])


def _main_help_kb() -> InlineKeyboardMarkup:
    """Category buttons on the main help landing page."""
    buttons = []
    row = []
    for i, (title, _) in enumerate(_PAGES, 1):
        row.append(InlineKeyboardButton(title, callback_data=f"help:{i}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


_MAIN_HELP_TEXT = (
    "📚 *SoulCatcher — Command Help*\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "Choose a category below to explore all commands.\n\n"
    "🎴 Collection — manage your harem\n"
    "⚔️ Spawns — claim characters in groups\n"
    "💰 Economy — earn & spend kakera\n"
    "🛒 Market — buy & sell between players\n"
    "🤝 Social — trade, gift & marry\n"
    "📊 Rankings — stats & leaderboards"
)


@app.on_callback_query(filters.regex(r"^help:"))
async def help_cb(client, cb: CallbackQuery):
    page = cb.data.split(":", 1)[1]

    # ── Home / landing ────────────────────────────────────────────────────────
    if page in ("home", "main"):
        bot_me = await client.get_me()
        if page == "home":
            # Go back to the start welcome card
            mention = _safe_mention(cb.from_user)
            text    = DM_TEXT.format(mention=mention, bot=_esc(BOT_NAME))
            kb      = _dm_kb(bot_me.username)
        else:
            text = _MAIN_HELP_TEXT
            kb   = _main_help_kb()
        try:
            await cb.message.edit_caption(text, reply_markup=kb, parse_mode=MD)
        except Exception:
            try:
                await cb.message.edit_text(text, reply_markup=kb, parse_mode=MD)
            except Exception as e:
                log.warning(f"help_cb {page} edit failed: {e}")
        return await cb.answer()

    # ── "📚 Help & Commands" button → show category menu ─────────────────────
    if page not in HELP_PAGES:
        # Unknown page — show main menu
        try:
            await cb.message.edit_text(_MAIN_HELP_TEXT, reply_markup=_main_help_kb(), parse_mode=MD)
        except Exception:
            pass
        return await cb.answer()

    # ── Specific category page ────────────────────────────────────────────────
    try:
        await cb.message.edit_text(
            HELP_PAGES[page], reply_markup=_help_kb(page), parse_mode=MD,
        )
    except Exception as e:
        log.warning(f"help_cb page={page} edit failed: {e}")

    await cb.answer()


@app.on_callback_query(filters.regex("^noop$"))
async def noop(_, cb: CallbackQuery):
    await cb.answer()
