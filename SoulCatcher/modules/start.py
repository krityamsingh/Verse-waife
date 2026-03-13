"""SoulCatcher/modules/start_colored.py

Rewritten in aiogram 3.x to use Bot API 9.4 colored inline buttons.

Bot API 9.4 button style options (ONLY these 3 exist — no pink):
  "danger"  → red   (closest to pink/rose — best for SoulCatcher theme)
  "success" → green
  "primary" → blue

Install:
  pip install aiogram>=3.7.0

Pyrogram does NOT support the `style` field yet.
Use aiogram for colored buttons.
"""

import logging
import re
import time
from datetime import datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

# ── Config (replace with your values / import from config.py) ─────────────────
BOT_TOKEN       = "YOUR_BOT_TOKEN"
BOT_NAME        = "Soul Catcher"
LOG_CHANNEL_ID  = -1001234567890   # or None
SUPPORT_GROUP   = "your_support_group"
UPDATE_CHANNEL  = "your_update_channel"
DM_INTRO_VIDEO  = "https://files.catbox.moe/6nqjqk.mp4"

# ── Bot / Dispatcher setup ────────────────────────────────────────────────────
bot    = Bot(token=BOT_TOKEN)
dp     = Dispatcher()
router = Router()
dp.include_router(router)

log         = logging.getLogger("SoulCatcher.start")
_start_time = time.time()

# ── Markdown escape (Telegram MarkdownV2) ─────────────────────────────────────
_MD2 = re.compile(r"([_*\[\]()~`>#+=|{}.!\\-])")

def _esc(text: str) -> str:
    return _MD2.sub(r"\\\1", str(text))


def _uptime() -> str:
    s = int(time.time() - _start_time)
    h, m, s = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}h {m}m {s}s"


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")


# ─────────────────────────────────────────────────────────────────────────────
# HELPER: Build InlineKeyboardButton WITH color style
#
# aiogram 3.x passes extra kwargs straight through to the Bot API JSON,
# so `style=` works as soon as Telegram supports it (Bot API 9.4+).
# ─────────────────────────────────────────────────────────────────────────────

def _btn(text: str, *, style: str | None = None, **kwargs) -> InlineKeyboardButton:
    """
    Wrapper that injects `style` into an InlineKeyboardButton.

    style options:
      "danger"  → red   (best pink-adjacent for SoulCatcher 🌸)
      "success" → green
      "primary" → blue
      None      → default (grey/white depending on theme)
    """
    extra = {"style": style} if style else {}
    return InlineKeyboardButton(text=text, **kwargs, **extra)


# ─────────────────────────────────────────────────────────────────────────────
# WELCOME TEXT
# ─────────────────────────────────────────────────────────────────────────────

DM_TEXT = (
    "╭━━━〔 🌸 *SOUL CATCHER* 🌸 〕━━━╮\n\n"
    "💗 *Welcome, {mention}\\!*\n"
    "_Your anime soul\\-collecting journey begins here\\._\n\n"
  
    "━━━━━━━━━━━━━━━━━━━━\n"
    "✨ _You've been registered\\! Explore below_ 👇\n"
    "╰━━━━━━━━━━━━━━━━━━━━━━━━━━━╯"
)

GC_TEXT = (
    "╭━━━〔 🌸 *SOUL CATCHER* 🌸 〕━━━╮\n\n"
    "💗 *{bot} is now active in this group\\!*\n\n"
    "🎴 Characters spawn every *15 messages*\n"
    "⏱ Uptime: `{uptime}`\n"
    "💖 Press ❤️ to claim a spawned character\\!\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "✨ _Type_ `/drop` _to force a character spawn_\n"
    "╰━━━━━━━━━━━━━━━━━━━━━━━━━━━╯"
)


# ─────────────────────────────────────────────────────────────────────────────
# KEYBOARDS  —  Bot API 9.4 colored buttons
#
# Layout mirror (2 per row, pink-adjacent theme):
#   [ 🌸 Add to Group  |  💗 Support  ]   ← both "danger" (red/rose)
#   [ 💖 Updates       |  🎀 Help     ]   ← "success" (green) + "danger"
# ─────────────────────────────────────────────────────────────────────────────

def _dm_kb(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn("🌸 Add to Group",
                 style="danger",
                 url=f"https://t.me/{bot_username}?startgroup=true"),
            _btn("💗 Support",
                 style="danger",
                 url=f"https://t.me/{SUPPORT_GROUP}"),
        ],
        [
            _btn("💖 Updates",
                 style="success",
                 url=f"https://t.me/{UPDATE_CHANNEL}"),
            _btn("🎀 Help & Commands",
                 style="danger",
                 callback_data="help:main"),
        ],
    ])


def _gc_kb(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn("🌸 Open in DM",
                 style="danger",
                 url=f"https://t.me/{bot_username}?start=start"),
            _btn("💗 Support",
                 style="danger",
                 url=f"https://t.me/{SUPPORT_GROUP}"),
        ],
        [
            _btn("🎀 Help & Commands",
                 style="success",
                 callback_data="help:main"),
        ],
    ])


# ─────────────────────────────────────────────────────────────────────────────
# /start — PRIVATE DM
# ─────────────────────────────────────────────────────────────────────────────

@router.message(CommandStart(), F.chat.type == "private")
async def start_dm(message: Message):
    user    = message.from_user
    mention = f"[{_esc(user.first_name)}](tg://user?id={user.id})"
    text    = DM_TEXT.format(mention=mention)
    me      = await bot.get_me()
    kb      = _dm_kb(me.username)

    sent = False
    if DM_INTRO_VIDEO:
        try:
            await message.answer_video(
                DM_INTRO_VIDEO,
                caption=text,
                reply_markup=kb,
                parse_mode="MarkdownV2",
            )
            sent = True
        except Exception as e:
            log.warning(f"send_video failed uid={user.id}: {e}")

    if not sent:
        await message.answer(text, reply_markup=kb, parse_mode="MarkdownV2")

    if LOG_CHANNEL_ID:
        try:
            await bot.send_message(
                LOG_CHANNEL_ID,
                f"🌸 */start DM*\n{_esc(user.first_name or f'User#{user.id}')} `{user.id}`\n{_now()}",
                parse_mode="MarkdownV2",
            )
        except Exception as e:
            log.warning(f"Log channel failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# /start — GROUP
# ─────────────────────────────────────────────────────────────────────────────

@router.message(CommandStart(), F.chat.type.in_({"group", "supergroup"}))
async def start_gc(message: Message):
    me   = await bot.get_me()
    text = GC_TEXT.format(bot=_esc(BOT_NAME), uptime=_uptime())
    kb   = _gc_kb(me.username)
    await message.answer(text, reply_markup=kb, parse_mode="MarkdownV2")


# ─────────────────────────────────────────────────────────────────────────────
# HELP PAGES
# ─────────────────────────────────────────────────────────────────────────────

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
    "\n💎 *Rarity Tiers \\(low → high\\)*\n"
    "⚫ Common · 🔵 Rare · 🌌 Legendry · 🔥 Elite\n"
    "💎 Seasonal · 🌸 Festival · 💀 Mythic · 🔮 Limited\n"
    "🏆 Sports · 🧝 Fantasy · ✨ Eternal · 🎠 Verse _\\(video — rarest\\)_"
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
            f"📚 *SoulCatcher Help \\({i}/{total}\\)*\n",
        ]
        for cmd, desc in cmds:
            lines.append(f"`{_esc(cmd)}` — {_esc(desc)}")
        if i == total:
            lines.append(_RARITY_REF)
        lines.append("\n╰━━━━━━━━━━━━━━━━━━━━━━━━━━━╯")
        pages[str(i)] = "\n".join(lines)
    return pages


HELP_PAGES = _render_pages()


def _help_kb(page: str) -> InlineKeyboardMarkup:
    pages = [str(i) for i in range(1, len(_PAGES) + 1)]
    idx   = pages.index(page)
    nav: list[InlineKeyboardButton] = []
    if idx > 0:
        nav.append(_btn("◀️ Prev", style="primary",
                        callback_data=f"help:{pages[idx - 1]}"))
    nav.append(_btn(f"🌸 {int(page)}/{len(pages)}", callback_data="noop"))
    if idx < len(pages) - 1:
        nav.append(_btn("Next ▶️", style="primary",
                        callback_data=f"help:{pages[idx + 1]}"))
    return InlineKeyboardMarkup(inline_keyboard=[
        nav,
        [_btn("🏠 Home", style="danger", callback_data="help:home")],
    ])


def _main_help_kb() -> InlineKeyboardMarkup:
    """2-per-row category grid — alternating danger / success colors."""
    buttons: list[list] = []
    row: list = []
    styles = ["danger", "success"]
    for i, (title, _) in enumerate(_PAGES, 1):
        row.append(_btn(title, style=styles[i % 2],
                        callback_data=f"help:{i}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ─────────────────────────────────────────────────────────────────────────────
# HELP CALLBACK
# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("help:"))
async def help_cb(cb: CallbackQuery):
    page = cb.data.split(":", 1)[1]

    if page == "home":
        me      = await bot.get_me()
        mention = f"[{_esc(cb.from_user.first_name)}](tg://user?id={cb.from_user.id})"
        text    = DM_TEXT.format(mention=mention)
        kb      = _dm_kb(me.username)
        try:
            await cb.message.edit_caption(text, reply_markup=kb, parse_mode="MarkdownV2")
        except Exception:
            await cb.message.edit_text(text, reply_markup=kb, parse_mode="MarkdownV2")
        return await cb.answer()

    if page == "main":
        await cb.message.edit_text(
            _MAIN_HELP_TEXT, reply_markup=_main_help_kb(), parse_mode="MarkdownV2",
        )
        return await cb.answer()

    if page not in HELP_PAGES:
        await cb.message.edit_text(
            _MAIN_HELP_TEXT, reply_markup=_main_help_kb(), parse_mode="MarkdownV2",
        )
        return await cb.answer()

    await cb.message.edit_text(
        HELP_PAGES[page], reply_markup=_help_kb(page), parse_mode="MarkdownV2",
    )
    await cb.answer()


@router.callback_query(F.data == "noop")
async def noop(cb: CallbackQuery):
    await cb.answer()


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
