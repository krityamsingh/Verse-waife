"""
SoulCatcher/modules/summon.py
/summon — duel a random character from the database.

Adapted from smash.py (Grabber bot) to use SoulCatcher's database layer,
rarity system, config, and Pyrogram client.

Speed optimisations carried over:
  1. Character cache fetches only needed fields via projection and filters
     entirely server-side in MongoDB — no full-collection scan into RAM.
  2. Background cache refresh — stale cache triggers asyncio.create_task()
     so the current /summon request is never blocked waiting for a reload.
  3. Persistent aiohttp session reused across all image downloads
     — eliminates ~200-400 ms of TCP handshake overhead per /summon.
  4. "Summoning…" reply sent immediately before the download starts
     so the user gets instant visual feedback.
  5. Battle animation 3 × 0.5 s = 1.5 s total (vs 4 × 0.8 s = 3.2 s).
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

from pyrogram import filters
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

log = logging.getLogger("SoulCatcher.summon")


# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

# Rarities excluded from /summon pool.
# These are the internal `name` keys from rarity.py.
# cartoon  = Verse sub-rarity (video-only — img_url may be missing)
# eternal  = top-tier, kept rare and out of smash pool
EXCLUDED_RARITIES: set[str] = {
    "eternal",
    "cartoon",
}

SUMMON_COOLDOWN_SECS = 10
CACHE_MAX_AGE_SECS   = 1800   # refresh cache every 30 min
DOWNLOAD_TIMEOUT     = 30     # seconds before giving up on a dead image URL
MAX_RETRIES          = 5      # candidates to try before giving up entirely

# Permanent chats where /summon is always allowed (add your main GC ID here).
PERMANENT_AUTHORIZED_CHATS: set[int] = set()

# Runtime-mutable authorized chats (managed via /authsummon / /unauthsummon).
authorized_chats: set[int] = set(PERMANENT_AUTHORIZED_CHATS)


# ══════════════════════════════════════════════════════════════════════════════
#  IN-MEMORY STATE  (cleared on bot restart — intentional, keeps it simple)
# ══════════════════════════════════════════════════════════════════════════════

_last_summon_times: dict[int, datetime] = {}  # user_id → last /summon timestamp
_active_summons:    dict[int, dict]     = {}  # user_id → character dict mid-battle
_summon_stats:      dict[int, dict]     = {}  # user_id → win/loss/streak counters


# ══════════════════════════════════════════════════════════════════════════════
#  PERSISTENT HTTP SESSION
#  One module-level session = one TCP pool reused for every image download.
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
#  Fetches only the 5 fields summon needs, filtered server-side.
# ══════════════════════════════════════════════════════════════════════════════

_character_cache:  list[dict] = []
_cache_loaded_at:  float      = 0.0
_cache_refreshing: bool       = False


async def _do_cache_refresh() -> None:
    """Fetch eligible characters from MongoDB. Runs in a background task."""
    global _character_cache, _cache_loaded_at, _cache_refreshing
    try:
        log.info("Summon cache refresh starting…")
        t0 = time.monotonic()

        docs = await get_db()["characters"].find(
            {
                "enabled":  True,
                "img_url":  {"$exists": True, "$nin": [None, ""]},
                "rarity":   {"$nin": list(EXCLUDED_RARITIES)},
            },
            {
                "_id":     0,
                "id":      1,
                "name":    1,
                "anime":   1,
                "rarity":  1,
                "img_url": 1,
            },
        ).to_list(length=None)

        # Belt-and-suspenders: drop any doc that still slipped through without a URL
        _character_cache = [d for d in docs if d.get("img_url")]
        _cache_loaded_at = time.monotonic()
        elapsed = round(time.monotonic() - t0, 2)
        log.info("Summon cache ready: %d characters in %ss", len(_character_cache), elapsed)
    except Exception:
        log.exception("Summon cache refresh failed")
    finally:
        _cache_refreshing = False


async def _ensure_cache() -> None:
    """
    Guarantee the cache is usable before picking a character.
    • First call ever  → blocks until loaded (cold-start, unavoidable)
    • Cache is fresh   → returns immediately
    • Cache is stale   → returns the old cache now, refreshes in background
    """
    global _cache_refreshing
    age = time.monotonic() - _cache_loaded_at

    if not _character_cache:
        _cache_refreshing = True
        await _do_cache_refresh()
        return

    if age >= CACHE_MAX_AGE_SECS and not _cache_refreshing:
        _cache_refreshing = True
        asyncio.create_task(_do_cache_refresh())


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _is_owner(user_id: int) -> bool:
    return user_id in OWNER_IDS


def _is_authorized(chat_id: int) -> bool:
    return chat_id in authorized_chats


def _get_user_stats(user_id: int) -> dict:
    return _summon_stats.get(user_id, {
        "wins": 0, "losses": 0,
        "streak": 0, "max_streak": 0, "total_summons": 0,
    })


async def _send_unauthorized(message: Message, is_private: bool = False) -> None:
    if is_private:
        text = (
            f"**𖤍  Sealed Territory**\n\n"
            f"Soul rituals must be performed inside a sanctified group.\n"
            f"Lone summoners cannot draw the binding seal.\n\n"
            f"› Community — @{SUPPORT_GROUP}"
        )
    else:
        text = (
            f"**𖤍  This Ground Is Unsanctified**\n\n"
            f"The ritual circle has not been blessed here.\n"
            f"Souls will not answer the call of an unregistered realm.\n\n"
            f"› Community — @{SUPPORT_GROUP}"
        )
    await message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("↳ Enter the Sanctum", url=f"https://t.me/{SUPPORT_GROUP}")
        ]]),
    )


async def _safe_edit_caption(
    message: Message,
    caption: str,
    buttons: list | None = None,
) -> None:
    from pyrogram import enums
    markup = InlineKeyboardMarkup(buttons) if buttons else None
    try:
        await message.edit_caption(
            caption,
            reply_markup=markup,
            parse_mode=enums.ParseMode.MARKDOWN,
        )
    except FloodWait as exc:
        log.warning("FloodWait on edit_caption: %ds", exc.value)
        await asyncio.sleep(exc.value)
        try:
            await message.edit_caption(
                caption,
                reply_markup=markup,
                parse_mode=enums.ParseMode.MARKDOWN,
            )
        except Exception as e:
            log.error("edit_caption retry failed: %s", e)
    except Exception as e:
        log.error("edit_caption error: %s", e)


async def _safe_answer(query, text: str, show_alert: bool = False) -> None:
    try:
        await query.answer(text, show_alert=show_alert)
    except QueryIdInvalid:
        pass
    except Exception as e:
        log.warning("safe_answer error: %s", e)


async def _download_to_temp(url: str) -> str:
    """Download image via the persistent session. Returns a temp file path."""
    session = _get_http_session()
    async with session.get(url) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} for {url!r}")
        data = await resp.read()

    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    try:
        tmp.write(data)
    finally:
        tmp.close()
    return tmp.name


def _safe_delete(path: str | None) -> None:
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass


# ══════════════════════════════════════════════════════════════════════════════
#  OWNER COMMANDS — manage which groups may use /summon
# ══════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("authsummon"))
async def cmd_authsummon(_, message: Message) -> None:
    if not _is_owner(message.from_user.id):
        await message.reply_text("𖤍 This seal is not yours to draw.")
        return

    args = message.text.split()
    if len(args) > 1:
        try:
            target = int(args[1])
        except ValueError:
            await message.reply_text("↯ Invalid realm ID.\n`/authsummon <chat_id>`")
            return
    else:
        if message.chat.type == "private":
            await message.reply_text("↯ Private chats cannot be sanctified.")
            return
        target = message.chat.id

    if target in authorized_chats:
        await message.reply_text(f"⟡ This realm is already sanctified.\n`{target}`")
        return

    authorized_chats.add(target)
    log.info("Summon authorized: %d by owner %d", target, message.from_user.id)
    await message.reply_text(
        f"**⟡ Realm Sanctified**\n\n"
        f"`{target}`\n"
        f"Souls will now answer /summon in this group."
    )


@app.on_message(filters.command("unauthsummon"))
async def cmd_unauthsummon(_, message: Message) -> None:
    if not _is_owner(message.from_user.id):
        await message.reply_text("𖤍 This seal is not yours to break.")
        return

    args = message.text.split()
    if len(args) > 1:
        try:
            target = int(args[1])
        except ValueError:
            await message.reply_text("↯ Invalid realm ID.")
            return
    else:
        if message.chat.type == "private":
            await message.reply_text("↯ Specify a group ID.")
            return
        target = message.chat.id

    if target in PERMANENT_AUTHORIZED_CHATS:
        await message.reply_text(
            f"⟡ This realm is permanently bound — the seal cannot be broken.\n`{target}`"
        )
        return
    if target not in authorized_chats:
        await message.reply_text(f"↯ This realm was never sanctified.\n`{target}`")
        return

    authorized_chats.discard(target)
    log.info("Summon authorization removed: %d by owner %d", target, message.from_user.id)
    await message.reply_text(
        f"**𖤍 Seal Dissolved**\n\n"
        f"`{target}`\n"
        f"Souls will no longer respond here."
    )


@app.on_message(filters.command("summonlist"))
async def cmd_summonlist(_, message: Message) -> None:
    if not _is_owner(message.from_user.id):
        await message.reply_text("𖤍 This seal is not yours to read.")
        return
    if not authorized_chats:
        await message.reply_text("⟡ No realms have been sanctified yet.")
        return

    lines = [
        f"`{cid}` — bound forever" if cid in PERMANENT_AUTHORIZED_CHATS else f"`{cid}`"
        for cid in sorted(authorized_chats)
    ]
    await message.reply_text(
        f"**⟡ Sanctified Realms  ·  {len(authorized_chats)}**\n\n" + "\n".join(lines)
    )


@app.on_message(filters.command("reloadsummon"))
async def cmd_reloadsummon(_, message: Message) -> None:
    if not _is_owner(message.from_user.id):
        await message.reply_text("𖤍 This seal is not yours to refresh.")
        return

    global _cache_loaded_at
    _cache_loaded_at = 0.0
    await _do_cache_refresh()
    await message.reply_text(
        f"⟡ **Soul pool refreshed**\n`{len(_character_cache)} spirits standing by`"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  /summon
# ══════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("summon"))
async def cmd_summon(_, message: Message) -> None:
    # /summon is group-only — no DMs
    if message.chat.type == "private":
        await message.reply_text(
            "**𖤍  Sealed Territory**\n\n"
            "Soul rituals must be performed inside a group.\n"
            f"› Community — @{SUPPORT_GROUP}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("↳ Enter the Sanctum", url=f"https://t.me/{SUPPORT_GROUP}")
            ]]),
        )
        return

    user_id = message.from_user.id

    # Register user in DB (no-op if already exists)
    await get_or_create_user(
        user_id,
        message.from_user.username or "",
        message.from_user.first_name or "",
        message.from_user.last_name or "",
    )

    # Ban check
    if await is_user_banned(user_id):
        await message.reply_text("𖤍 Your soul-binding rights have been revoked.")
        return

    # Already in a summon?
    if user_id in _active_summons:
        await message.reply_text(
            "⟡ A spirit is already waiting on your seal.\n"
            "`Resolve it or use /exitsummon to release it.`"
        )
        return

    # Cooldown check
    last_time = _last_summon_times.get(user_id)
    if last_time:
        elapsed = (datetime.now() - last_time).total_seconds()
        if elapsed < SUMMON_COOLDOWN_SECS:
            remaining = int(SUMMON_COOLDOWN_SECS - elapsed)
            await message.reply_text(
                f"𖤍 The ritual circle is still recovering.\n`{remaining}s until the next seal`"
            )
            return

    # Warm up the cache (blocks only on first-ever call)
    await _ensure_cache()
    if not _character_cache:
        await message.reply_text(
            "⟡ The spirit world is quiet right now.\n`No souls could be reached — try again shortly.`"
        )
        return

    # Instant feedback — user sees this while the image downloads
    loading_msg = await message.reply_text("𖤍  _Drawing the seal…_")

    # Try up to MAX_RETRIES different characters; skip any with a dead image URL
    pool      = random.sample(_character_cache, min(MAX_RETRIES, len(_character_cache)))
    character = None
    last_exc  = None

    for candidate in pool:
        img_url  = candidate.get("img_url", "")
        tmp_path = None

        if not img_url:
            continue

        try:
            tmp_path = await _download_to_temp(img_url)
            rarity_str = rarity_display(candidate.get("rarity", ""))

            caption = (
                f"≺  Spirit Detected  ≻\n\n"
                f"**{candidate.get('name', 'Unknown Spirit')}**\n"
                f"` {candidate.get('anime', 'Origin unknown')} `\n\n"
                f"Rarity  ·  {rarity_str}\n\n"
                f"_A restless soul stirs nearby.\n"
                f"Draw the seal before it dissolves._"
            )
            buttons = [[
                InlineKeyboardButton(
                    "✦  Draw the Seal",
                    callback_data=f"summon_begin_{user_id}",
                )
            ]]

            with open(tmp_path, "rb") as fh:
                await message.reply_photo(
                    photo=fh,
                    caption=caption,
                    reply_markup=InlineKeyboardMarkup(buttons),
                )

            character = candidate
            _active_summons[user_id] = character
            log.info("/summon sent  user=%d  char=%s  rarity=%s",
                     user_id, character.get("name"), character.get("rarity"))
            break

        except Exception as exc:
            log.warning(
                "/summon URL failed, trying next  char=%s  url=%r  err=%s",
                candidate.get("name"), img_url, exc,
            )
            last_exc = exc
        finally:
            _safe_delete(tmp_path)

    # Remove the loading message regardless of outcome
    try:
        await loading_msg.delete()
    except Exception:
        pass

    if character is None:
        log.error("/summon: all %d candidates failed for user %d", MAX_RETRIES, user_id)
        await message.reply_text(
            "⟡ The spirits would not answer.\n`All seals dissolved before completing — try again.`"
        )


# ══════════════════════════════════════════════════════════════════════════════
#  BATTLE CALLBACKS
# ══════════════════════════════════════════════════════════════════════════════

@app.on_callback_query(filters.regex(r"^summon_begin_(\d+)$"))
async def cb_summon_begin(_, query) -> None:
    user_id = int(query.matches[0].group(1))

    if query.from_user.id != user_id:
        await _safe_answer(query, "This seal was drawn by another hand.", show_alert=True)
        return
    if user_id not in _active_summons:
        await _safe_answer(query, "The spirit dissolved before you could act.", show_alert=True)
        return

    await query.answer()
    character  = _active_summons[user_id]
    rarity_str = rarity_display(character.get("rarity", ""))

    await _safe_edit_caption(
        query.message,
        f"≺  The Seal Trembles  ≻\n\n"
        f"**{character['name']}** resists your call.\n"
        f"Rarity  ·  {rarity_str}\n\n"
        f"_Will you press the ritual to completion?_",
        [
            [InlineKeyboardButton("𖦹  Bind the Soul",   callback_data=f"summon_engage_{user_id}")],
            [InlineKeyboardButton("↩  Release It",       callback_data=f"summon_retreat_{user_id}")],
        ],
    )


@app.on_callback_query(filters.regex(r"^summon_engage_(\d+)$"))
async def cb_summon_engage(_, query) -> None:
    user_id = int(query.matches[0].group(1))

    if query.from_user.id != user_id:
        await _safe_answer(query, "This ritual belongs to another.", show_alert=True)
        return
    if user_id not in _active_summons:
        await _safe_answer(query, "The seal closed before you could finish.", show_alert=True)
        return

    await query.answer()
    character  = _active_summons[user_id]
    rarity_str = rarity_display(character.get("rarity", ""))

    # Ritual animation — 3 phases × 0.5 s
    for phase in [
        "_⟡  The sigil takes shape…_",
        "_⟡  Threads of fate draw tight…_",
        "_⟡  The binding is cast…_",
    ]:
        await _safe_edit_caption(query.message, phase)
        await asyncio.sleep(0.5)

    stats = _get_user_stats(user_id)
    stats["total_summons"] += 1
    success = random.random() < 0.5

    if success:
        # Add character to user's harem via the SoulCatcher DB layer
        await add_to_harem(user_id, character)

        # Kakera reward scaled to rarity
        rarity_obj   = get_rarity(character.get("rarity", ""))
        kakera_bonus = rarity_obj.kakera_reward if rarity_obj else 10
        await add_balance(user_id, kakera_bonus)

        stats["wins"]      += 1
        stats["streak"]    += 1
        stats["max_streak"] = max(stats["streak"], stats["max_streak"])
        log.info("summon WIN  user=%d  char=%s  rarity=%s  kakera+%d",
                 user_id, character.get("name"), character.get("rarity"), kakera_bonus)

        header = random.choice([
            f"**≺  Soul Bound  ≻**\n\n{character['name']} has been sealed into your collection.",
            f"**≺  Binding Complete  ≻**\n\n{character['name']} surrenders to your will.",
            f"**≺  The Seal Holds  ≻**\n\n{character['name']} is now yours to keep.",
        ])
        await _safe_edit_caption(
            query.message,
            f"{header}\n\n"
            f"Rarity  ·  {rarity_str}\n"
            f"Kakera  ·  +{kakera_bonus} 🪙\n"
            f"Bond  ·  Streak {stats['streak']} 🔗",
        )
    else:
        stats["losses"] += 1
        stats["streak"]  = 0
        log.info("summon LOSS  user=%d  char=%s", user_id, character.get("name"))

        header = random.choice([
            f"**≺  Seal Broken  ≻**\n\n{character['name']} shattered your sigil and fled.",
            f"**≺  The Ritual Failed  ≻**\n\n{character['name']} was too strong to hold.",
            f"**≺  Spirit Unbound  ≻**\n\n{character['name']} dissolved your threads and vanished.",
        ])
        await _safe_edit_caption(
            query.message,
            f"{header}\n\n"
            f"Rarity  ·  {rarity_str}\n\n"
            f"_Redraw the circle and try again._",
        )

    _summon_stats[user_id]      = stats
    _last_summon_times[user_id] = datetime.now()
    _active_summons.pop(user_id, None)


@app.on_callback_query(filters.regex(r"^summon_retreat_(\d+)$"))
async def cb_summon_retreat(_, query) -> None:
    user_id = int(query.matches[0].group(1))

    if query.from_user.id != user_id:
        await _safe_answer(query, "This thread of fate is not yours.", show_alert=True)
        return
    if user_id not in _active_summons:
        await _safe_answer(query, "The soul already drifted away.", show_alert=True)
        return

    await query.answer()
    character = _active_summons[user_id]
    log.info("summon RETREAT  user=%d  char=%s", user_id, character.get("name"))

    await _safe_edit_caption(
        query.message,
        random.choice([
            f"**≺  Seal Released  ≻**\n\nYou unravelled the threads.\n{character['name']} drifts back into the void.",
            f"**≺  Ritual Abandoned  ≻**\n\nThe sigil fades.\n{character['name']} slips through your fingers.",
            f"**≺  The Circle Opens  ≻**\n\nYou let the seal dissolve.\n{character['name']} is free.",
        ]),
    )

    stats           = _get_user_stats(user_id)
    stats["losses"] += 1
    stats["streak"]  = 0
    _summon_stats[user_id]      = stats
    _last_summon_times[user_id] = datetime.now()
    _active_summons.pop(user_id, None)


# ══════════════════════════════════════════════════════════════════════════════
#  /exitsummon — abandon an active summon without penalty
# ══════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("exitsummon"))
async def cmd_exitsummon(_, message: Message) -> None:
    if message.chat.type == "private":
        await message.reply_text("⟡ No seal is active in private chats.")
        return

    user_id = message.from_user.id
    if user_id in _active_summons:
        character = _active_summons.pop(user_id)
        log.info("/exitsummon  user=%d  abandoned=%s", user_id, character.get("name"))
        await message.reply_text(
            f"**≺  Ritual Severed  ≻**\n\n"
            f"_The seal crumbles. {character['name']} returns to the void._"
        )
    else:
        await message.reply_text(
            "⟡ No seal is active.\n`Use /summon to call a spirit.`"
        )
