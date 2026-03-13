"""SoulCatcher/modules/start.py

Pyrogram 2.x  +  Bot API 9.4 colored inline buttons via raw TL invoke.

Colored buttons (Bot API 9.4):
  style="danger"  → red / rose  ← used for SoulCatcher pink theme
  style="success" → green
  style="primary" → blue

Pyrogram's high-level InlineKeyboardButton has no `style` param yet,
so we build raw TL objects and call client.invoke() to send them.
Everything else (handlers, DB, logs) stays on normal Pyrogram API.
"""

import re
import time
import logging
from datetime import datetime

from pyrogram import filters, enums
from pyrogram.types import (
    Message,
    CallbackQuery,
    ChatMemberUpdated,
)
from pyrogram import raw
from pyrogram.raw import functions, types as raw_types

from .. import app
from ..config import BOT_NAME, SUPPORT_GROUP, UPDATE_CHANNEL

# ── Logger GC — hardcoded ─────────────────────────────────────────────────────
LOGGER_GC = -1003824102394
from ..database import get_or_create_user, track_group

log = logging.getLogger("SoulCatcher.start")
_start_time = time.time()

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
    """Escape Markdown v1 special chars only: _ * ` ["""
    return _MD1.sub(r"\\\1", str(text))


def _safe_mention(user) -> str:
    if user and user.first_name:
        return f"[{_esc(user.first_name)}](tg://user?id={user.id})"
    if user:
        return f"User#{user.id}"
    return "Unknown"


# ── Message text ──────────────────────────────────────────────────────────────

DM_TEXT = """\
╭━━━〔 🌸 *SOUL CATCHER* 🌸 〕━━━╮

💗 *Welcome,* {mention}!
_Your anime soul-collecting journey begins here._

🌸 Collect rare characters across every rarity tier
🎀 Build your dream harem and top the leaderboards
💕 Trade, gift, and compete with players worldwide
🌷 Claim your daily kakera and spin the wheel
💖 Wishlist characters — get pinged on spawn

━━━━━━━━━━━━━━━━━━━━
✨ _You've been registered! Explore below_ 👇
╰━━━━━━━━━━━━━━━━━━━━━━━━━━━╯\
"""

GC_TEXT = """\
╭━━━〔 🌸 *SOUL CATCHER* 🌸 〕━━━╮

💗 *{bot} is now active in this group!*

🎴 Characters spawn every *15 messages*
⏱ Uptime: `{uptime}`
💖 Press ❤️ to claim a spawned character!

━━━━━━━━━━━━━━━━━━━━
✨ _Type_ `/drop` _to force a character spawn_
╰━━━━━━━━━━━━━━━━━━━━━━━━━━━╯\
"""


# ─────────────────────────────────────────────────────────────────────────────
# RAW TL BUTTON BUILDERS  (Bot API 9.4 colored buttons via Pyrogram raw API)
#
# raw_types.KeyboardButtonCallback  →  callback_data buttons
# raw_types.KeyboardButtonUrl       →  URL buttons
#
# Both accept a `style` string field in Bot API 9.4:
#   "danger"  = red/rose
#   "success" = green
#   "primary" = blue
#   ""        = default (no color)
#
# NOTE: Pyrogram's TL schema may not include `style` yet.
# We use model_copy / __dict__ injection below as a safe fallback.
# ─────────────────────────────────────────────────────────────────────────────

def _raw_url_btn(text: str, url: str, style: str = "") -> object:
    btn = raw_types.KeyboardButtonUrl(text=text, url=url)
    if style:
        # Inject the style field directly into the TL object dict
        # so it gets serialised into the raw API call even if Pyrogram's
        # schema doesn't expose it natively yet.
        try:
            object.__setattr__(btn, "style", style)
        except Exception:
            pass
    return btn


def _raw_cb_btn(text: str, data: str, style: str = "") -> object:
    btn = raw_types.KeyboardButtonCallback(
        text=text,
        data=data.encode(),
        requires_password=False,
    )
    if style:
        try:
            object.__setattr__(btn, "style", style)
        except Exception:
            pass
    return btn


def _raw_markup(rows: list[list]) -> raw_types.ReplyInlineMarkup:
    """Wrap a list-of-lists of raw button objects into a ReplyInlineMarkup."""
    return raw_types.ReplyInlineMarkup(
        rows=[raw_types.KeyboardButtonRow(buttons=row) for row in rows]
    )


# ─────────────────────────────────────────────────────────────────────────────
# KEYBOARD DEFINITIONS
# ─────────────────────────────────────────────────────────────────────────────

def _dm_raw_kb(bot_username: str) -> raw_types.ReplyInlineMarkup:
    return _raw_markup([
        [
            _raw_url_btn("🌸 Add to Group",
                         f"https://t.me/{bot_username}?startgroup=true",
                         style="danger"),
            _raw_url_btn("💗 Support",
                         f"https://t.me/{SUPPORT_GROUP}",
                         style="danger"),
        ],
        [
            _raw_url_btn("💖 Updates",
                         f"https://t.me/{UPDATE_CHANNEL}",
                         style="success"),
            _raw_cb_btn("🎀 Help & Commands",
                        "help:main",
                        style="danger"),
        ],
    ])


def _gc_raw_kb(bot_username: str) -> raw_types.ReplyInlineMarkup:
    return _raw_markup([
        [
            _raw_url_btn("🌸 Open in DM",
                         f"https://t.me/{bot_username}?start=start",
                         style="danger"),
            _raw_url_btn("💗 Support",
                         f"https://t.me/{SUPPORT_GROUP}",
                         style="danger"),
        ],
        [
            _raw_cb_btn("🎀 Help & Commands", "help:main", style="success"),
        ],
    ])


# ─────────────────────────────────────────────────────────────────────────────
# SEND HELPERS  (raw invoke so the markup reaches Telegram unmodified)
# ─────────────────────────────────────────────────────────────────────────────

async def _send_raw_text(client, chat_id: int, text: str,
                         markup: raw_types.ReplyInlineMarkup) -> None:
    """Send a text message with a raw TL markup using client.invoke()."""
    peer = await client.resolve_peer(chat_id)
    await client.invoke(
        functions.messages.SendMessage(
            peer=peer,
            message=text,
            random_id=client.rnd_id(),
            reply_markup=markup,
            no_webpage=True,
            parse_mode=raw_types.InputTextMarkdownV1(),  # Pyrogram internal parse
        )
    )


async def _send_raw_video(client, chat_id: int, video_url: str,
                          caption: str,
                          markup: raw_types.ReplyInlineMarkup) -> None:
    """Send a video URL with caption + raw TL markup."""
    peer = await client.resolve_peer(chat_id)
    media = raw_types.InputMediaDocumentExternal(url=video_url)
    await client.invoke(
        functions.messages.SendMedia(
            peer=peer,
            media=media,
            message=caption,
            random_id=client.rnd_id(),
            reply_markup=markup,
        )
    )


# ─────────────────────────────────────────────────────────────────────────────
# /start — PRIVATE DM
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("start") & filters.private)
async def start_dm(client, message: Message):
    try:
        user = message.from_user

        # Register user
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
        text    = DM_TEXT.format(mention=mention)
        markup  = _dm_raw_kb(bot_me.username)

        # Try raw video send first
        sent = False
        if DM_INTRO_VIDEO:
            try:
                await _send_raw_video(client, message.chat.id,
                                      DM_INTRO_VIDEO, text, markup)
                sent = True
            except Exception as e:
                log.warning(f"raw video send failed uid={user.id}: {e}")

        # Fallback: raw text send
        if not sent:
            try:
                await _send_raw_text(client, message.chat.id, text, markup)
                sent = True
            except Exception as e:
                log.warning(f"raw text send failed uid={user.id}: {e}")

        # Last resort: normal Pyrogram send (no colored buttons)
        if not sent:
            from pyrogram.types import (
                InlineKeyboardMarkup as IKM,
                InlineKeyboardButton as IKB,
            )
            fallback_kb = IKM([
                [IKB("🌸 Add to Group",
                     url=f"https://t.me/{bot_me.username}?startgroup=true"),
                 IKB("💗 Support", url=f"https://t.me/{SUPPORT_GROUP}")],
                [IKB("💖 Updates", url=f"https://t.me/{UPDATE_CHANNEL}"),
                 IKB("🎀 Help & Commands", callback_data="help:main")],
            ])
            await message.reply_text(text, reply_markup=fallback_kb, parse_mode=MD)

        # ── Log to logger GC ─────────────────────────────────────────────────
        try:
            uname     = f"@{user.username}" if user.username else "no username"
            full_name = _esc(
                f"{user.first_name or ''} {user.last_name or ''}".strip()
                or f"User#{user.id}"
            )
            mention_link = f"[{full_name}](tg://user?id={user.id})"
            await client.send_message(
                LOGGER_GC,
                (
                    "🌸 *New User Started Bot*\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 *Name:* {mention_link}\n"
                    f"🔖 *Username:* `{uname}`\n"
                    f"🆔 *User ID:* `{user.id}`\n"
                    f"🕐 *Time:* `{_now()}`\n"
                    "━━━━━━━━━━━━━━━━━━━━"
                ),
                parse_mode=MD,
            )
        except Exception as e:
            log.warning(f"Logger GC /start log failed: {e}")

    except Exception as e:
        log.exception(f"start_dm crashed uid={getattr(message.from_user, 'id', '?')}: {e}")
        try:
            await message.reply_text(
                "🌸 Something went wrong. Please try /start again!"
            )
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# /start — GROUP
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("start") & filters.group)
async def start_gc(client, message: Message):
    try:
        bot_me = await client.get_me()
        text   = GC_TEXT.format(bot=_esc(BOT_NAME), uptime=_uptime())
        markup = _gc_raw_kb(bot_me.username)

        try:
            await _send_raw_text(client, message.chat.id, text, markup)
        except Exception as e:
            log.warning(f"raw gc send failed, falling back: {e}")
            from pyrogram.types import (
                InlineKeyboardMarkup as IKM,
                InlineKeyboardButton as IKB,
            )
            fallback_kb = IKM([[
                IKB("🌸 Open in DM",
                    url=f"https://t.me/{bot_me.username}?start=start"),
                IKB("💗 Support", url=f"https://t.me/{SUPPORT_GROUP}"),
            ]])
            await message.reply_text(text, reply_markup=fallback_kb, parse_mode=MD)

        try:
            await track_group(message.chat.id, getattr(message.chat, "title", ""))
        except Exception as e:
            log.warning(f"track_group failed: {e}")

    except Exception as e:
        log.exception(f"start_gc crashed: {e}")
        try:
            await message.reply_text(
                f"🌸 *{_esc(BOT_NAME)} is active!* Spawns every 15 messages!",
                parse_mode=MD,
            )
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# BOT ADDED TO GROUP — log
# ─────────────────────────────────────────────────────────────────────────────

@app.on_chat_member_updated()
async def on_member_update(client, update: ChatMemberUpdated):
    try:
        old_s = getattr(update.old_chat_member, "status", None)
        new_s = getattr(update.new_chat_member, "status", None)

        # ── Bot was added to / rejoined a group ───────────────────────────────
        if old_s in ("left", "kicked", None) and new_s in ("member", "administrator"):
            chat  = update.chat
            actor = update.from_user

            try:
                await track_group(chat.id, getattr(chat, "title", ""))
            except Exception as e:
                log.warning(f"track_group failed: {e}")

            # ── Rich logger GC message ────────────────────────────────────────
            try:
                # Try to get a real invite link; fall back gracefully
                try:
                    inv_link = await client.export_chat_invite_link(chat.id)
                except Exception:
                    inv_link = None

                # Try fetching member count
                try:
                    full_chat   = await client.get_chat(chat.id)
                    member_count = getattr(full_chat, "members_count", "?")
                except Exception:
                    member_count = "?"

                chat_title  = _esc(getattr(chat, "title", str(chat.id)))
                chat_type   = str(getattr(chat, "type", "group")).replace("ChatType.", "").lower()
                actor_name  = _esc(actor.first_name) if actor and actor.first_name else "Unknown"
                actor_uname = f"@{actor.username}" if actor and actor.username else "no username"
                actor_id    = actor.id if actor else 0

                link_line = f"🔗 *Link:* {inv_link}" if inv_link else "🔗 *Link:* `private / N/A`"

                await client.send_message(
                    LOGGER_GC,
                    (
                        "🔔 *Bot Added to Group*\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"📛 *Group:* {chat_title}\n"
                        f"🆔 *Group ID:* `{chat.id}`\n"
                        f"📂 *Type:* `{chat_type}`\n"
                        f"👥 *Members:* `{member_count}`\n"
                        f"{link_line}\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"👤 *Added by:* [{actor_name}](tg://user?id={actor_id})\n"
                        f"🔖 *Username:* `{actor_uname}`\n"
                        f"🆔 *User ID:* `{actor_id}`\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"🕐 *Time:* `{_now()}`"
                    ),
                    parse_mode=MD,
                )
            except Exception as e:
                log.warning(f"Logger GC group-added log failed: {e}")

        # ── Bot was removed from a group ──────────────────────────────────────
        elif old_s in ("member", "administrator") and new_s in ("left", "kicked"):
            chat  = update.chat
            actor = update.from_user
            try:
                chat_title  = _esc(getattr(chat, "title", str(chat.id)))
                actor_name  = _esc(actor.first_name) if actor and actor.first_name else "Unknown"
                actor_id    = actor.id if actor else 0
                await client.send_message(
                    LOGGER_GC,
                    (
                        "🚫 *Bot Removed from Group*\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"📛 *Group:* {chat_title}\n"
                        f"🆔 *Group ID:* `{chat.id}`\n"
                        f"👤 *Removed by:* [{actor_name}](tg://user?id={actor_id})\n"
                        f"🕐 *Time:* `{_now()}`"
                    ),
                    parse_mode=MD,
                )
            except Exception as e:
                log.warning(f"Logger GC group-removed log failed: {e}")

    except Exception as e:
        log.warning(f"on_member_update error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# HELP SYSTEM  (normal Pyrogram high-level API — no colored buttons needed)
# ─────────────────────────────────────────────────────────────────────────────

from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

_PAGES: list[tuple[str, list[tuple[str, str]]]] = [
    ("🌸 Collection", [
        ("/harem",                           "Browse your full character collection"),
        ("/collection",                      "Alias for /harem"),
        ("/view <id>",                       "View a character card from your harem"),
        ("/setfav <id>",                     "Mark a character as favourite ⭐"),
        ("/burn <id>",                       "Burn a character for kakera 🔥"),
        ("/sort <rarity|name|anime|recent>", "Change harem sort order"),
        ("/cmode",                           "Set collection display mode"),
        ("/all",                             "Full breakdown by rarity"),
        ("/check",                           "Browse global character database"),
        ("/check <char_id>",                 "View a specific card + ownership stats"),
    ]),
    ("💗 Spawns & Claiming", [
        ("/drop",        "Force a character spawn (group cooldown applies)"),
        ("/spawn",       "Alias for /drop"),
        ("❤️ button",   "Press to claim a spawned character"),
        ("/wish <id>",   "Wishlist a character — get pinged on spawn"),
        ("/wishlist",    "View your wishlist (max 25)"),
        ("/unwish <id>", "Remove from wishlist"),
    ]),
    ("💰 Economy", [
        ("/daily",                  "Claim daily kakera (streak bonuses!)"),
        ("/spin",                   "Spin the wheel for kakera (1h cooldown)"),
        ("/bal",                    "Check your kakera balance"),
        ("/bal @user",              "Check someone else's balance"),
        ("/pay <amount>",           "Send kakera (reply to user, 2% fee)"),
        ("/cheque <amount> [note]", "Send a collectible cheque card"),
        ("/cashcheque <id>",        "Cash a received cheque"),
    ]),
    ("🛒 Market", [
        ("/sell <id>",         "Sell a character instantly for kakera"),
        ("/list <id> <price>", "List a character on the market"),
        ("/buy <listing_id>",  "Buy a listing from the market"),
        ("/market",            "Browse all active listings"),
        ("/market <rarity>",   "Filter market by rarity"),
    ]),
    ("🎀 Social & Trading", [
        ("/trade <my_id> <their_id>", "Propose a character trade"),
        ("/gift <id>",                "Gift a character to someone"),
        ("/marry",                    "Marry a random character"),
        ("/propose",                  "Propose to a character (3rd guaranteed!)"),
        ("/basket <bet>",             "🏀 Bet kakera on a dice game"),
    ]),
    ("💖 Rankings & Stats", [
        ("/profile",            "View your full profile card"),
        ("/status",             "Detailed stats: collection, economy, rarities"),
        ("/rank",               "Your current global collector rank"),
        ("/top",                "Top 10 collectors by character count"),
        ("/ktop",               "Top 10 richest by kakera"),
        ("/ctop",               "Top 10 by total copies"),
        ("/toprarity <rarity>", "Top 10 for a specific rarity"),
        ("/richest",            "Top 10 wealthiest players"),
        ("/rarityinfo",         "Full rarity table with drop rates"),
        ("/event",              "Current game mode"),
    ]),
]

_RARITY_REF = (
    "\n💎 *Rarity Tiers (low → high)*\n"
    "⚫ Common · 🔵 Rare · 🌌 Legendry · 🔥 Elite\n"
    "💎 Seasonal · 🌸 Festival · 💀 Mythic · 🔮 Limited\n"
    "🏆 Sports · 🧝 Fantasy · ✨ Eternal · 🎠 Verse _(video — rarest)_"
)

_MAIN_HELP_TEXT = (
    "╭━━━〔 🌸 *SOUL CATCHER HELP* 🌸 〕━━━╮\n\n"
    "💗 Choose a category below to explore all commands\\.\n\n"
    "🌸 Collection — manage your harem\n"
    "💗 Spawns — claim characters in groups\n"
    "💰 Economy — earn and spend kakera\n"
    "🛒 Market — buy and sell between players\n"
    "🎀 Social — trade, gift and marry\n"
    "💖 Rankings — stats and leaderboards\n\n"
    "╰━━━━━━━━━━━━━━━━━━━━━━━━━━━╯"
)


def _render_pages() -> dict[str, str]:
    pages = {}
    total = len(_PAGES)
    for i, (title, cmds) in enumerate(_PAGES, 1):
        lines = [
            f"╭━━━〔 {title} 〕━━━╮\n",
            f"📚 *SoulCatcher Help ({i}/{total})*\n",
        ]
        for cmd, desc in cmds:
            lines.append(f"`{cmd}` — {desc}")
        if i == total:
            lines.append(_RARITY_REF)
        lines.append("\n╰━━━━━━━━━━━━━━━━━━━━━━━━━━━╯")
        pages[str(i)] = "\n".join(lines)
    return pages


HELP_PAGES = _render_pages()


def _help_kb(page: str) -> InlineKeyboardMarkup:
    pages = [str(i) for i in range(1, len(_PAGES) + 1)]
    idx   = pages.index(page)
    nav   = []
    if idx > 0:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"help:{pages[idx - 1]}"))
    nav.append(InlineKeyboardButton(f"🌸 {int(page)}/{len(pages)}", callback_data="noop"))
    if idx < len(pages) - 1:
        nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"help:{pages[idx + 1]}"))
    return InlineKeyboardMarkup([
        nav,
        [InlineKeyboardButton("🏠 Home", callback_data="help:home")],
    ])


def _main_help_kb() -> InlineKeyboardMarkup:
    buttons: list[list] = []
    row: list = []
    for i, (title, _) in enumerate(_PAGES, 1):
        row.append(InlineKeyboardButton(title, callback_data=f"help:{i}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


@app.on_callback_query(filters.regex(r"^help:"))
async def help_cb(client, cb: CallbackQuery):
    page = cb.data.split(":", 1)[1]

    if page == "home":
        bot_me  = await client.get_me()
        mention = _safe_mention(cb.from_user)
        text    = DM_TEXT.format(mention=mention)
        # rebuild raw markup for home button
        markup = _dm_raw_kb(bot_me.username)
        try:
            await cb.message.edit_caption(text, parse_mode=MD)
        except Exception:
            try:
                await cb.message.edit_text(text, parse_mode=MD)
            except Exception as e:
                log.warning(f"help home edit failed: {e}")
        return await cb.answer()

    if page == "main":
        try:
            await cb.message.edit_text(
                _MAIN_HELP_TEXT, reply_markup=_main_help_kb(), parse_mode=MD,
            )
        except Exception as e:
            log.warning(f"help main edit failed: {e}")
        return await cb.answer()

    if page not in HELP_PAGES:
        try:
            await cb.message.edit_text(
                _MAIN_HELP_TEXT, reply_markup=_main_help_kb(), parse_mode=MD,
            )
        except Exception:
            pass
        return await cb.answer()

    try:
        await cb.message.edit_text(
            HELP_PAGES[page], reply_markup=_help_kb(page), parse_mode=MD,
        )
    except Exception as e:
        log.warning(f"help page={page} edit failed: {e}")
    await cb.answer()


@app.on_callback_query(filters.regex("^noop$"))
async def noop(_, cb: CallbackQuery):
    await cb.answer()
