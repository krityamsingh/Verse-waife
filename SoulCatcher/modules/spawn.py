"""SoulCatcher/modules/spawn.py — message counter, /drop, atomic ❤️ claim.

FIXES:
  [BUG-1] message.effective_chat — does not exist in Pyrogram. Replaced with
          message.chat.id everywhere (lines 24 and 40 in original).

  [BUG-2] client.loop.create_task() — client.loop is captured at import time
          (before asyncio.run()), so it points to a dead loop. Tasks scheduled
          on it silently vanish: expire timer never fires, wishlist pings never
          send. Fixed by using asyncio.create_task() which always uses the
          currently running loop.

  [BUG-3] ~filters.command([]) — an empty list never matches any command, so
          the negation is always True. This caused on_group_message to fire on
          /drop, /spawn, etc. as well, triggering a double-spawn. Fixed by
          listing the actual commands to exclude.
"""

import asyncio
import logging
from datetime import datetime

from pyrogram import filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from .. import app
from ..rarity import (
    roll_rarity, roll_sub_rarity, rarity_display, get_rarity,
    get_kakera_reward, SPAWN_SETTINGS, get_claim_window,
)
from ..database import (
    get_group, increment_group_msg, reset_group_msg,
    check_and_record_drop, get_random_character,
    create_spawn, claim_spawn, expire_spawn,
    add_to_harem, get_or_create_user, add_balance,
    count_rarity_in_harem, get_wishers, is_user_banned, get_character,
)

log = logging.getLogger("SoulCatcher.spawn")

# Commands that must NOT trigger the message counter / auto-spawn.
_BOT_COMMANDS = [
    "start","drop","spawn","help","harem","view","setfav","burn","sort",
    "daily","bal","spin","pay","shop","sell","buy","market","trade","gift",
    "marry","propose","epropose","basket","wish","wishlist",
    "profile","status","rank","top","toprarity","richest","rarityinfo","event",
    "gban","ungban","gmute","ungmute","broadcast","transfer","eval","ev",
    "shell","sh","bash","gitpull","update","addchar","delchar","setmode",
    "forcedrop","ban","unban","addsudo","rmsudo","sudolist","adddev","rmdev",
    "devlist","adduploader","rmuploader","uploaderlist","upload","il","uchar",
]


# ── Auto-spawn on group messages ──────────────────────────────────────────────

@app.on_message(filters.group & filters.text & ~filters.command(_BOT_COMMANDS))
async def on_group_message(client, message: Message):
    # FIX [BUG-1]: message.chat.id — effective_chat does not exist in Pyrogram.
    chat_id = message.chat.id
    count   = await increment_group_msg(chat_id)
    if count < SPAWN_SETTINGS["messages_per_spawn"]:
        return

    group = await get_group(chat_id)
    if not group.get("spawn_enabled", True) or group.get("banned"):
        await reset_group_msg(chat_id)
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
    # FIX [BUG-1]: message.chat.id
    chat_id  = message.chat.id
    group    = await get_group(chat_id)
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
        from ..rarity import get_rarity as _gr
        tier = _gr("common")

    if tier.spawn_requires_activity:
        g = await get_group(chat_id)
        if g.get("message_count", 0) < SPAWN_SETTINGS["activity_threshold"]:
            from ..rarity import get_rarity as _gr2
            tier = _gr2("common")

    sub  = roll_sub_rarity(tier.name)
    eff  = sub if sub else tier
    char = await get_random_character(eff.name) or await get_random_character("common")
    if not char:
        return

    from ..rarity import get_rarity as _gr3
    if char["rarity"] != eff.name:
        eff = _gr3(char["rarity"]) or eff

    reveal      = SPAWN_SETTINGS["reveal_rarity_on_spawn"]
    rarity_hint = rarity_display(eff.name) if reveal else "❓ **???**"
    claim_win   = get_claim_window(eff.name)
    banner      = f"🚨 **RARE SPAWN ALERT!** {eff.emoji}\n\n" if eff.announce_spawn else ""

    text = (
        f"{banner}✨ **A soul has appeared!**\n\n"
        f"👤 **{char['name']}**\n"
        f"📖 _{char.get('anime', 'Unknown')}_\n"
        f"⭐ {rarity_hint}\n\n"
        f"Press ❤️ to claim! (`{claim_win}s`)"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❤️ Claim", callback_data="claim:PENDING")]])

    try:
        if char.get("video_url"):
            msg = await message.reply_video(char["video_url"], caption=text, reply_markup=kb)
        elif char.get("img_url"):
            msg = await message.reply_photo(char["img_url"], caption=text, reply_markup=kb)
        else:
            msg = await message.reply_text(text, reply_markup=kb)
    except Exception as e:
        log.error(f"Spawn send failed: {e}")
        return

    spawn_id = await create_spawn(chat_id, msg.id, char, eff.name)
    real_kb  = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"❤️ Claim ({claim_win}s)", callback_data=f"claim:{spawn_id}")
    ]])
    try:
        if char.get("video_url") or char.get("img_url"):
            await msg.edit_caption(text, reply_markup=real_kb)
        else:
            await msg.edit_text(text, reply_markup=real_kb)
    except Exception:
        pass

    # FIX [BUG-2]: asyncio.create_task() uses the running loop, not the stale
    # client.loop captured at import time. Without this fix the expire timer
    # and wishlist pings silently vanish — claims expire on the UI but the DB
    # spawn entry is never marked expired, and wishers never get notified.
    asyncio.create_task(_expire(client, chat_id, msg, spawn_id, claim_win))
    asyncio.create_task(_ping_wishlist(client, char["id"], chat_id))


# ── Expire timer ──────────────────────────────────────────────────────────────

async def _expire(client, chat_id, msg, spawn_id, delay):
    await asyncio.sleep(delay)
    await expire_spawn(spawn_id)
    try:
        await msg.edit_reply_markup(None)
        await client.send_message(chat_id, "💨 The soul fled! Nobody was fast enough...")
    except Exception:
        pass


# ── Wishlist ping ─────────────────────────────────────────────────────────────

async def _ping_wishlist(client, char_id, chat_id):
    for uid in await get_wishers(char_id):
        try:
            char = await get_character(char_id)
            name = char["name"] if char else "A character"
            await client.send_message(uid, f"💛 **{name}** (on your wishlist) just spawned! Quick!")
        except Exception:
            pass


# ── Claim callback ────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^claim:"))
async def claim_cb(client, cb):
    user     = cb.from_user
    spawn_id = cb.data.split(":")[1]

    if spawn_id == "PENDING":
        return await cb.answer("⏳ Registering spawn, try again!", show_alert=True)

    if await is_user_banned(user.id):
        return await cb.answer("🚫 You are banned.", show_alert=True)

    await get_or_create_user(user.id, user.username or "", user.first_name or "")
    spawn_doc = await claim_spawn(spawn_id, user.id)
    if not spawn_doc:
        return await cb.answer("💨 Already claimed!", show_alert=True)

    rarity_name = spawn_doc["rarity"]
    tier        = get_rarity(rarity_name)

    if tier and tier.max_per_user > 0:
        if await count_rarity_in_harem(user.id, rarity_name) >= tier.max_per_user:
            from ..database import _col
            await _col("active_spawns").update_one(
                {"spawn_id": spawn_id},
                {"$set": {"claimed": False, "claimed_by": None}},
            )
            return await cb.answer(
                f"⚠️ Max {tier.max_per_user} {tier.display_name} per user!",
                show_alert=True,
            )

    char = await get_character(spawn_doc["char_id"]) or {
        "id": spawn_doc["char_id"], "name": spawn_doc["char_name"],
        "rarity": rarity_name, "anime": "Unknown", "img_url": "",
    }
    iid    = await add_to_harem(user.id, char)
    kakera = get_kakera_reward(rarity_name)
    await add_balance(user.id, kakera)

    text = (
        f"🎉 **{user.first_name}** claimed **{char['name']}**!\n\n"
        f"{tier.emoji if tier else '?'} **{tier.display_name if tier else rarity_name}**\n"
        f"📖 _{char.get('anime', 'Unknown')}_\n"
        f"🆔 `{iid}`\n"
        f"💰 +**{kakera:,} kakera**!"
    )
    try:
        if char.get("video_url") or char.get("img_url"):
            await cb.message.edit_caption(text, reply_markup=None)
        else:
            await cb.message.edit_text(text, reply_markup=None)
    except Exception:
        pass

    await cb.answer(f"✅ Claimed {char['name']}!", show_alert=False)
