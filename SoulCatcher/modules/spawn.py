"""SoulCatcher/modules/spawn.py — Character spawning & claiming system."""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta

import SoulCatcher as _soul
from pyrogram import filters
from pyrogram.types import Message

from SoulCatcher.database import (
    get_or_create_user,
    get_group_settings,
    track_group,
    get_random_character,
    create_spawn,
    get_spawn,
    delete_spawn,
    add_to_harem,
    add_xp,
    add_balance,
    get_wishlist_users,
    increment_drop_log,
    get_drop_count,
    is_user_banned,
    is_globally_banned,
    increment_char_stat,
)
from SoulCatcher.rarity import (
    roll_rarity,
    roll_sub_rarity,
    get_rarity,
    rarity_display,
    get_kakera_reward,
    get_xp_reward,
    get_claim_window,
    get_drop_limit,
    SPAWN_SETTINGS,
)
from SoulCatcher.config import LOG_CHANNEL_ID

log = logging.getLogger("SoulCatcher.spawn")

# ── Per-group message counters ────────────────────────────────────────────────
_msg_counters:  dict[int, int]      = {}
_last_spawn:    dict[int, datetime] = {}
_active_spawns: dict[int, dict]     = {}   # chat_id → spawn doc cache


def _should_spawn(chat_id: int, threshold: int) -> bool:
    count = _msg_counters.get(chat_id, 0) + 1
    _msg_counters[chat_id] = count
    if count < threshold:
        return False
    now = datetime.utcnow()
    last = _last_spawn.get(chat_id)
    cooldown = SPAWN_SETTINGS["cooldown_seconds"]
    if last and (now - last).total_seconds() < cooldown:
        return False
    _msg_counters[chat_id] = 0
    _last_spawn[chat_id] = now
    return True


async def _do_spawn(client, chat_id: int) -> None:
    # Pick rarity
    rarity_tier = roll_rarity()
    sub_tier    = roll_sub_rarity(rarity_tier.name)
    final_tier  = sub_tier or rarity_tier

    # Check daily drop limit
    limit = get_drop_limit(final_tier.name)
    if limit:
        count = await get_drop_count(chat_id, final_tier.name)
        if count >= limit:
            # Fall back to common
            from SoulCatcher.rarity import RARITIES
            final_tier = RARITIES["common"]

    # Get a character
    char = await get_random_character(final_tier.name)
    if not char:
        # Fallback to any common
        from SoulCatcher.rarity import RARITIES
        char = await get_random_character(RARITIES["common"].name)
    if not char:
        log.warning("No characters in DB for chat %d — skipping spawn.", chat_id)
        return

    # Increment drop log
    await increment_drop_log(chat_id, final_tier.name)

    # Build spawn doc
    expires = datetime.utcnow() + timedelta(seconds=get_claim_window(final_tier.name))
    spawn_doc = {
        "chat_id":    chat_id,
        "char_id":    char["id"],
        "rarity":     final_tier.name,
        "expires_at": expires,
        "spawned_at": datetime.utcnow(),
        "claimed":    False,
    }

    # Remove old spawn if any
    await delete_spawn(chat_id)
    await create_spawn(spawn_doc)
    _active_spawns[chat_id] = {**spawn_doc, "char": char, "tier": final_tier}

    # Build announcement
    rarity_str = rarity_display(final_tier.name)
    window     = get_claim_window(final_tier.name)

    if final_tier.announce_spawn:
        header = f"🚨 **RARE SPAWN!** 🚨\n\n"
    else:
        header = "🌸 **A character appeared!**\n\n"

    caption = (
        f"{header}"
        f"**{char['name']}**\n"
        f"📺 *{char.get('anime', 'Unknown')}*\n"
        f"✨ Rarity: **{rarity_str}**\n\n"
        f"⏳ Type the **character's name** to claim!\n"
        f"⏱ Window: `{window}s`"
    )

    # Send spawn message
    try:
        if char.get("video_url"):
            msg = await client.send_video(chat_id, char["video_url"], caption=caption)
        elif char.get("img_url"):
            msg = await client.send_photo(chat_id, char["img_url"], caption=caption)
        else:
            msg = await client.send_message(chat_id, caption)
        _active_spawns[chat_id]["msg_id"] = msg.id
    except Exception as exc:
        log.error("Failed to send spawn in %d: %s", chat_id, exc)

    # Ping wishlist users
    if final_tier.wishlist_ping:
        wishlist_uids = await get_wishlist_users(char["id"])
        if wishlist_uids:
            pings = " ".join(f"[👤](tg://user?id={uid})" for uid in wishlist_uids[:5])
            try:
                await client.send_message(
                    chat_id,
                    f"🔔 Wishlist ping! {pings}\n**{char['name']}** just spawned!",
                    disable_notification=False,
                )
            except Exception:
                pass

    # Schedule expiry cleanup
    asyncio.create_task(_expire_spawn(client, chat_id, char["id"], window))


async def _expire_spawn(client, chat_id: int, char_id: str, window: int) -> None:
    await asyncio.sleep(window + 2)
    spawn = _active_spawns.get(chat_id)
    if spawn and spawn.get("char_id") == char_id and not spawn.get("claimed"):
        await delete_spawn(chat_id)
        _active_spawns.pop(chat_id, None)
        try:
            char_name = spawn["char"]["name"]
            await client.send_message(
                chat_id,
                f"⌛ **{char_name}** fled! Nobody claimed them in time.",
            )
        except Exception:
            pass


# ── Message listener ──────────────────────────────────────────────────────────

@_soul.app.on_message(filters.group & filters.text & ~filters.command(""))
async def message_listener(client, m: Message):
    if not m.from_user:
        return

    chat_id = m.chat.id
    uid     = m.from_user.id
    text    = m.text.strip()

    # Track group
    asyncio.create_task(track_group(chat_id, m.chat.title or ""))

    # Check for active claim attempt
    spawn = _active_spawns.get(chat_id)
    if spawn and not spawn.get("claimed"):
        char      = spawn["char"]
        char_name = char["name"].lower().strip()
        guess     = text.lower().strip()

        # Allow partial match (first word at minimum)
        char_words  = char_name.split()
        guess_words = guess.split()

        matched = (
            guess == char_name
            or (len(char_words) > 1 and guess == char_words[0])
            or char_name in guess
            or guess in char_name
        )

        if matched:
            # Check bans
            if await is_globally_banned(uid) or await is_user_banned(uid):
                await m.reply("❌ You are banned from the game.")
                return

            # Claim!
            spawn["claimed"] = True
            await delete_spawn(chat_id)
            _active_spawns.pop(chat_id, None)

            u = m.from_user
            await get_or_create_user(uid, u.username or "", u.first_name or "", u.last_name or "")

            instance_id = await add_to_harem(uid, char)
            kakera      = get_kakera_reward(spawn["rarity"])
            xp_gain     = get_xp_reward(spawn["rarity"])

            await add_balance(uid, kakera)
            new_xp, new_level, levelled_up = await add_xp(uid, xp_gain)
            await increment_char_stat(char["id"], "claims")

            tier        = spawn["tier"]
            rarity_str  = rarity_display(spawn["rarity"])

            reply = (
                f"🎉 **{u.first_name}** claimed **{char['name']}**!\n"
                f"📺 *{char.get('anime', 'Unknown')}*\n"
                f"✨ {rarity_str}\n"
                f"🆔 Instance: `{instance_id}`\n"
                f"💰 +{kakera:,} kakera | ⭐ +{xp_gain:,} XP"
            )

            if levelled_up:
                reply += f"\n\n🆙 **Level Up!** You are now level **{new_level}**!"

            await m.reply(reply)

            # Log to log channel
            if LOG_CHANNEL_ID:
                try:
                    await client.send_message(
                        LOG_CHANNEL_ID,
                        f"🎴 **Claim Log**\n"
                        f"👤 {u.first_name} (`{uid}`)\n"
                        f"🎭 {char['name']} — {rarity_str}\n"
                        f"👥 Chat: `{chat_id}`",
                    )
                except Exception:
                    pass
            return

    # Auto-spawn logic
    gs = await get_group_settings(chat_id)
    if not gs.get("spawn_enabled", True):
        return
    threshold = gs.get("spawn_frequency", SPAWN_SETTINGS["messages_per_spawn"])
    if _should_spawn(chat_id, threshold):
        asyncio.create_task(_do_spawn(client, chat_id))


# ── /drop command ─────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command("drop") & filters.group & _soul.sudo_filter)
async def force_drop(client, m: Message):
    await m.reply("🌀 Forcing a spawn...")
    _last_spawn.pop(m.chat.id, None)   # reset cooldown for sudo
    asyncio.create_task(_do_spawn(client, m.chat.id))
