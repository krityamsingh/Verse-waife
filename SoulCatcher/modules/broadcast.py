"""SoulCatcher/modules/broadcast.py — /broadcast, /groupcast (owner only)."""
from __future__ import annotations

import asyncio
import logging

import SoulCatcher as _soul
from pyrogram import filters
from pyrogram.types import Message

from SoulCatcher.database import get_all_user_ids, get_all_group_ids

log = logging.getLogger("SoulCatcher.broadcast")

BROADCAST_DELAY = 0.05   # seconds between sends to avoid flood


@_soul.app.on_message(filters.command("broadcast") & _soul.owner_filter)
async def broadcast_cmd(client, m: Message):
    """Broadcast a message to all bot users."""
    if not m.reply_to_message:
        await m.reply("↩️ Reply to the message you want to broadcast.")
        return

    user_ids = await get_all_user_ids()
    total    = len(user_ids)
    progress = await m.reply(f"📡 Broadcasting to **{total:,}** users...")

    sent, failed = 0, 0
    for uid in user_ids:
        try:
            await m.reply_to_message.forward(uid)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(BROADCAST_DELAY)

    await progress.edit_text(
        f"✅ **Broadcast complete!**\n\n"
        f"📨 Sent: `{sent:,}`\n"
        f"❌ Failed: `{failed:,}`\n"
        f"📊 Total: `{total:,}`"
    )


@_soul.app.on_message(filters.command("groupcast") & _soul.owner_filter)
async def groupcast_cmd(client, m: Message):
    """Broadcast a message to all tracked groups."""
    if not m.reply_to_message:
        await m.reply("↩️ Reply to the message you want to broadcast to groups.")
        return

    group_ids = await get_all_group_ids()
    total     = len(group_ids)
    progress  = await m.reply(f"📡 Broadcasting to **{total:,}** groups...")

    sent, failed = 0, 0
    for gid in group_ids:
        try:
            await m.reply_to_message.forward(gid)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(BROADCAST_DELAY)

    await progress.edit_text(
        f"✅ **Group broadcast complete!**\n\n"
        f"📨 Sent: `{sent:,}`\n"
        f"❌ Failed: `{failed:,}`\n"
        f"📊 Total groups: `{total:,}`"
    )
