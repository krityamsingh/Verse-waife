"""
SoulCatcher/modules/summon.py
Commands: /summon  /exitsummon  /reloadsummon  /authgc  /deauthgc  /cool
"""

import asyncio
import logging
import random
from datetime import datetime, timedelta

from pyrogram import filters, enums
from pyrogram.errors import FloodWait, QueryIdInvalid
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from .. import app
from ..config import OWNER_IDS, SUPPORT_GROUP
from ..database import (
    get_or_create_user,
    is_user_banned,
    add_to_harem,
    add_balance,
    get_random_character,
)
from ..rarity import (
    rarity_display,
    get_rarity,
    get_all_rarities,
    get_all_sub_rarities,
)

log = logging.getLogger("SoulCatcher.summon")

# ── Config ────────────────────────────────────────────────────────────────────

EXCLUDED_RARITIES: set[str] = {"eternal", "cartoon"}
SUMMON_COOLDOWN_SECS = 30
MAX_RETRIES          = 7
PITY_THRESHOLD       = 7

# Main sanctum — always allowed, shown in all redirect messages
MAIN_GC_LINK  = "https://t.me/Divine_Catchers"
MAIN_GC_ID    = -1002313549356  # permanent home group

# Auth duration for owner-granted groups
AUTH_DURATION_HOURS = 24

# Extra owner allowed to use /authgc  /deauthgc  /cool  /reloadsummon
_EXTRA_OWNER_ID = 6118760915

# ── In-memory state ───────────────────────────────────────────────────────────

_last_summon:   dict[int, datetime] = {}
_active:        dict[int, dict]     = {}
_stats:         dict[int, dict]     = {}

# { chat_id: datetime_expiry }  — owner-authorised groups (24 h)
_authed_groups: dict[int, datetime] = {}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _esc(t) -> str:
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _get_stats(uid: int) -> dict:
    if uid not in _stats:
        _stats[uid] = {
            "wins": 0, "losses": 0, "streak": 0,
            "max_streak": 0, "total": 0, "pity": 0,
        }
    return _stats[uid]


def _is_owner(uid: int) -> bool:
    return uid in OWNER_IDS or uid == _EXTRA_OWNER_ID


async def _safe_edit(msg: Message, text: str, buttons: list | None = None) -> None:
    markup = InlineKeyboardMarkup(buttons) if buttons else None
    try:
        await msg.edit_caption(
            text,
            reply_markup=markup,
            parse_mode=enums.ParseMode.HTML,
        )
    except FloodWait as e:
        log.warning("FloodWait edit_caption: %ds", e.value)
        await asyncio.sleep(e.value)
        try:
            await msg.edit_caption(
                text,
                reply_markup=markup,
                parse_mode=enums.ParseMode.HTML,
            )
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
        log.warning("safe_answer: %s", e)


def _eligible_rarities() -> list[str]:
    all_r = [r.name for r in get_all_rarities() + get_all_sub_rarities()]
    return [name for name in all_r if name not in EXCLUDED_RARITIES]


def _is_allowed_chat(chat_id: int) -> bool:
    """Return True for the permanent home group OR any owner-authorised group
    whose 24-hour window has not yet expired."""
    if chat_id == MAIN_GC_ID:
        return True
    expiry = _authed_groups.get(chat_id)
    if expiry and datetime.now() < expiry:
        return True
    # Clean up expired entry
    _authed_groups.pop(chat_id, None)
    return False


def _sanctum_button() -> InlineKeyboardMarkup:
    """Single 'Enter the Sanctum' button that always points to the main GC."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("↳ Enter the Sanctum", url=MAIN_GC_LINK)
    ]])


# ── Owner commands ────────────────────────────────────────────────────────────

@app.on_message(filters.command("reloadsummon"))
async def cmd_reloadsummon(_, message: Message) -> None:
    if not _is_owner(message.from_user.id):
        return await message.reply_text(
            "𖤍 Not your seal to refresh.",
            parse_mode=enums.ParseMode.HTML,
        )
    rarities = _eligible_rarities()
    await message.reply_text(
        f"⟡ <b>Summon pool</b>\n"
        f"<code>{len(rarities)} eligible tiers: {', '.join(rarities)}</code>",
        parse_mode=enums.ParseMode.HTML,
    )


import re as _re

# Tracks the auto-reset task so we can cancel it if /cool is called again
_cool_reset_task: asyncio.Task | None = None

DEFAULT_COOLDOWN_SECS = SUMMON_COOLDOWN_SECS  # 30 — used for auto-reset


def _parse_duration(raw: str) -> int | None:
    """Parse a duration string into total seconds.

    Accepted formats (case-insensitive):
        60 / 60s → 60 s
        5m       → 300 s
        1h       → 3600 s
        1h30m    → 5400 s
        2h15m10s → 8110 s
    Returns None on failure.
    """
    raw = raw.strip().lower().replace(" ", "")
    if raw.isdigit():
        return int(raw)
    pattern = _re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", raw)
    if not pattern or not any(pattern.groups()):
        return None
    h = int(pattern.group(1) or 0)
    m = int(pattern.group(2) or 0)
    s = int(pattern.group(3) or 0)
    total = h * 3600 + m * 60 + s
    return total if total >= 0 else None


def _fmt_duration(secs: int) -> str:
    """Return a human-friendly label like '1h 30m' or '45s'."""
    if secs == 0:
        return "0s (no cooldown)"
    h, rem = divmod(secs, 3600)
    m, s   = divmod(rem, 60)
    parts  = []
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if s: parts.append(f"{s}s")
    return " ".join(parts)


async def _schedule_cooldown_reset(new_cool: int, for_secs: int) -> None:
    """Wait for_secs then restore DEFAULT_COOLDOWN_SECS."""
    global SUMMON_COOLDOWN_SECS
    try:
        await asyncio.sleep(for_secs)
        SUMMON_COOLDOWN_SECS = DEFAULT_COOLDOWN_SECS
        log.info("cooldown auto-reset to default %ds after %ds",
                 DEFAULT_COOLDOWN_SECS, for_secs)
    except asyncio.CancelledError:
        pass  # A new /cool call cancelled us — that handler sets the value itself


@app.on_message(filters.command("cool"))
async def cmd_cool(_, message: Message) -> None:
    """Owner-only: set cooldown, optionally for a limited time.

    Usage:
        /cool                  — show current cooldown
        /cool 60s              — set to 60 s permanently
        /cool 60s 1h           — set to 60 s for 1 hour, then reset to 30 s
        /cool 0 2h             — disable cooldown for 2 hours, then reset
        /cool 2m 30m           — set to 2 min for 30 minutes, then reset

    Time formats: 60 · 30s · 5m · 1h · 1h30m · 2h15m10s
    """
    global SUMMON_COOLDOWN_SECS, _cool_reset_task

    if not _is_owner(message.from_user.id):
        return await message.reply_text(
            "𖤍 Only the Archon may alter the ritual timer.",
            parse_mode=enums.ParseMode.HTML,
        )

    parts = message.text.split()[1:]  # drop the command itself

    # ── No args: show status ──────────────────────────────────────────────────
    if not parts:
        if _cool_reset_task and not _cool_reset_task.done():
            # We can't know exact remaining time easily, just note it's temporary
            note = "\n<i>⏳ A timed override is active — will reset to default soon.</i>"
        else:
            note = ""
        return await message.reply_text(
            f"<b>⟡  Ritual Cooldown</b>\n\n"
            f"Current · <code>{_fmt_duration(SUMMON_COOLDOWN_SECS)}</code>{note}\n\n"
            f"<i>Usage: <code>/cool &lt;cooldown&gt;</code> or "
            f"<code>/cool &lt;cooldown&gt; &lt;duration&gt;</code>\n"
            f"e.g. <code>/cool 60s 1h</code></i>",
            parse_mode=enums.ParseMode.HTML,
        )

    # ── Parse cooldown value (first arg) ─────────────────────────────────────
    new_cool = _parse_duration(parts[0])
    if new_cool is None:
        return await message.reply_text(
            "<b>⟡  Invalid cooldown value</b>\n\n"
            "Examples: <code>60</code> · <code>30s</code> · <code>5m</code> · <code>1h30m</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    # ── Parse optional "for how long" (second arg) ───────────────────────────
    for_secs: int | None = None
    if len(parts) >= 2:
        for_secs = _parse_duration(parts[1])
        if for_secs is None or for_secs <= 0:
            return await message.reply_text(
                "<b>⟡  Invalid duration</b>\n\n"
                "Examples: <code>1h</code> · <code>30m</code> · <code>2h30m</code>",
                parse_mode=enums.ParseMode.HTML,
            )

    # ── Cancel any running auto-reset ────────────────────────────────────────
    if _cool_reset_task and not _cool_reset_task.done():
        _cool_reset_task.cancel()
        _cool_reset_task = None

    old_val = SUMMON_COOLDOWN_SECS
    SUMMON_COOLDOWN_SECS = new_cool

    log.info("cooldown changed  owner=%d  %ds → %ds  for=%s",
             message.from_user.id, old_val, new_cool,
             f"{for_secs}s" if for_secs else "permanent")

    # ── Schedule auto-reset if a duration was given ───────────────────────────
    if for_secs:
        _cool_reset_task = asyncio.create_task(
            _schedule_cooldown_reset(new_cool, for_secs)
        )
        await message.reply_text(
            f"<b>≺  Cooldown Overridden  ≻</b>\n\n"
            f"<code>{_fmt_duration(old_val)}  →  {_fmt_duration(new_cool)}</code>\n"
            f"⏳ Resets to default (<code>{_fmt_duration(DEFAULT_COOLDOWN_SECS)}</code>) "
            f"after <b>{_fmt_duration(for_secs)}</b>.",
            parse_mode=enums.ParseMode.HTML,
        )
    else:
        await message.reply_text(
            f"<b>≺  Cooldown Updated  ≻</b>\n\n"
            f"<code>{_fmt_duration(old_val)}  →  {_fmt_duration(new_cool)}</code>\n\n"
            f"<i>The ritual circle now recovers in <b>{_fmt_duration(new_cool)}</b>.</i>",
            parse_mode=enums.ParseMode.HTML,
        )


@app.on_message(filters.command("authgc"))
async def cmd_authgc(_, message: Message) -> None:
    """Owner-only: authorise the current group for AUTH_DURATION_HOURS hours.
    Usage (in target group): /authgc
    """
    if not _is_owner(message.from_user.id):
        return await message.reply_text(
            "𖤍 Only the Archon may grant sanctum rights.",
            parse_mode=enums.ParseMode.HTML,
        )

    if message.chat.type == enums.ChatType.PRIVATE:
        return await message.reply_text(
            "⟡ Use <code>/authgc</code> inside the group you want to authorise.",
            parse_mode=enums.ParseMode.HTML,
        )

    chat_id = message.chat.id
    expiry  = datetime.now() + timedelta(hours=AUTH_DURATION_HOURS)
    _authed_groups[chat_id] = expiry

    log.info("authgc  owner=%d  chat=%d  expiry=%s",
             message.from_user.id, chat_id, expiry.isoformat())

    await message.reply_text(
        f"<b>≺  Sanctum Opened  ≻</b>\n\n"
        f"This group has been granted summon access for "
        f"<b>{AUTH_DURATION_HOURS} hours</b>.\n"
        f"<code>Expires: {expiry.strftime('%Y-%m-%d %H:%M:%S')}</code>",
        parse_mode=enums.ParseMode.HTML,
    )


@app.on_message(filters.command("deauthgc"))
async def cmd_deauthgc(_, message: Message) -> None:
    """Owner-only: immediately revoke a group's temporary authorisation."""
    if not _is_owner(message.from_user.id):
        return await message.reply_text(
            "𖤍 Only the Archon may seal sanctum rights.",
            parse_mode=enums.ParseMode.HTML,
        )

    if message.chat.type == enums.ChatType.PRIVATE:
        return await message.reply_text(
            "⟡ Use <code>/deauthgc</code> inside the group you want to revoke.",
            parse_mode=enums.ParseMode.HTML,
        )

    chat_id = message.chat.id
    if chat_id == MAIN_GC_ID:
        return await message.reply_text(
            "⟡ The main sanctum cannot be revoked.",
            parse_mode=enums.ParseMode.HTML,
        )

    if _authed_groups.pop(chat_id, None):
        log.info("deauthgc  owner=%d  chat=%d", message.from_user.id, chat_id)
        await message.reply_text(
            "<b>≺  Sanctum Sealed  ≻</b>\n\n"
            "Summon access for this group has been <b>revoked</b>.",
            parse_mode=enums.ParseMode.HTML,
        )
    else:
        await message.reply_text(
            "⟡ This group had no active authorisation.",
            parse_mode=enums.ParseMode.HTML,
        )


# ── /summon ───────────────────────────────────────────────────────────────────

@app.on_message(filters.command("summon"))
async def cmd_summon(_, message: Message) -> None:
    # ── Private chat ──────────────────────────────────────────────────────────
    if message.chat.type == enums.ChatType.PRIVATE:
        return await message.reply_text(
            f"<b>𖤍  Sealed Territory</b>\n\n"
            f"Soul rituals must be performed inside the sanctum group.\n"
            f"› Join us at {MAIN_GC_LINK}",
            parse_mode=enums.ParseMode.HTML,
            reply_markup=_sanctum_button(),
        )

    # ── Unauthorised group ────────────────────────────────────────────────────
    if not _is_allowed_chat(message.chat.id):
        return await message.reply_text(
            "<b>𖤍  Forbidden Ground</b>\n\n"
            "<i>The ritual circle does not extend here.\n"
            "Soul-binding is sealed to the one true sanctum.</i>\n\n"
            f"› {MAIN_GC_LINK}",
            parse_mode=enums.ParseMode.HTML,
            reply_markup=_sanctum_button(),
        )

    if not message.from_user:
        return

    user_id = message.from_user.id

    await get_or_create_user(
        user_id,
        message.from_user.username or "",
        message.from_user.first_name or "",
        message.from_user.last_name or "",
    )

    if await is_user_banned(user_id):
        return await message.reply_text(
            "𖤍 Your soul-binding rights have been revoked.",
            parse_mode=enums.ParseMode.HTML,
        )

    if user_id in _active:
        return await message.reply_text(
            "⟡ A spirit is already waiting on your seal.\n"
            "<code>Resolve it or use /exitsummon to release it.</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    last = _last_summon.get(user_id)
    if last:
        elapsed = (datetime.now() - last).total_seconds()
        if elapsed < SUMMON_COOLDOWN_SECS:
            remaining = int(SUMMON_COOLDOWN_SECS - elapsed)
            return await message.reply_text(
                f"𖤍 The ritual circle is still recovering.\n"
                f"<code>{remaining}s until the next seal</code>",
                parse_mode=enums.ParseMode.HTML,
            )

    eligible = _eligible_rarities()
    if not eligible:
        return await message.reply_text(
            "⟡ The spirit world is quiet right now.",
            parse_mode=enums.ParseMode.HTML,
        )

    loading_msg = await message.reply_text(
        "𖤍  <i>Drawing the seal…</i>",
        parse_mode=enums.ParseMode.HTML,
    )

    character  = None
    rarity_str = ""

    for _ in range(MAX_RETRIES):
        rarity_name = random.choice(eligible)
        char = await get_random_character(rarity_name)
        if not char or not char.get("img_url"):
            continue
        character  = char
        rarity_str = rarity_display(character.get("rarity", rarity_name))
        break

    try:
        await loading_msg.delete()
    except Exception:
        pass

    if not character:
        return await message.reply_text(
            "⟡ The spirits would not answer.\n"
            "<code>No souls could be reached — try again.</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    caption = (
        f"≺  Spirit Detected  ≻\n\n"
        f"<b>{_esc(character.get('name', 'Unknown Spirit'))}</b>\n"
        f"<code>{_esc(character.get('anime', 'Origin unknown'))}</code>\n\n"
        f"Rarity  ·  {_esc(rarity_str)}\n\n"
        f"<i>A restless soul stirs nearby.\n"
        f"Draw the seal before it dissolves.</i>"
    )

    try:
        await message.reply_photo(
            photo=character["img_url"],
            caption=caption,
            parse_mode=enums.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "✦  Draw the Seal",
                    callback_data=f"summon_begin_{user_id}",
                )
            ]]),
        )
        _active[user_id] = character
        log.info("/summon  user=%d  char=%s  rarity=%s",
                 user_id, character.get("name"), character.get("rarity"))
    except Exception as e:
        log.error("/summon reply_photo failed  user=%d  err=%s", user_id, e)
        await message.reply_text(
            "⟡ The seal dissolved before it could form.\n"
            "<code>Try again shortly.</code>",
            parse_mode=enums.ParseMode.HTML,
        )


# ── /exitsummon ───────────────────────────────────────────────────────────────

@app.on_message(filters.command("exitsummon"))
async def cmd_exitsummon(_, message: Message) -> None:
    # Silently ignore in private or unauthorised groups
    if message.chat.type == enums.ChatType.PRIVATE or not _is_allowed_chat(message.chat.id):
        return

    if not message.from_user:
        return

    user_id = message.from_user.id
    if user_id in _active:
        char = _active.pop(user_id)
        log.info("/exitsummon  user=%d  abandoned=%s", user_id, char.get("name"))
        await message.reply_text(
            f"<b>≺  Ritual Severed  ≻</b>\n\n"
            f"<i>The seal crumbles. {_esc(char['name'])} returns to the void.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    else:
        await message.reply_text(
            "⟡ No seal is active.\n<code>Use /summon to call a spirit.</code>",
            parse_mode=enums.ParseMode.HTML,
        )


# ── Callbacks ─────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^summon_begin_(\d+)$"))
async def cb_summon_begin(_, query) -> None:
    user_id = int(query.matches[0].group(1))

    if query.from_user.id != user_id:
        return await _safe_answer(query, "This seal was drawn by another hand.", alert=True)
    if user_id not in _active:
        return await _safe_answer(query, "The spirit dissolved before you could act.", alert=True)

    await query.answer()
    char       = _active[user_id]
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
    if user_id not in _active:
        return await _safe_answer(query, "The seal closed before you could finish.", alert=True)

    await query.answer()
    char       = _active.pop(user_id)
    rarity_str = rarity_display(char.get("rarity", ""))
    stats      = _get_stats(user_id)
    stats["total"] += 1
    stats["pity"]  += 1

    for phase in [
        "⟡  <i>The sigil takes shape…</i>",
        "⟡  <i>Threads of fate draw tight…</i>",
        "⟡  <i>The binding is cast…</i>",
    ]:
        await _safe_edit(query.message, phase)
        await asyncio.sleep(0.5)

    success = stats["pity"] >= PITY_THRESHOLD or random.random() < 0.5

    if success:
        stats["pity"] = 0
        await add_to_harem(user_id, char)

        robj         = get_rarity(char.get("rarity", ""))
        kakera_bonus = robj.kakera_reward if robj else 10
        await add_balance(user_id, kakera_bonus)

        stats["wins"]      += 1
        stats["streak"]    += 1
        stats["max_streak"] = max(stats["streak"], stats["max_streak"])
        log.info("summon WIN  user=%d  char=%s  kakera+%d",
                 user_id, char.get("name"), kakera_bonus)

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

    _last_summon[user_id] = datetime.now()


@app.on_callback_query(filters.regex(r"^summon_retreat_(\d+)$"))
async def cb_summon_retreat(_, query) -> None:
    user_id = int(query.matches[0].group(1))

    if query.from_user.id != user_id:
        return await _safe_answer(query, "This thread of fate is not yours.", alert=True)
    if user_id not in _active:
        return await _safe_answer(query, "The soul already drifted away.", alert=True)

    await query.answer()
    char = _active.pop(user_id)
    log.info("summon RETREAT  user=%d  char=%s", user_id, char.get("name"))

    stats           = _get_stats(user_id)
    stats["losses"] += 1
    stats["streak"]  = 0
    _last_summon[user_id] = datetime.now()

    await _safe_edit(
        query.message,
        random.choice([
            f"<b>≺  Seal Released  ≻</b>\n\n"
            f"You unravelled the threads.\n{_esc(char['name'])} drifts back into the void.",
            f"<b>≺  Ritual Abandoned  ≻</b>\n\n"
            f"The sigil fades.\n{_esc(char['name'])} slips through your fingers.",
            f"<b>≺  The Circle Opens  ≻</b>\n\n"
            f"You let the seal dissolve.\n{_esc(char['name'])} is free.",
        ]),
    )
