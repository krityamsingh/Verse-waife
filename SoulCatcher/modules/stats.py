"""SoulCatcher/modules/stats.py  — owner-only bot stats

  /stats  — total registered users, groups, characters, kakera in circulation
"""

from __future__ import annotations
import logging

from pyrogram import filters
from pyrogram.types import Message

from .. import app, owner_filter
from ..database import _col

log = logging.getLogger("SoulCatcher.stats")


def _fmt(n) -> str:
    try:
        return f"{int(n):,}"
    except Exception:
        return str(n)


@app.on_message(filters.command("stats") & owner_filter)
async def cmd_stats(client, message: Message):
    wait = await message.reply_text("⏳ Fetching stats...")

    # Users
    total_users = await _col("users").count_documents({})

    # Groups (group_settings tracks every gc the bot has been added to)
    total_groups = await _col("group_settings").count_documents({})

    # Characters in DB
    total_chars    = await _col("characters").count_documents({"enabled": True})
    disabled_chars = await _col("characters").count_documents({"enabled": False})

    # Harem instances
    total_harem = await _col("user_characters").count_documents({})

    # Total kakera in circulation
    pipeline = [{"$group": {"_id": None, "total": {"$sum": "$balance"}}}]
    res = await _col("users").aggregate(pipeline).to_list(1)
    total_kakera = res[0]["total"] if res else 0

    # Spawns ever / claimed
    total_spawns  = await _col("active_spawns").count_documents({})
    claimed_spawns = await _col("active_spawns").count_documents({"claimed": True})

    text = (
        "〔 📊  ʙᴏᴛ  ꜱᴛᴀᴛꜱ  〕\n\n"
        f"👥  Registered Users:   **{_fmt(total_users)}**\n"
        f"💬  Groups Added In:    **{_fmt(total_groups)}**\n\n"
        f"🃏  Characters (active): **{_fmt(total_chars)}**\n"
        f"🚫  Characters (disabled): **{_fmt(disabled_chars)}**\n"
        f"📦  Total Harem Entries: **{_fmt(total_harem)}**\n\n"
        f"🌸  Kakera in Circulation: **{_fmt(total_kakera)}**\n\n"
        f"🎴  Total Spawns:  **{_fmt(total_spawns)}**\n"
        f"✅  Claimed:       **{_fmt(claimed_spawns)}**\n"
        f"❌  Unclaimed:     **{_fmt(total_spawns - claimed_spawns)}**"
    )

    try:
        await wait.edit_text(text)
    except Exception:
        await message.reply_text(text)
