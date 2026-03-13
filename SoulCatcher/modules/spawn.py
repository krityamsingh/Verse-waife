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
    # NOTE: claim_spawn is no longer used — guessing replaces the button flow.
    # We keep expire_spawn / unclaim_spawn for cleanup paths.
)

log = logging.getLogger("SoulCatcher.spawn")

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


# ── /setspawn — owner / admin command ─────────────────────────────────────────

@app.on_message(filters.command("setspawn") & filters.group)
async def cmd_setspawn(client, message: Message):
    """
    Usage:  /setspawn <number>
    Sets how many messages must be sent before a character spawns.
    Restricted to group owner and admins.
    """
    user_id = message.from_user.id
    chat_id = message.chat.id

    # Permission check — only group admins / owner may change the limit.
    member = await client.get_chat_member(chat_id, user_id)
    if member.status not in ("creator", "administrator"):
        return await message.reply_text("❌ Only group admins can change the spawn limit.")

    args = message.command[1:]          # everything after /setspawn
    if not args or not args[0].isdigit():
        return await message.reply_text(
            "⚙️ **Usage:** `/setspawn <messages>`\n"
            "Example: `/setspawn 10` — spawn after every 10 messages.\n"
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
        # Don't count guessing messages toward the spawn counter; return early.
        return

    # ── Guard: disabled / banned groups ─────────────────────────────────────
    group = await get_group(chat_id)
    if not group.get("spawn_enabled", True) or group.get("banned"):
        return

    # ── Increment and check threshold ───────────────────────────────────────
    # Use the group's custom limit if set, otherwise fall back to the global default.
    threshold = group.get("spawn_msg_limit", SPAWN_SETTINGS["messages_per_spawn"])
    count = await increment_group_msg(chat_id)
    if count < threshold:
        return

    last     = group.get("last_spawn")
    cooldown = group.get("spawn_cooldown", SPAWN_SETTINGS["cooldown_seconds"])
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
    cooldown = group.get("spawn_cooldown", SPAWN_SETTINGS["cooldown_seconds"])
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
        if g.get("message_count", 0) < SPAWN_SETTINGS["activity_threshold"]:
            tier = get_rarity("common")

    sub  = roll_sub_rarity(tier.name)
    eff  = sub or tier
    char = await get_random_character(eff.name) or await get_random_character("common")
    if not char:
        return

    if char["rarity"] != eff.name:
        eff = get_rarity(char["rarity"]) or eff

    reveal      = SPAWN_SETTINGS["reveal_rarity_on_spawn"]
    rarity_hint = rarity_display(eff.name) if reveal else "❓ **???**"
    claim_win   = get_claim_window(eff.name)
    banner      = f"🚨 **RARE SPAWN ALERT!** {eff.emoji}\n\n" if eff.announce_spawn else ""
    hidden_name = _obscure_name(char["name"])

    text = (
        f"{banner}✨ **A mystery soul has appeared!**\n\n"
        f"👤 **{hidden_name}**\n"
        f"📖 _{char.get('anime', 'Unknown')}_\n"
        f"⭐ {rarity_hint}\n\n"
        f"🔤 **Type the character's name to claim!** (`{claim_win}s`)"
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

    # Register the active guessing session in memory.
    _active_spawns[chat_id] = {
        "spawn_id":     spawn_id,
        "char":         char,
        "eff":          eff,
        "msg":          msg,
        "claim_win":    claim_win,
        "answer_norm":  _normalize(char["name"]),
        "locked":       False,   # True while a correct guess is being processed
    }

    asyncio.create_task(_expire(client, chat_id, msg, spawn_id, claim_win))
    asyncio.create_task(_ping_wishlist(client, char["id"], chat_id))


# ── Guess handler (called from on_group_message when a session is active) ────

async def _check_guess(client, message: Message, chat_id: int, session: dict):
    """Compares the incoming message text against the active spawn's answer."""
    if session["locked"]:
        return                          # already being processed

    guess = _normalize(message.text or "")
    if not guess:
        return

    if guess != session["answer_norm"]:
        return                          # wrong — let the chat continue

    # ── Correct guess ────────────────────────────────────────────────────────
    session["locked"] = True           # prevent race conditions

    user = message.from_user
    if await is_user_banned(user.id):
        session["locked"] = False
        return await message.reply_text("🚫 You are banned and cannot claim characters.")

    await get_or_create_user(
        user.id,
        user.username  or "",
        user.first_name or "",
        getattr(user, "last_name", "") or "",
    )

    char         = session["char"]
    eff          = session["eff"]
    rarity_name  = eff.name
    spawn_id     = session["spawn_id"]

    # Rarity cap check
    if eff.max_per_user > 0:
        if await count_rarity_in_harem(user.id, rarity_name) >= eff.max_per_user:
            session["locked"] = False
            return await message.reply_text(
                f"⚠️ You already have the max **{eff.max_per_user}** "
                f"{eff.display_name} characters allowed!"
            )

    # Add to harem and reward kakera
    iid    = await add_to_harem(user.id, char)
    kakera = get_kakera_reward(rarity_name)
    await add_balance(user.id, kakera)
    await expire_spawn(spawn_id)       # mark DB record as claimed / expired

    # Remove the session before any awaits that could race
    _active_spawns.pop(chat_id, None)

    # Edit the original spawn message to show "claimed" state
    result_text = (
        f"🎉 **{user.first_name}** guessed correctly and claimed **{char['name']}**!\n\n"
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
        # Fall back to a plain reply if the edit fails
        await message.reply_text(result_text)


# ── Spawn expiry ──────────────────────────────────────────────────────────────

async def _expire(client, chat_id: int, msg, spawn_id: str, delay: int):
    await asyncio.sleep(delay)

    # Only act if the session is still present (not already claimed).
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
                f"💛 **{name}** (on your wishlist) just spawned in a group! Be the first to guess!"
            )
        except Exception:
            pass
