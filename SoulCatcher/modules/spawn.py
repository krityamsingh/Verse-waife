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
    create_spawn, claim_spawn, expire_spawn, unclaim_spawn,
    add_to_harem, get_or_create_user, add_balance,
    count_rarity_in_harem, get_wishers, is_user_banned, get_character,
)

log = logging.getLogger("SoulCatcher.spawn")

# All commands the bot handles — excluded from the message-counter so they
# never accidentally trigger a spawn or count toward the spawn threshold.
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
]


# ── Message counter → auto-spawn ──────────────────────────────────────────────

@app.on_message(filters.group & filters.text & ~filters.command(_ALL_COMMANDS))
async def on_group_message(client, message: Message):
    # FIX [BUG-1]: message.chat, NOT message.effective_chat (doesn't exist in 2.0.106)
    chat_id = message.chat.id

    # FIX [BUG-8]: check group state *before* incrementing so banned/disabled
    # groups never burn through the counter and trigger a reset each time.
    group = await get_group(chat_id)
    if not group.get("spawn_enabled", True) or group.get("banned"):
        return

    count = await increment_group_msg(chat_id)
    if count < SPAWN_SETTINGS["messages_per_spawn"]:
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
    # FIX [BUG-1]: message.chat, NOT message.effective_chat
    chat_id = message.chat.id

    # FIX [BUG-4]: honour spawn_enabled and banned flags, just like the
    # auto-spawn path does. Without this, /drop bypassed both checks entirely.
    group = await get_group(chat_id)
    if not group.get("spawn_enabled", True) or group.get("banned"):
        return await message.reply_text("❌ Spawning is disabled in this group.")

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

    sub = roll_sub_rarity(tier.name)
    # FIX [BUG-5]: `sub or tier` is explicit; both sub and tier are guaranteed
    # non-None here (roll_sub_rarity returns None when there is no sub-rarity,
    # and tier was already validated above).
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

    text = (
        f"{banner}✨ **A soul has appeared!**\n\n"
        f"👤 **{char['name']}**\n"
        f"📖 _{char.get('anime', 'Unknown')}_\n"
        f"⭐ {rarity_hint}\n\n"
        f"Press ❤️ to claim! (`{claim_win}s`)"
    )
    # Placeholder keyboard shown while we await create_spawn() for the real ID.
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❤️ Claim", callback_data="claim:PENDING")]])

    try:
        if char.get("video_url"):
            msg = await message.reply_video(char["video_url"], caption=text, reply_markup=kb)
        elif char.get("img_url"):
            msg = await message.reply_photo(char["img_url"],   caption=text, reply_markup=kb)
        else:
            msg = await message.reply_text(text, reply_markup=kb)
    except Exception as e:
        log.error(f"Spawn send failed: {e}")
        return

    spawn_id = await create_spawn(chat_id, msg.id, char, eff.name)
    real_kb  = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"❤️ Claim ({claim_win}s)", callback_data=f"claim:{spawn_id}")
    ]])

    # FIX [BUG-6]: if this edit fails, callback_data stays "claim:PENDING" and
    # the button becomes permanently broken. Expire the spawn immediately so it
    # doesn't linger as an unclaim-able ghost.
    try:
        if char.get("video_url") or char.get("img_url"):
            await msg.edit_caption(text, reply_markup=real_kb)
        else:
            await msg.edit_text(text, reply_markup=real_kb)
    except Exception as e:
        log.error(f"Failed to update spawn button with real ID ({spawn_id}): {e}. Expiring spawn.")
        await expire_spawn(spawn_id)
        return

    # FIX [BUG-2]: asyncio.create_task() instead of client.loop.create_task().
    # client.loop is captured at Client.__init__ time (before asyncio.run()),
    # so it points to a dead loop. asyncio.create_task() always uses the
    # currently running loop — the correct one.
    asyncio.create_task(_expire(client, chat_id, msg, spawn_id, claim_win))
    asyncio.create_task(_ping_wishlist(client, char["id"], chat_id))


# ── Spawn expiry ──────────────────────────────────────────────────────────────

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


# ── ❤️ Claim callback ─────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^claim:"))
async def claim_cb(client, cb):
    user = cb.from_user

    if await is_user_banned(user.id):
        return await cb.answer("🚫 You are banned.", show_alert=True)

    spawn_id = cb.data.split(":")[1]
    if spawn_id == "PENDING":
        return await cb.answer("⏳ Registering spawn, try again!", show_alert=True)

    await get_or_create_user(user.id, user.username or "", user.first_name or "", getattr(user, "last_name", "") or "")
    spawn_doc = await claim_spawn(spawn_id, user.id)
    if not spawn_doc:
        return await cb.answer("💨 Already claimed!", show_alert=True)

    rarity_name = spawn_doc["rarity"]
    tier        = get_rarity(rarity_name)

    if tier and tier.max_per_user > 0:
        if await count_rarity_in_harem(user.id, rarity_name) >= tier.max_per_user:
            # FIX [BUG-7]: use the dedicated public helper instead of importing
            # the private _col() directly, which leaked an internal detail and
            # bypassed any future claim-rollback logic in the database layer.
            await unclaim_spawn(spawn_id)
            return await cb.answer(
                f"⚠️ Max {tier.max_per_user} {tier.display_name} per user!", show_alert=True
            )

    char = await get_character(spawn_doc["char_id"]) or {
        "id":     spawn_doc["char_id"],
        "name":   spawn_doc.get("char_name", "Unknown"),   # FIX: .get() with fallback
        "rarity": rarity_name,
        "anime":  "Unknown",
        "img_url": "",
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

    await cb.answer(f"✅ Claimed {char['name']}!")
