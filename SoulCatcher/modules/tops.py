"""SoulCatcher/modules/tops.py

  /ktop   — top 10 kakera holders
  /ctop   — top 10 character collectors (by total copies)
"""

from __future__ import annotations
import logging

from pyrogram import filters
from pyrogram.types import Message

from .. import app
from ..database import _col

log = logging.getLogger("SoulCatcher.tops")


def _fmt(n) -> str:
    try:
        return f"{int(n):,}"
    except Exception:
        return str(n)


MEDALS = ["🥇", "🥈", "🥉",
          "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"]


async def _resolve_name(client, user_id: int) -> str:
    try:
        u = await client.get_users(user_id)
        return u.first_name or f"User {user_id}"
    except Exception:
        return f"User {user_id}"


# ─────────────────────────────────────────────────────────────────────────────
# /ktop — top 10 kakera holders
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("ktop"))
async def cmd_ktop(client, message: Message):
    rows = await (
        _col("users")
        .find({"balance": {"$gt": 0}}, {"user_id": 1, "balance": 1})
        .sort("balance", -1)
        .limit(10)
        .to_list(10)
    )

    if not rows:
        return await message.reply_text("📊 No kakera holders yet.")

    lines = [
        "〔 🌸  ᴋᴀᴋᴇʀᴀ  ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ  〕\n"
    ]
    for i, row in enumerate(rows):
        medal = MEDALS[i] if i < len(MEDALS) else f"{i+1}."
        name  = await _resolve_name(client, row["user_id"])
        bal   = row.get("balance", 0)
        lines.append(f"{medal}  **{name}**\n     🌸 `{_fmt(bal)}` kakera")

    await message.reply_text("\n\n".join(lines[0:1]) + "\n" + "\n\n".join(lines[1:]))


# ─────────────────────────────────────────────────────────────────────────────
# /ctop — top 10 character collectors
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("ctop"))
async def cmd_ctop(client, message: Message):
    pipeline = [
        {"$group": {"_id": "$user_id", "total": {"$sum": 1}}},
        {"$sort": {"total": -1}},
        {"$limit": 10},
    ]
    rows = await _col("user_characters").aggregate(pipeline).to_list(10)

    if not rows:
        return await message.reply_text("📊 No collectors yet.")

    lines = [
        "〔 🃏  ᴄʜᴀʀᴀᴄᴛᴇʀ  ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ  〕\n"
    ]
    for i, row in enumerate(rows):
        medal = MEDALS[i] if i < len(MEDALS) else f"{i+1}."
        name  = await _resolve_name(client, row["_id"])
        total = row["total"]

        # Unique char count for this user
        unique = await _col("user_characters").distinct("char_id", {"user_id": row["_id"]})
        lines.append(
            f"{medal}  **{name}**\n"
            f"     📦 `{_fmt(total)}` copies  ·  ✨ `{_fmt(len(unique))}` unique"
        )

    await message.reply_text("\n\n".join(lines[0:1]) + "\n" + "\n\n".join(lines[1:]))
