import asyncio
import logging
import re
import unicodedata
from datetime import datetime

from pyrogram import filters
from pyrogram.types import Message

from .. import app
from ..rarity import (
    roll_rarity, roll_sub_rarity, rarity_display, get_rarity,
    get_kakera_reward, SPAWN_SETTINGS, get_claim_window,
)
from ..database import (
    get_group, increment_group_msg, reset_group_msg,
    check_and_record_drop, get_random_character,
    create_spawn, expire_spawn, unclaim_spawn,
    add_to_harem, get_or_create_user, add_balance,
    count_rarity_in_harem, get_wishers, is_user_banned,
    get_character, set_group_spawn_limit,
)

log = logging.getLogger("SoulCatcher.spawn")

# ── Default message threshold override ────────────────────────────────────────
# If SPAWN_SETTINGS doesn't define messages_per_spawn we fall back to 100.
_DEFAULT_SPAWN_THRESHOLD = 100

# ── Active guess sessions ──────────────────────────────────────────────────────
# Maps  chat_id  →  dict with all live-spawn metadata.
# Cleared the moment a correct guess lands or the expiry timer fires.
_active_spawns: dict[int, dict] = {}

# ── All commands excluded from the message counter ────────────────────────────
_ALL_COMMANDS = [
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
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """
    Lowercase + strip accents + collapse whitespace so guesses like
    'Naruto', 'náruto', 'NARUTO', and 'n a r u t o' all match.
    """
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = nfkd.encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", ascii_text).strip().lower()


def _name_tokens(name: str) -> list[str]:
    """
    Returns every normalized token (word) in a character name PLUS
    the full normalized name itself.

    'Monkey D Luffy'  →  ['monkey', 'd', 'luffy', 'monkey d luffy']

    This lets users claim with just 'Luffy', 'Monkey', or the full name.
    Single-character tokens (like the 'D' in 'Monkey D Luffy') are only
    accepted when they are at least 2 characters long to avoid false
    positives from stray letters in chat.
    """
    full_norm = _normalize(name)
    parts = full_norm.split()
    # Keep tokens that are at least 2 chars so lone initials don't trigger.
    tokens = [p for p in parts if len(p) >= 2]
    tokens.append(full_norm)           # always allow the full name too
    return list(dict.fromkeys(tokens)) # deduplicate while preserving order


def _obscure_name(name: str) -> str:
    """
    Shows only the first letter of each word; everything else becomes ░.
    e.g. 'Monkey D Luffy'  →  'M░░░░░ D L░░░░'
    """
    parts = []
    for word in name.split():
        if len(word) <= 1:
            parts.append(word)
        else:
            parts.append(word[0] + "░" * (len(word) - 1))
    return " ".join(parts)


def _match_guess(guess_norm: str, session: dict) -> bool:
    """
    Returns True if the normalised guess matches any accepted token
    stored in the session (full name or any individual name part).
    """
    return guess_norm in session["answer_tokens"]


# ── /setspawn — owner command ──────────────────────────────────────────────────

@app.on_message(filters.command("setspawn") & filters.group)
async def cmd_setspawn(client, message: Message):
    """
    Usage:  /setspawn <number>
    Sets how many messages must be sent before a character spawns.
    Restricted to the bot owner.
    """
    user_id = message.from_user.id
    chat_id = message.chat.id

    if user_id != 6118760915:
        return await message.reply_text("❌ Only the bot owner can change the spawn limit.")

    args = message.command[1:]
    if not args or not args[0].isdigit():
        return await message.reply_text(
            "⚙️ **Usage:** `/setspawn <messages>`\n"
            "Example: `/setspawn 100` — spawn after every 100 messages.\n"
            "Allowed values: **1 – 10 000**"
        )

    limit = int(args[0])
    if not (1 <= limit <= 10_000):
        return await message.reply_text("⚠️ Limit must be between **1** and **10 000**.")

    await set_group_spawn_limit(chat_id, limit)
    await message.reply_text(
        f"✅ Spawn message limit set to **{limit}** messages.\n"
        f"A character will appear after every **{limit}** messages sent in this group."
    )


# ── Message counter → auto-spawn ──────────────────────────────────────────────

@app.on_message(filters.group & filters.text & ~filters.command(_ALL_COMMANDS))
async def on_group_message(client, message: Message):
    chat_id = message.chat.id

    # ── Check for an active guess session first ──────────────────────────────
    session = _active_spawns.get(chat_id)
    if session:
        await _check_guess(client, message, chat_id, session)
        # Don't count guessing messages toward the spawn counter.
        return

    # ── Guard: disabled / banned groups ─────────────────────────────────────
    group = await get_group(chat_id)
    if not group.get("spawn_enabled", True) or group.get("banned"):
        return

    # ── Increment and check threshold ───────────────────────────────────────
    # Priority: group custom limit → SPAWN_SETTINGS → hard default of 100
    threshold = group.get(
        "spawn_msg_limit",
        SPAWN_SETTINGS.get("messages_per_spawn", _DEFAULT_SPAWN_THRESHOLD),
    )
    count = await increment_group_msg(chat_id)
    if count < threshold:
        return

    last     = group.get("last_spawn")
    cooldown = group.get("spawn_cooldown", SPAWN_SETTINGS.get("cooldown_seconds", 0))
    if last and (datetime.utcnow() - last).total_seconds() < cooldown:
        await reset_group_msg(chat_id)
        return

    await reset_group_msg(chat_id)
    await _do_spawn(client, message, chat_id)


# ── /drop command ─────────────────────────────────────────────────────────────

@app.on_message(filters.command(["drop", "spawn"]) & filters.group)
async def cmd_drop(client, message: Message):
    chat_id = message.chat.id

    group = await get_group(chat_id)
    if not group.get("spawn_enabled", True) or group.get("banned"):
        return await message.reply_text("❌ Spawning is disabled in this group.")

    if chat_id in _active_spawns:
        return await message.reply_text("⚠️ A character is already waiting to be guessed!")

    last     = group.get("last_spawn")
    cooldown = group.get("spawn_cooldown", SPAWN_SETTINGS.get("cooldown_seconds", 0))
    if last and (datetime.utcnow() - last).total_seconds() < cooldown:
        rem = int(cooldown - (datetime.utcnow() - last).total_seconds())
        return await message.reply_text(f"⏳ Next drop in **{rem}s**")

    await _do_spawn(client, message, chat_id)


# ── Core spawn logic ──────────────────────────────────────────────────────────

async def _do_spawn(client, message: Message, chat_id: int):
    tier = roll_rarity()
    if not await check_and_record_drop(chat_id, tier.name):
        tier = get_rarity("common")

    if tier.spawn_requires_activity:
        g = await get_group(chat_id)
        if g.get("message_count", 0) < SPAWN_SETTINGS.get("activity_threshold", 0):
            tier = get_rarity("common")

    sub  = roll_sub_rarity(tier.name)
    eff  = sub or tier
    char = await get_random_character(eff.name) or await get_random_character("common")
    if not char:
        return

    if char["rarity"] != eff.name:
        eff = get_rarity(char["rarity"]) or eff

    reveal      = SPAWN_SETTINGS.get("reveal_rarity_on_spawn", True)
    rarity_hint = rarity_display(eff.name) if reveal else "❓ **???**"
    claim_win   = get_claim_window(eff.name)
    banner      = f"🚨 **RARE SPAWN ALERT!** {eff.emoji}\n\n" if eff.announce_spawn else ""
    hidden_name = _obscure_name(char["name"])

    # Build hint showing how many name parts there are.
    name_parts  = char["name"].split()
    part_hint   = " · ".join(
        f"`{p[0]}{'░' * (len(p)-1)}`" for p in name_parts
    )

    text = (
        f"{banner}✨ **A mystery soul has appeared!**\n\n"
        f"👤 **{hidden_name}**\n"
        f"📖 _{char.get('anime', 'Unknown')}_\n"
        f"⭐ {rarity_hint}\n\n"
        f"🔤 **Type the character's name to claim!**\n"
        f"💡 _First, middle, or last name all work!_ (`{claim_win}s`)"
    )

    try:
        if char.get("video_url"):
            msg = await message.reply_video(char["video_url"], caption=text)
        elif char.get("img_url"):
            msg = await message.reply_photo(char["img_url"], caption=text)
        else:
            msg = await message.reply_text(text)
    except Exception as e:
        log.error(f"Spawn send failed: {e}")
        return

    spawn_id = await create_spawn(chat_id, msg.id, char, eff.name)

    # Build all accepted answer tokens for this character.
    answer_tokens = _name_tokens(char["name"])

    # Register the active guessing session in memory.
    _active_spawns[chat_id] = {
        "spawn_id":      spawn_id,
        "char":          char,
        "eff":           eff,
        "msg":           msg,
        "claim_win":     claim_win,
        "answer_tokens": answer_tokens,   # list of accepted normalised strings
        "locked":        False,           # True while a correct guess is being processed
    }

    asyncio.create_task(_expire(client, chat_id, msg, spawn_id, claim_win))
    asyncio.create_task(_ping_wishlist(client, char["id"], chat_id))


# ── Guess handler (called from on_group_message when a session is active) ─────

async def _check_guess(client, message: Message, chat_id: int, session: dict):
    """
    Compares the incoming message text against all accepted name tokens
    for the active spawn.  A user can type:
        • The full name   → 'Monkey D Luffy'
        • The first name  → 'Monkey'
        • A middle token  → (any word that is ≥2 chars)
        • The last name   → 'Luffy'
    All matches are accent-insensitive and case-insensitive.
    """
    if session["locked"]:
        return

    guess = _normalize(message.text or "")
    if not guess:
        return

    if not _match_guess(guess, session):
        return                          # wrong — let the chat continue

    # ── Correct guess ────────────────────────────────────────────────────────
    session["locked"] = True

    user = message.from_user
    if await is_user_banned(user.id):
        session["locked"] = False
        return await message.reply_text("🚫 You are banned and cannot claim characters.")

    await get_or_create_user(
        user.id,
        user.username   or "",
        user.first_name or "",
        getattr(user, "last_name", "") or "",
    )

    char        = session["char"]
    eff         = session["eff"]
    rarity_name = eff.name
    spawn_id    = session["spawn_id"]

    # Rarity cap check
    if eff.max_per_user > 0:
        if await count_rarity_in_harem(user.id, rarity_name) >= eff.max_per_user:
            session["locked"] = False
            return await message.reply_text(
                f"⚠️ You already have the max **{eff.max_per_user}** "
                f"{eff.display_name} characters allowed!"
            )

    # Work out what the user actually typed vs. the full name
    typed       = (message.text or "").strip()
    full_name   = char["name"]
    guessed_full = _normalize(typed) == _normalize(full_name)
    name_label  = (
        f"**{full_name}**"
        if guessed_full
        else f"**{full_name}** _(guessed as '{typed}')_"
    )

    # Add to harem and reward kakera
    iid    = await add_to_harem(user.id, char)
    kakera = get_kakera_reward(rarity_name)
    await add_balance(user.id, kakera)
    await expire_spawn(spawn_id)

    # Remove the session before further awaits to avoid races
    _active_spawns.pop(chat_id, None)

    result_text = (
        f"🎉 **{user.first_name}** guessed correctly and claimed {name_label}!\n\n"
        f"{eff.emoji} **{eff.display_name}**\n"
        f"📖 _{char.get('anime', 'Unknown')}_\n"
        f"🆔 `{iid}`\n"
        f"💰 +**{kakera:,} kakera**!"
    )
    try:
        msg = session["msg"]
        if char.get("video_url") or char.get("img_url"):
            await msg.edit_caption(result_text)
        else:
            await msg.edit_text(result_text)
    except Exception:
        await message.reply_text(result_text)


# ── Spawn expiry ──────────────────────────────────────────────────────────────

async def _expire(client, chat_id: int, msg, spawn_id: str, delay: int):
    await asyncio.sleep(delay)

    session = _active_spawns.pop(chat_id, None)
    if not session or session["spawn_id"] != spawn_id:
        return                          # already claimed — nothing to do

    await expire_spawn(spawn_id)

    char        = session["char"]
    answer_text = (
        f"⏰ **Time's up!** Nobody guessed the character.\n"
        f"The answer was: **{char['name']}** "
        f"_({char.get('anime', 'Unknown')})_ 👻"
    )
    try:
        if char.get("video_url") or char.get("img_url"):
            await msg.edit_caption(answer_text)
        else:
            await msg.edit_text(answer_text)
    except Exception:
        try:
            await client.send_message(chat_id, answer_text)
        except Exception:
            pass


# ── Wishlist ping ─────────────────────────────────────────────────────────────

async def _ping_wishlist(client, char_id, chat_id: int):
    for uid in await get_wishers(char_id):
        try:
            char = await get_character(char_id)
            name = char["name"] if char else "A character"
            await client.send_message(
                uid,
                f"💛 **{name}** (on your wishlist) just spawned!\n"
                f"Type their first, middle, or last name to claim — fast! 🏃"
            )
        except Exception:
            pass
