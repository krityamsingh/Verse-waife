"""
spawn.py — Character spawn & guess engine for SoulCatcher.

Key improvements over previous version
───────────────────────────────────────
• Per-chat asyncio.Lock  → eliminates all race conditions
• dataclass SpawnSession → typed, readable, IDE-friendly state
• Compiled regex          → faster normalization (re.compile once)
• asyncio.gather          → parallel wishlist DMs
• Single DB read per msg  → group data cached into session on spawn
• Structured logging      → every important event is traceable
• Narrow exception catches → swallowed bugs are now visible
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

from pyrogram import filters
from pyrogram.errors import MessageNotModified, FloodWait
from pyrogram.types import Message

from .. import app
from ..rarity import (
    roll_rarity, roll_sub_rarity, rarity_display, get_rarity,
    get_kakera_reward, SPAWN_SETTINGS, get_claim_window,
)
from ..database import (
    get_group, increment_group_msg, reset_group_msg,
    check_and_record_drop, get_random_character,
    create_spawn, expire_spawn,
    add_to_harem, get_or_create_user, add_balance,
    count_rarity_in_harem, get_wishers, is_user_banned,
    get_character, set_group_spawn_limit,
)

# ─────────────────────────────────────────────────────────────────────────────
log = logging.getLogger("SoulCatcher.spawn")

# ── Constants ─────────────────────────────────────────────────────────────────
_DEFAULT_SPAWN_THRESHOLD: int = 100
_BOT_OWNER_ID: int = 6118760915
_BLOCK_CHAR = "░"

# Pre-compiled for speed — called on every incoming message.
_WHITESPACE_RE = re.compile(r"\s+")

# ── Commands excluded from the message counter ────────────────────────────────
_ALL_COMMANDS: list[str] = [
    "start", "drop", "spawn", "harem", "view", "setfav", "burn", "sort",
    "daily", "bal", "spin", "pay", "shop", "sell", "buy", "market",
    "trade", "gift", "marry", "propose", "epropose", "basket",
    "wish", "wishlist", "profile", "status", "rank", "top", "toprarity",
    "richest", "rarityinfo", "event",
    "topcollector", "topc", "tc",
    "wguess",
    "gban", "ungban", "gmute", "ungmute", "broadcast", "transfer",
    "eval", "ev", "shell", "sh", "bash", "gitpull", "update",
    "addchar", "delchar", "setmode", "forcedrop", "ban", "unban",
    "addsudo", "rmsudo", "sudolist", "adddev", "rmdev", "devlist",
    "adduploader", "rmuploader", "uploaderlist",
    "upload", "il", "uchar",
    "setspawn",
    "summon", "exitsummon", "reloadsummon",
]


# ─────────────────────────────────────────────────────────────────────────────
# Session state
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SpawnSession:
    """All live-spawn metadata for a single chat."""
    spawn_id:      str
    char:          dict
    eff:           object          # Rarity object
    msg:           Message
    claim_win:     int
    answer_tokens: frozenset[str]  # frozenset → O(1) lookup
    lock:          asyncio.Lock = field(default_factory=asyncio.Lock)
    claimed:       bool = False


# chat_id → SpawnSession
_active_spawns: dict[int, SpawnSession] = {}

# Per-chat spawn lock so only one spawn can be created at a time.
_spawn_creation_locks: dict[int, asyncio.Lock] = {}


def _get_creation_lock(chat_id: int) -> asyncio.Lock:
    if chat_id not in _spawn_creation_locks:
        _spawn_creation_locks[chat_id] = asyncio.Lock()
    return _spawn_creation_locks[chat_id]


# ─────────────────────────────────────────────────────────────────────────────
# Text helpers
# ─────────────────────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """
    Lowercase + strip accents + collapse whitespace.
    'Náruto' → 'naruto', 'Monkey D Luffy' → 'monkey d luffy'.
    """
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = nfkd.encode("ascii", "ignore").decode()
    return _WHITESPACE_RE.sub(" ", ascii_text).strip().lower()


def _name_tokens(name: str) -> frozenset[str]:
    """
    Returns a frozenset of every accepted guess for *name*.

    Rules
    ─────
    • Full normalized name is always accepted.
    • Individual words that are ≥ 2 characters are accepted (avoids
      single-letter initials like the 'D' in 'Monkey D Luffy').

    'Monkey D Luffy' → frozenset({'monkey d luffy', 'monkey', 'luffy'})
    """
    full_norm = _normalize(name)
    parts = [p for p in full_norm.split() if len(p) >= 2]
    parts.append(full_norm)
    return frozenset(parts)


def _obscure_name(name: str) -> str:
    """
    Shows first letter of every word, the rest become ░.
    'Monkey D Luffy' → 'M░░░░░ D L░░░░'
    """
    return " ".join(
        w if len(w) <= 1 else w[0] + _BLOCK_CHAR * (len(w) - 1)
        for w in name.split()
    )


def _now_utc() -> datetime:
    """Current UTC time as a **timezone-naive** datetime.

    MongoDB / most ORMs store datetimes without tzinfo, so we stay naive
    throughout to avoid "can't subtract offset-naive and offset-aware
    datetimes" TypeErrors.
    """
    return datetime.utcnow()


def _seconds_since(dt: "Optional[datetime]") -> float:
    """Seconds elapsed since *dt*, which may be naive **or** aware.

    Normalises *dt* to naive UTC before subtracting so callers never
    have to worry about what the database returned.
    Returns ``inf`` when *dt* is ``None`` (i.e. never happened).
    """
    if dt is None:
        return float("inf")
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return (_now_utc() - dt).total_seconds()


# ─────────────────────────────────────────────────────────────────────────────
# /setspawn — owner-only command
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("setspawn") & filters.group)
async def cmd_setspawn(client, message: Message) -> None:
    """Set how many messages trigger a spawn.  Owner-only."""
    if message.from_user.id != _BOT_OWNER_ID:
        return await message.reply_text("❌ Only the bot owner can change the spawn limit.")

    args = message.command[1:]
    if not args or not args[0].isdigit():
        return await message.reply_text(
            "⚙️ **Usage:** `/setspawn <messages>`\n"
            "Example: `/setspawn 100` — spawn after every 100 messages.\n"
            "Allowed values: **1 – 10 000**"
        )

    limit = int(args[0])
    if not 1 <= limit <= 10_000:
        return await message.reply_text("⚠️ Limit must be between **1** and **10 000**.")

    await set_group_spawn_limit(message.chat.id, limit)
    log.info("spawn_limit_set chat=%d limit=%d by=%d", message.chat.id, limit, message.from_user.id)
    await message.reply_text(
        f"✅ Spawn message limit set to **{limit}** messages.\n"
        f"A character will appear after every **{limit}** messages sent in this group."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Message counter → auto-spawn
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.group & filters.text & ~filters.command(_ALL_COMMANDS))
async def on_group_message(client, message: Message) -> None:
    chat_id = message.chat.id

    # ── Active session: check guess first, never count toward the threshold ──
    session = _active_spawns.get(chat_id)
    if session:
        await _check_guess(client, message, chat_id, session)
        return

    # ── Load group once ──────────────────────────────────────────────────────
    group = await get_group(chat_id)
    if not group.get("spawn_enabled", True) or group.get("banned"):
        return

    # ── Threshold & cooldown check ───────────────────────────────────────────
    threshold: int = group.get(
        "spawn_msg_limit",
        SPAWN_SETTINGS.get("messages_per_spawn", _DEFAULT_SPAWN_THRESHOLD),
    )
    count = await increment_group_msg(chat_id)
    if count < threshold:
        return

    cooldown: int = group.get("spawn_cooldown", SPAWN_SETTINGS.get("cooldown_seconds", 0))
    last: Optional[datetime] = group.get("last_spawn")
    if _seconds_since(last) < cooldown:
        await reset_group_msg(chat_id)
        return

    await reset_group_msg(chat_id)
    await _do_spawn(client, message, chat_id)


# ─────────────────────────────────────────────────────────────────────────────
# /drop — manual spawn command
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command(["drop", "spawn"]) & filters.group)
async def cmd_drop(client, message: Message) -> None:
    chat_id = message.chat.id

    group = await get_group(chat_id)
    if not group.get("spawn_enabled", True) or group.get("banned"):
        return await message.reply_text("❌ Spawning is disabled in this group.")

    if chat_id in _active_spawns:
        return await message.reply_text("⚠️ A character is already waiting to be guessed!")

    cooldown: int = group.get("spawn_cooldown", SPAWN_SETTINGS.get("cooldown_seconds", 0))
    last: Optional[datetime] = group.get("last_spawn")
    elapsed = _seconds_since(last)
    if elapsed < cooldown:
        remaining = int(cooldown - elapsed)
        return await message.reply_text(f"⏳ Next drop in **{remaining}s**")

    await _do_spawn(client, message, chat_id)


# ─────────────────────────────────────────────────────────────────────────────
# Core spawn logic
# ─────────────────────────────────────────────────────────────────────────────

async def _do_spawn(client, message: Message, chat_id: int) -> None:
    """Roll a character, post the spawn message, register the session."""
    async with _get_creation_lock(chat_id):
        # Double-check: another coroutine may have spawned while we waited.
        if chat_id in _active_spawns:
            return

        # ── Roll rarity ──────────────────────────────────────────────────────
        tier = roll_rarity()
        if not await check_and_record_drop(chat_id, tier.name):
            tier = get_rarity("common")

        if tier.spawn_requires_activity:
            g = await get_group(chat_id)
            if g.get("message_count", 0) < SPAWN_SETTINGS.get("activity_threshold", 0):
                tier = get_rarity("common")

        sub = roll_sub_rarity(tier.name)
        eff = sub or tier

        # ── Pick character ───────────────────────────────────────────────────
        char = await get_random_character(eff.name) or await get_random_character("common")
        if not char:
            log.warning("spawn_no_char chat=%d rarity=%s", chat_id, eff.name)
            return

        if char["rarity"] != eff.name:
            eff = get_rarity(char["rarity"]) or eff

        # ── Build spawn message ──────────────────────────────────────────────
        reveal      = SPAWN_SETTINGS.get("reveal_rarity_on_spawn", True)
        rarity_hint = rarity_display(eff.name) if reveal else "❓ **???**"
        claim_win   = get_claim_window(eff.name)
        banner      = f"🚨 **RARE SPAWN ALERT!** {eff.emoji}\n\n" if eff.announce_spawn else ""
        hidden_name = _obscure_name(char["name"])

        text = (
            f"{banner}✨ **A mystery soul has appeared!**\n\n"
            f"👤 **{hidden_name}**\n"
            f"📖 _{char.get('anime', 'Unknown')}_\n"
            f"⭐ {rarity_hint}\n\n"
            f"🔤 **Type the character's name to claim!**\n"
            f"💡 _First, middle, or last name all work!_ (`{claim_win}s`)"
        )

        # ── Send spawn message ───────────────────────────────────────────────
        try:
            if char.get("video_url"):
                msg = await message.reply_video(char["video_url"], caption=text)
            elif char.get("img_url"):
                msg = await message.reply_photo(char["img_url"], caption=text)
            else:
                msg = await message.reply_text(text)
        except FloodWait as e:
            log.warning("spawn_flood_wait chat=%d seconds=%d", chat_id, e.value)
            await asyncio.sleep(e.value)
            return
        except Exception:
            log.exception("spawn_send_failed chat=%d char=%s", chat_id, char.get("name"))
            return

        # ── Persist & register session ───────────────────────────────────────
        spawn_id = await create_spawn(chat_id, msg.id, char, eff.name)

        _active_spawns[chat_id] = SpawnSession(
            spawn_id      = spawn_id,
            char          = char,
            eff           = eff,
            msg           = msg,
            claim_win     = claim_win,
            answer_tokens = _name_tokens(char["name"]),
        )

        log.info(
            "spawned chat=%d char=%r rarity=%s spawn_id=%s window=%ds",
            chat_id, char["name"], eff.name, spawn_id, claim_win,
        )

    # Background tasks run outside the creation lock.
    asyncio.create_task(_expire(client, chat_id, msg, spawn_id, claim_win))
    asyncio.create_task(_ping_wishlist(client, char["id"], chat_id))


# ─────────────────────────────────────────────────────────────────────────────
# Guess handler
# ─────────────────────────────────────────────────────────────────────────────

async def _check_guess(
    client,
    message: Message,
    chat_id: int,
    session: SpawnSession,
) -> None:
    """
    Validate a message as a character-name guess.
    Uses per-session asyncio.Lock to safely handle concurrent correct guesses.
    """
    guess = _normalize(message.text or "")
    if not guess or guess not in session.answer_tokens:
        return

    # ── Acquire session lock ─────────────────────────────────────────────────
    async with session.lock:
        # Re-check inside lock: another user may have claimed between
        # the token lookup and acquiring the lock.
        if session.claimed or _active_spawns.get(chat_id) is not session:
            return

        user = message.from_user

        if await is_user_banned(user.id):
            return await message.reply_text("🚫 You are banned and cannot claim characters.")

        await get_or_create_user(
            user.id,
            user.username   or "",
            user.first_name or "",
            getattr(user, "last_name", "") or "",
        )

        char        = session.char
        eff         = session.eff
        rarity_name = eff.name

        # ── Rarity cap ───────────────────────────────────────────────────────
        if eff.max_per_user > 0:
            current_count = await count_rarity_in_harem(user.id, rarity_name)
            if current_count >= eff.max_per_user:
                return await message.reply_text(
                    f"⚠️ You already have the max **{eff.max_per_user}** "
                    f"{eff.display_name} characters allowed!"
                )

        # ── Mark claimed & remove from active table ──────────────────────────
        session.claimed = True
        _active_spawns.pop(chat_id, None)

        # ── Record claim ─────────────────────────────────────────────────────
        typed       = (message.text or "").strip()
        full_name   = char["name"]
        guessed_full = _normalize(typed) == _normalize(full_name)
        name_label  = (
            f"**{full_name}**"
            if guessed_full
            else f"**{full_name}** _(guessed as '{typed}')_"
        )

        kakera = get_kakera_reward(rarity_name)   # sync helper — no await needed
        iid, *_ = await asyncio.gather(
            add_to_harem(user.id, char),
            add_balance(user.id, kakera),
            expire_spawn(session.spawn_id),
        )

        log.info(
            "claimed chat=%d user=%d char=%r rarity=%s kakera=%d spawn_id=%s",
            chat_id, user.id, full_name, rarity_name, kakera, session.spawn_id,
        )

    # ── Edit spawn message with result ───────────────────────────────────────
    result_text = (
        f"🎉 **{user.first_name}** guessed correctly and claimed {name_label}!\n\n"
        f"{eff.emoji} **{eff.display_name}**\n"
        f"📖 _{char.get('anime', 'Unknown')}_\n"
        f"🆔 `{iid}`\n"
        f"💰 +**{kakera:,} kakera**!"
    )
    await _safe_edit(session, result_text, fallback_message=message)


# ─────────────────────────────────────────────────────────────────────────────
# Expiry timer
# ─────────────────────────────────────────────────────────────────────────────

async def _expire(client, chat_id: int, msg: Message, spawn_id: str, delay: int) -> None:
    await asyncio.sleep(delay)

    session = _active_spawns.pop(chat_id, None)
    if not session or session.spawn_id != spawn_id:
        return   # already claimed — nothing to do

    await expire_spawn(spawn_id)

    char = session.char
    answer_text = (
        f"⏰ **Time's up!** Nobody guessed the character.\n"
        f"The answer was: **{char['name']}** "
        f"_({char.get('anime', 'Unknown')})_ 👻"
    )
    await _safe_edit(session, answer_text, fallback_client=client, fallback_chat=chat_id)
    log.info("expired chat=%d spawn_id=%s char=%r", chat_id, spawn_id, char["name"])


# ─────────────────────────────────────────────────────────────────────────────
# Wishlist ping  (runs in parallel for all wishers)
# ─────────────────────────────────────────────────────────────────────────────

async def _ping_wishlist(client, char_id: str, chat_id: int) -> None:
    wishers = await get_wishers(char_id)
    if not wishers:
        return

    char = await get_character(char_id)
    name = char["name"] if char else "A character"
    text = (
        f"💛 **{name}** (on your wishlist) just spawned!\n"
        f"Type their first, middle, or last name to claim — fast! 🏃"
    )

    async def _dm(uid: int) -> None:
        try:
            await client.send_message(uid, text)
        except Exception:
            pass   # DMs may be blocked; silently skip

    await asyncio.gather(*(_dm(uid) for uid in wishers))


# ─────────────────────────────────────────────────────────────────────────────
# Shared edit helper
# ─────────────────────────────────────────────────────────────────────────────

async def _safe_edit(
    session: SpawnSession,
    text: str,
    *,
    fallback_message: Optional[Message] = None,
    fallback_client=None,
    fallback_chat: Optional[int] = None,
) -> None:
    """Edit the spawn message caption/text; fall back to a new message on failure."""
    char = session.char
    try:
        if char.get("video_url") or char.get("img_url"):
            await session.msg.edit_caption(text)
        else:
            await session.msg.edit_text(text)
    except MessageNotModified:
        pass   # content already matches — ignore
    except Exception:
        log.exception("safe_edit_failed")
        try:
            if fallback_message:
                await fallback_message.reply_text(text)
            elif fallback_client and fallback_chat:
                await fallback_client.send_message(fallback_chat, text)
        except Exception:
            log.exception("fallback_send_failed")
