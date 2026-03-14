"""
SoulCatcher/modules/summon.py
/summon — ritual duel against a random character.

Speed notes:
  1. Cache fetches only 5 fields via server-side projection + filter.
  2. Stale cache refreshes in background — current request never blocked.
  3. Persistent aiohttp session — one TCP pool reused across all downloads.
  4. Loading message sent instantly before download begins.
  5. Ritual animation: 3 × 0.5 s = 1.5 s total.
"""

from __future__ import annotations

import asyncio
import aiohttp
import logging
import os
import random
import tempfile
import time
from datetime import datetime

from pyrogram import filters, enums
from pyrogram.errors import FloodWait, QueryIdInvalid
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from .. import app
from ..config import OWNER_IDS, SUPPORT_GROUP, BOT_NAME
from ..database import (
    get_or_create_user,
    is_user_banned,
    add_to_harem,
    add_balance,
    get_db,
)
from ..rarity import rarity_display, get_rarity

log  = logging.getLogger("SoulCatcher.summon")
HTML = enums.ParseMode.HTML


# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

# Internal rarity `name` keys to exclude (cartoon = video-only Verse tier)
EXCLUDED_RARITIES: set[str] = {"eternal", "cartoon"}

SUMMON_COOLDOWN_SECS = 10
CACHE_MAX_AGE_SECS   = 1800
DOWNLOAD_TIMEOUT     = 30
MAX_RETRIES          = 5


# ══════════════════════════════════════════════════════════════════════════════
#  IN-MEMORY STATE
# ══════════════════════════════════════════════════════════════════════════════

_last_summon_times: dict[int, datetime] = {}
_active_summons:    dict[int, dict]     = {}
_summon_stats:      dict[int, dict]     = {}


# ══════════════════════════════════════════════════════════════════════════════
#  PERSISTENT HTTP SESSION
# ══════════════════════════════════════════════════════════════════════════════

_http_session: aiohttp.ClientSession | None = None


def _get_http_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession(
            headers={"User-Agent": "Mozilla/5.0"},
            connector=aiohttp.TCPConnector(limit=10),
            timeout=aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT),
        )
    return _http_session


# ══════════════════════════════════════════════════════════════════════════════
#  CHARACTER CACHE
# ══════════════════════════════════════════════════════════════════════════════

_character_cache:  list[dict] = []
_cache_loaded_at:  float      = 0.0
_cache_refreshing: bool       = False


async def _do_cache_refresh() -> None:
    global _character_cache, _cache_loaded_at, _cache_refreshing
    try:
        log.info("Summon cache refresh starting…")
        t0   = time.monotonic()
        docs = await get_db()["characters"].find(
            {
                "enabled": True,
                "img_url": {"$exists": True, "$nin": [None, ""]},
                "rarity":  {"$nin": list(EXCLUDED_RARITIES)},
            },
            {"_id": 0, "id": 1, "name": 1, "anime": 1, "rarity": 1, "img_url": 1},
        ).to_list(length=None)
        _character_cache = [d for d in docs if d.get("img_url")]
        _cache_loaded_at = time.monotonic()
        log.info("Summon cache ready: %d chars in %.2fs",
                 len(_character_cache), time.monotonic() - t0)
    except Exception:
        log.exception("Summon cache refresh failed")
    finally:
        _cache_refreshing = False


async def _ensure_cache() -> None:
    global _cache_refreshing
    if not _character_cache:
        _cache_refreshing = True
        await _do_cache_refresh()
        return
    if time.monotonic() - _cache_loaded_at >= CACHE_MAX_AGE_SECS and not _cache_refreshing:
        _cache_refreshing = True
        asyncio.create_task(_do_cache_refresh())


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _is_owner(uid: int) -> bool:
    return uid in OWNER_IDS


def _stats(uid: int) -> dict:
    return _summon_stats.get(uid, {
        "wins": 0, "losses": 0,
        "streak": 0, "max_streak": 0, "total": 0,
    })


def _esc(t) -> str:
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def _safe_edit(msg: Message, text: str, buttons: list | None = None) -> None:
    markup = InlineKeyboardMarkup(buttons) if buttons else None
    try:
        await msg.edit_caption(text, reply_markup=markup, parse_mode=HTML)
    except FloodWait as e:
        log.warning("FloodWait edit_caption: %ds", e.value)
        await asyncio.sleep(e.value)
        try:
            await msg.edit_caption(text, reply_markup=markup, parse_mode=HTML)
        except Exception as ex:
            log.error("edit_caption retry failed: %s", ex)
    except Exception as e:
        log.error("edit_caption error: %s", e)


async def _safe_answer(query, text: str, alert: bool = False) -> None:
    try:
        await query.answer(text, show_alert=alert)
    except QueryIdInvalid:
        pass
    except Exception as e:
        log.warning("safe_answer error: %s", e)


async def _download_to_temp(url: str) -> str:
    session = _get_http_session()
    async with session.get(url) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        data = await resp.read()
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    try:
        tmp.write(data)
    finally:
        tmp.close()
    return tmp.name


def _del(path: str | None) -> None:
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass


# ══════════════════════════════════════════════════════════════════════════════
#  OWNER COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("reloadsummon"))
async def cmd_reloadsummon(_, message: Message) -> None:
    if not _is_owner(message.from_user.id):
        return await message.reply_text("𖤍 Not your seal to refresh.", parse_mode=HTML)
    global _cache_loaded_at
    _cache_loaded_at = 0.0
    await _do_cache_refresh()
    await message.reply_text(
        f"⟡ <b>Soul pool refreshed</b>\n"
        f"<code>{len(_character_cache)} spirits standing by</code>",
        parse_mode=HTML,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  /summon
# ══════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("summon"))
async def cmd_summon(_, message: Message) -> None:
    if message.chat.type == "private":
        return await message.reply_text(
            f"<b>𖤍  Sealed Territory</b>\n\n"
            f"Soul rituals must be performed inside a group.\n"
            f"› Community — @{_esc(SUPPORT_GROUP)}",
            parse_mode=HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "↳ Enter the Sanctum",
                    url=f"https://t.me/{SUPPORT_GROUP}",
                )
            ]]),
        )

    user_id = message.from_user.id

    await get_or_create_user(
        user_id,
        message.from_user.username or "",
        message.from_user.first_name or "",
        message.from_user.last_name or "",
    )

    if await is_user_banned(user_id):
        return await message.reply_text(
            "𖤍 Your soul-binding rights have been revoked.", parse_mode=HTML
        )

    if user_id in _active_summons:
        return await message.reply_text(
            "⟡ A spirit is already waiting on your seal.\n"
            "<code>Resolve it or use /exitsummon to release it.</code>",
            parse_mode=HTML,
        )

    last = _last_summon_times.get(user_id)
    if last:
        elapsed = (datetime.now() - last).total_seconds()
        if elapsed < SUMMON_COOLDOWN_SECS:
            remaining = int(SUMMON_COOLDOWN_SECS - elapsed)
            return await message.reply_text(
                f"𖤍 The ritual circle is still recovering.\n"
                f"<code>{remaining}s until the next seal</code>",
                parse_mode=HTML,
            )

    await _ensure_cache()
    if not _character_cache:
        return await message.reply_text(
            "⟡ The spirit world is quiet right now.\n"
            "<code>No souls could be reached — try again shortly.</code>",
            parse_mode=HTML,
        )

    loading_msg = await message.reply_text("𖤍  <i>Drawing the seal…</i>", parse_mode=HTML)

    pool      = random.sample(_character_cache, min(MAX_RETRIES, len(_character_cache)))
    character = None
    last_exc  = None

    for candidate in pool:
        img_url  = candidate.get("img_url", "")
        tmp_path = None
        if not img_url:
            continue
        try:
            tmp_path   = await _download_to_temp(img_url)
            rarity_str = rarity_display(candidate.get("rarity", ""))

            caption = (
                f"≺  Spirit Detected  ≻\n\n"
                f"<b>{_esc(candidate.get('name', 'Unknown Spirit'))}</b>\n"
                f"<code>{_esc(candidate.get('anime', 'Origin unknown'))}</code>\n\n"
                f"Rarity  ·  {_esc(rarity_str)}\n\n"
                f"<i>A restless soul stirs nearby.\n"
                f"Draw the seal before it dissolves.</i>"
            )
            with open(tmp_path, "rb") as fh:
                await message.reply_photo(
                    photo=fh,
                    caption=caption,
                    parse_mode=HTML,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton(
                            "✦  Draw the Seal",
                            callback_data=f"summon_begin_{user_id}",
                        )
                    ]]),
                )
            character = candidate
            _active_summons[user_id] = character
            log.info("/summon  user=%d  char=%s  rarity=%s",
                     user_id, character.get("name"), character.get("rarity"))
            break

        except Exception as exc:
            log.warning("/summon URL failed  char=%s  url=%r  err=%s",
                        candidate.get("name"), img_url, exc)
            last_exc = exc
        finally:
            _del(tmp_path)

    try:
        await loading_msg.delete()
    except Exception:
        pass

    if character is None:
        log.error("/summon all %d candidates failed  user=%d", MAX_RETRIES, user_id)
        await message.reply_text(
            "⟡ The spirits would not answer.\n"
            "<code>All seals dissolved — try again.</code>",
            parse_mode=HTML,
        )


# ══════════════════════════════════════════════════════════════════════════════
#  CALLBACKS
# ══════════════════════════════════════════════════════════════════════════════

@app.on_callback_query(filters.regex(r"^summon_begin_(\d+)$"))
async def cb_summon_begin(_, query) -> None:
    user_id = int(query.matches[0].group(1))

    if query.from_user.id != user_id:
        return await _safe_answer(query, "This seal was drawn by another hand.", alert=True)
    if user_id not in _active_summons:
        return await _safe_answer(query, "The spirit dissolved before you could act.", alert=True)

    await query.answer()
    char       = _active_summons[user_id]
    rarity_str = rarity_display(char.get("rarity", ""))

    await _safe_edit(
        query.message,
        f"≺  The Seal Trembles  ≻\n\n"
        f"<b>{_esc(char['name'])}</b> resists your call.\n"
        f"Rarity  ·  {_esc(rarity_str)}\n\n"
        f"<i>Will you press the ritual to completion?</i>",
        [
            [InlineKeyboardButton("𖦹  Bind the Soul", callback_data=f"summon_engage_{user_id}")],
            [InlineKeyboardButton("↩  Release It",     callback_data=f"summon_retreat_{user_id}")],
        ],
    )


@app.on_callback_query(filters.regex(r"^summon_engage_(\d+)$"))
async def cb_summon_engage(_, query) -> None:
    user_id = int(query.matches[0].group(1))

    if query.from_user.id != user_id:
        return await _safe_answer(query, "This ritual belongs to another.", alert=True)
    if user_id not in _active_summons:
        return await _safe_answer(query, "The seal closed before you could finish.", alert=True)

    await query.answer()
    char       = _active_summons[user_id]
    rarity_str = rarity_display(char.get("rarity", ""))

    for phase in [
        "⟡  <i>The sigil takes shape…</i>",
        "⟡  <i>Threads of fate draw tight…</i>",
        "⟡  <i>The binding is cast…</i>",
    ]:
        await _safe_edit(query.message, phase)
        await asyncio.sleep(0.5)

    stats = _stats(user_id)
    stats["total"] += 1
    success = random.random() < 0.5

    if success:
        await add_to_harem(user_id, char)

        robj         = get_rarity(char.get("rarity", ""))
        kakera_bonus = robj.kakera_reward if robj else 10
        await add_balance(user_id, kakera_bonus)

        stats["wins"]      += 1
        stats["streak"]    += 1
        stats["max_streak"] = max(stats["streak"], stats["max_streak"])
        log.info("summon WIN  user=%d  char=%s  rarity=%s  kakera+%d",
                 user_id, char.get("name"), char.get("rarity"), kakera_bonus)

        header = random.choice([
            f"≺  Soul Bound  ≻\n\n{_esc(char['name'])} has been sealed into your collection.",
            f"≺  Binding Complete  ≻\n\n{_esc(char['name'])} surrenders to your will.",
            f"≺  The Seal Holds  ≻\n\n{_esc(char['name'])} is now yours to keep.",
        ])
        await _safe_edit(
            query.message,
            f"<b>{header}</b>\n\n"
            f"Rarity  ·  {_esc(rarity_str)}\n"
            f"Kakera  ·  +{kakera_bonus} 🪙\n"
            f"Bond  ·  Streak {stats['streak']} 🔗",
        )
    else:
        stats["losses"] += 1
        stats["streak"]  = 0
        log.info("summon LOSS  user=%d  char=%s", user_id, char.get("name"))

        header = random.choice([
            f"≺  Seal Broken  ≻\n\n{_esc(char['name'])} shattered your sigil and fled.",
            f"≺  The Ritual Failed  ≻\n\n{_esc(char['name'])} was too strong to hold.",
            f"≺  Spirit Unbound  ≻\n\n{_esc(char['name'])} dissolved your threads and vanished.",
        ])
        await _safe_edit(
            query.message,
            f"<b>{header}</b>\n\n"
            f"Rarity  ·  {_esc(rarity_str)}\n\n"
            f"<i>Redraw the circle and try again.</i>",
        )

    _summon_stats[user_id]      = stats
    _last_summon_times[user_id] = datetime.now()
    _active_summons.pop(user_id, None)


@app.on_callback_query(filters.regex(r"^summon_retreat_(\d+)$"))
async def cb_summon_retreat(_, query) -> None:
    user_id = int(query.matches[0].group(1))

    if query.from_user.id != user_id:
        return await _safe_answer(query, "This thread of fate is not yours.", alert=True)
    if user_id not in _active_summons:
        return await _safe_answer(query, "The soul already drifted away.", alert=True)

    await query.answer()
    char = _active_summons[user_id]
    log.info("summon RETREAT  user=%d  char=%s", user_id, char.get("name"))

    await _safe_edit(
        query.message,
        random.choice([
            f"<b>≺  Seal Released  ≻</b>\n\nYou unravelled the threads.\n{_esc(char['name'])} drifts back into the void.",
            f"<b>≺  Ritual Abandoned  ≻</b>\n\nThe sigil fades.\n{_esc(char['name'])} slips through your fingers.",
            f"<b>≺  The Circle Opens  ≻</b>\n\nYou let the seal dissolve.\n{_esc(char['name'])} is free.",
        ]),
    )

    stats           = _stats(user_id)
    stats["losses"] += 1
    stats["streak"]  = 0
    _summon_stats[user_id]      = stats
    _last_summon_times[user_id] = datetime.now()
    _active_summons.pop(user_id, None)


# ══════════════════════════════════════════════════════════════════════════════
#  /exitsummon
# ══════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("exitsummon"))
async def cmd_exitsummon(_, message: Message) -> None:
    if message.chat.type == "private":
        return await message.reply_text(
            "⟡ No seal is active in private chats.", parse_mode=HTML
        )

    user_id = message.from_user.id
    if user_id in _active_summons:
        char = _active_summons.pop(user_id)
        log.info("/exitsummon  user=%d  abandoned=%s", user_id, char.get("name"))
        await message.reply_text(
            f"<b>≺  Ritual Severed  ≻</b>\n\n"
            f"<i>The seal crumbles. {_esc(char['name'])} returns to the void.</i>",
            parse_mode=HTML,
        )
    else:
        await message.reply_text(
            "⟡ No seal is active.\n<code>Use /summon to call a spirit.</code>",
            parse_mode=HTML,
        )
