"""SoulCatcher/modules/start.py

Pyrogram 2.x  +  Bot API 9.4 colored inline buttons via raw TL invoke.

Colored buttons (Bot API 9.4):
  style="danger"  → red / rose  ← used for SoulCatcher pink theme
  style="success" → green
  style="primary" → blue

Pyrogram's high-level InlineKeyboardButton has no `style` param yet,
so we build raw TL objects and call client.invoke() to send them.
Everything else (handlers, DB, logs) stays on normal Pyrogram API.

HELP SYSTEM: Every command listed in _PAGES must have a matching
@app.on_message handler somewhere in the modules/ directory.
Phantom commands have been removed; real commands that were previously
missing have been added.
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
# ─────────────────────────────────────────────────────────────────────────────

def _raw_url_btn(text: str, url: str, style: str = "") -> object:
    btn = raw_types.KeyboardButtonUrl(text=text, url=url)
    if style:
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
    peer = await client.resolve_peer(chat_id)
    await client.invoke(
        functions.messages.SendMessage(
            peer=peer,
            message=text,
            random_id=client.rnd_id(),
            reply_markup=markup,
            no_webpage=True,
            parse_mode=raw_types.InputTextMarkdownV1(),
        )
    )


async def _send_raw_video(client, chat_id: int, video_url: str,
                          caption: str,
                          markup: raw_types.ReplyInlineMarkup) -> None:
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

        sent = False
        if DM_INTRO_VIDEO:
            try:
                await _send_raw_video(client, message.chat.id,
                                      DM_INTRO_VIDEO, text, markup)
                sent = True
            except Exception as e:
                log.warning(f"raw video send failed uid={user.id}: {e}")

        if not sent:
            try:
                await _send_raw_text(client, message.chat.id, text, markup)
                sent = True
            except Exception as e:
                log.warning(f"raw text send failed uid={user.id}: {e}")

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

            try:
                try:
                    inv_link = await client.export_chat_invite_link(chat.id)
                except Exception:
                    inv_link = None

                try:
                    full_chat    = await client.get_chat(chat.id)
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
# HELP SYSTEM
# All commands listed here are verified against registered @app.on_message
# handlers. Removed: /setfav /view /sort /cmode /list /buy /market /basket
#                    /rank /top /ktop /ctop /toprarity /collection
# Added:  /fav /claim /claiminfo /cashcheque /check /all /wguess
#         /marry /propose /epropose /drop
# ─────────────────────────────────────────────────────────────────────────────

from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

_PAGES: list[tuple[str, list[tuple[str, str]]]] = [
    ("🌸 Collection", [
        ("/harem",                "Browse your full character collection"),
        ("/fav <id>",             "Set a character as your harem cover ⭐"),
        ("/fav",                  "View your current favourite"),
        ("/claim",                "Claim your free daily character 🎁"),
        ("/claiminfo",            "Check your daily claim cooldown"),
        ("/check <char_id>",      "View a character card with ownership stats"),
        ("/all",                  "Full character breakdown by rarity tier"),
        ("/burn <id or count>",   "Burn character(s) for kakera 🔥"),
        ("/sell <instance_id>",   "Sell a character for kakera instantly"),
    ]),
    ("💗 Spawns & Claiming", [
        ("/drop",                 "Force a character spawn (group cooldown applies)"),
        ("/spawn",                "Alias for /drop"),
        ("Type the name",        "Guess the character name to claim in groups"),
        ("/wish <char_id>",       "Wishlist a character — get pinged on spawn"),
        ("/wishlist",             "View your wishlist (max 25)"),
        ("/unwish <char_id>",     "Remove a character from your wishlist"),
    ]),
    ("💰 Economy", [
        ("/daily",                "Claim daily kakera (streak bonuses up to day 10!)"),
        ("/spin",                 "Spin the wheel for kakera (10 spins/day)"),
        ("/bal",                  "Check your kakera balance"),
        ("/pay <amount>",         "Send kakera to someone (reply to user, 5m cooldown)"),
        ("/cheque <amount>",      "Issue a kakera cheque card (reply to recipient)"),
        ("/cashcheque <id>",      "Cash a cheque you received"),
    ]),
    ("🎀 Social & Trading", [
        ("/trade <my_id> <their_id>", "Propose a character swap (reply to partner)"),
        ("/gift <instance_id>",   "Gift a character to someone (reply to recipient)"),
        ("/marry",                "Marry a random character (60 s cooldown)"),
        ("/propose",              "Propose to a character — guaranteed on 4th attempt"),
        ("/epropose",             "Cancel your current propose encounter"),
    ]),
    ("💖 Rankings & Stats", [
        ("/profile",              "View your full profile card"),
        ("/status",               "Detailed stats: collection %, economy, rank"),
        ("/richest",              "Top 10 wealthiest players by kakera"),
        ("/topcollector",         "Top 10 collectors by character count"),
        ("/topc",                 "Alias for /topcollector"),
        ("/rarityinfo",           "Full rarity table with drop rates & limits"),
        ("/rarityinfo <name>",    "Detailed card for one rarity — e.g. /rarityinfo mythic"),
        ("/event",                "Current game mode and spawn multipliers"),
    ]),
    ("🎮 Mini-Games & Summon", [
        ("/wguess",               "Word guessing game — 4 or 5 letters, 15 s timer"),
        ("/summon",               "Summon a random soul to duel — group only"),
        ("/exitsummon",           "Abandon your current summon ritual"),
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
    "🌸 Collection — manage your harem & daily claim\n"
    "💗 Spawns — claim characters in groups\n"
    "💰 Economy — earn and spend kakera\n"
    "🎀 Social — trade, gift and marry\n"
    "💖 Rankings — stats and leaderboards\n"
    "🎮 Mini\\-Games — word guess & summon\n\n"
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
        markup  = _dm_raw_kb(bot_me.username)
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
