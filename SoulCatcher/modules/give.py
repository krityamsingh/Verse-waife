"""SoulCatcher/modules/give.py
Owner-only admin give commands.

  /give <user_id> <char_id>    — add one character to a user's harem
  /giveall <user_id>           — add every uploaded character to a user's harem
  /kakera <user_id> <amount>   — credit kakera balance to a user

All three require reply OR explicit user_id as first argument.
Only OWNER_IDS can use these commands.
"""

from __future__ import annotations
import logging

from pyrogram import filters
from pyrogram.types import Message

from .. import app, owner_filter
from ..database import (
    get_or_create_user, add_to_harem, add_balance,
    get_character, _col,
)

log = logging.getLogger("SoulCatcher.give")


def _fmt(n) -> str:
    try:
        return f"{int(n):,}"
    except Exception:
        return str(n)


def _pad(raw: str) -> str:
    try:
        return str(int(raw)).zfill(4)
    except (ValueError, TypeError):
        return raw.strip()


async def _resolve_target(client, message: Message, args: list[str], need_extra: int = 1):
    """
    Resolve target user_id from reply or first arg.
    Returns (user_id, remaining_args) or (None, []) on failure.
    need_extra = how many args are required AFTER the user identifier.
    """
    if message.reply_to_message:
        target_id = message.reply_to_message.from_user.id
        extra = args[1:]          # everything after the command
    elif len(args) > need_extra:
        raw = args[1]
        if raw.startswith("@"):
            try:
                u = await client.get_users(raw)
                target_id = u.id
            except Exception:
                return None, []
        else:
            try:
                target_id = int(raw)
            except ValueError:
                return None, []
        extra = args[2:]          # remaining after user identifier
    else:
        return None, []
    return target_id, extra


# ─────────────────────────────────────────────────────────────────────────────
# /give <user_id|@username|reply>  <char_id>
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("give") & owner_filter)
async def cmd_give(client, message: Message):
    args = message.command

    # Need at least: command + char_id  (user comes from reply or arg)
    if len(args) < 2:
        return await message.reply_text(
            "**Usage:**\n"
            "• Reply to user: `/give <char_id>`\n"
            "• By ID: `/give <user_id> <char_id>`\n"
            "• By username: `/give @username <char_id>`"
        )

    target_id, extra = await _resolve_target(client, message, args, need_extra=1)

    if target_id is None:
        return await message.reply_text(
            "❌ Could not resolve user.\n"
            "Reply to the user or provide their ID/username."
        )

    if not extra:
        return await message.reply_text("❌ Please provide a character ID.")

    char_id = _pad(extra[0])
    char    = await get_character(char_id)
    if not char:
        return await message.reply_text(f"❌ Character `{char_id}` not found in the database.")

    # Ensure user exists
    await get_or_create_user(target_id)

    iid = await add_to_harem(target_id, char)

    from ..rarity import get_rarity
    tier       = get_rarity(char.get("rarity", ""))
    rarity_str = f"{tier.emoji} {tier.display_name}" if tier else char.get("rarity", "?")

    log.info("GIVE: owner=%d → user=%d  char=%s (%s)  iid=%s",
             message.from_user.id, target_id, char_id, char["name"], iid)

    await message.reply_text(
        f"✅ **Given!**\n\n"
        f"👤 **{char['name']}** `{char_id}`\n"
        f"📖 _{char.get('anime', '?')}_\n"
        f"{rarity_str}\n\n"
        f"→ User `{target_id}`  ·  Instance `{iid}`"
    )

    # Notify recipient in DM (best-effort)
    try:
        vid = char.get("video_url", "")
        img = char.get("img_url", "")
        dm  = (
            f"🎁 **You received a character from the owner!**\n\n"
            f"👤 **{char['name']}**\n"
            f"📖 _{char.get('anime', '?')}_\n"
            f"{rarity_str}\n"
            f"🆔 Instance: `{iid}`"
        )
        if vid:
            await client.send_video(target_id, vid, caption=dm)
        elif img:
            await client.send_photo(target_id, img, caption=dm)
        else:
            await client.send_message(target_id, dm)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# /giveall <user_id|@username|reply>
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("giveall") & owner_filter)
async def cmd_giveall(client, message: Message):
    args = message.command

    target_id, _ = await _resolve_target(client, message, args, need_extra=0)

    if target_id is None:
        return await message.reply_text(
            "**Usage:**\n"
            "• Reply to user: `/giveall`\n"
            "• By ID: `/giveall <user_id>`\n"
            "• By username: `/giveall @username`"
        )

    # Fetch all enabled characters
    all_chars = await _col("characters").find({"enabled": True}).to_list(None)
    if not all_chars:
        return await message.reply_text("❌ No characters in the database yet.")

    await get_or_create_user(target_id)

    progress = await message.reply_text(
        f"⏳ Adding **{len(all_chars):,}** characters to user `{target_id}`...\nThis may take a moment."
    )

    added = 0
    failed = 0
    for char in all_chars:
        try:
            await add_to_harem(target_id, char)
            added += 1
        except Exception as exc:
            log.warning("giveall: failed char %s: %s", char.get("id"), exc)
            failed += 1

    log.info("GIVEALL: owner=%d → user=%d  added=%d  failed=%d",
             message.from_user.id, target_id, added, failed)

    result = (
        f"✅ **Done!**\n\n"
        f"👤 User: `{target_id}`\n"
        f"📦 Added: **{added:,}** characters\n"
    )
    if failed:
        result += f"⚠️ Failed: **{failed}**\n"

    try:
        await progress.edit_text(result)
    except Exception:
        await message.reply_text(result)

    # Notify recipient
    try:
        await client.send_message(
            target_id,
            f"🎁 **The owner just added all {added:,} characters to your harem!**\n"
            f"Use /harem to see your collection."
        )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# /kakera <user_id|@username|reply>  <amount>
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("kakera") & owner_filter)
async def cmd_kakera(client, message: Message):
    args = message.command

    if len(args) < 2:
        return await message.reply_text(
            "**Usage:**\n"
            "• Reply to user: `/kakera <amount>`\n"
            "• By ID: `/kakera <user_id> <amount>`\n"
            "• By username: `/kakera @username <amount>`\n\n"
            "Use a negative amount to deduct."
        )

    target_id, extra = await _resolve_target(client, message, args, need_extra=1)

    if target_id is None:
        return await message.reply_text(
            "❌ Could not resolve user.\n"
            "Reply to the user or provide their ID/username."
        )

    if not extra:
        return await message.reply_text("❌ Please provide an amount.")

    try:
        amount = int(extra[0].replace(",", ""))
    except ValueError:
        return await message.reply_text("❌ Amount must be a number.")

    if amount == 0:
        return await message.reply_text("❌ Amount can't be zero.")

    await get_or_create_user(target_id)
    await add_balance(target_id, amount)

    action = "credited" if amount > 0 else "deducted"
    sign   = "+" if amount > 0 else ""

    log.info("KAKERA: owner=%d → user=%d  amount=%+d", message.from_user.id, target_id, amount)

    await message.reply_text(
        f"✅ **Kakera {action}!**\n\n"
        f"👤 User: `{target_id}`\n"
        f"🌸 Amount: **{sign}{_fmt(amount)}** kakera"
    )

    # Notify recipient
    try:
        await client.send_message(
            target_id,
            f"🌸 **{'You received' if amount > 0 else 'Kakera deducted:'}** "
            f"**{sign}{_fmt(amount)} kakera** from the owner!"
        )
    except Exception:
        pass
