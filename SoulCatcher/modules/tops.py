"""SoulCatcher/modules/tops.py

  /ktop   — top 10 kakera holders
  /ctop   — top 10 character collectors (by total copies + unique count)
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


MEDALS = ["🥇", "🥈", "🥉", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"]


async def _name_from_db(user_id: int, fallback: str | None = None) -> str:
    """Read display name from users collection. No Telegram API call."""
    try:
        doc = await _col("users").find_one(
            {"user_id": user_id},
            {"first_name": 1, "username": 1},
        )
        if doc:
            return doc.get("first_name") or doc.get("username") or f"User {user_id}"
    except Exception as e:
        log.warning("_name_from_db uid=%s: %s", user_id, e)
    return fallback or f"User {user_id}"


# ─────────────────────────────────────────────────────────────────────────────
# /ktop — top 10 kakera holders
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("ktop"))
async def cmd_ktop(client, message: Message):
    wait = await message.reply_text("⏳ Loading kakera leaderboard...")
    try:
        rows = await (
            _col("users")
            .find(
                {"balance": {"$gt": 0}},
                {"user_id": 1, "balance": 1, "first_name": 1, "username": 1},
            )
            .sort("balance", -1)
            .limit(10)
            .to_list(10)
        )
    except Exception as e:
        log.error("ktop DB error: %s", e)
        return await wait.edit_text("❌ Failed to load leaderboard. Please try again.")

    if not rows:
        return await wait.edit_text("📊 No kakera holders yet.")

    lines = ["〔 🌸  ᴋᴀᴋᴇʀᴀ  ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ  〕\n"]
    for i, row in enumerate(rows):
        medal = MEDALS[i] if i < len(MEDALS) else f"{i+1}."
        uid   = row.get("user_id")
        # Name is already in the projection — no extra API call needed
        name  = row.get("first_name") or row.get("username") or f"User {uid}"
        bal   = row.get("balance", 0)
        lines.append(f"{medal}  **{name}**\n     🌸 `{_fmt(bal)}` kakera")

    await wait.edit_text(
        "\n\n".join(lines[0:1]) + "\n" + "\n\n".join(lines[1:])
    )


# ─────────────────────────────────────────────────────────────────────────────
# /ctop — top 10 character collectors
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("ctop"))
async def cmd_ctop(client, message: Message):
    wait = await message.reply_text("⏳ Loading collector leaderboard...")
    try:
        # Step 1: rank users by total character count
        total_rows = await _col("user_characters").aggregate([
            {"$group": {"_id": "$user_id", "total": {"$sum": 1}}},
            {"$sort":  {"total": -1}},
            {"$limit": 10},
        ]).to_list(10)
    except Exception as e:
        log.error("ctop aggregate error: %s", e)
        return await wait.edit_text("❌ Failed to load leaderboard. Please try again.")

    if not total_rows:
        return await wait.edit_text("📊 No collectors yet.")

    uid_list = [r["_id"] for r in total_rows]

    # Step 2: batch-fetch names from users collection (one query)
    try:
        user_docs = await _col("users").find(
            {"user_id": {"$in": uid_list}},
            {"user_id": 1, "first_name": 1, "username": 1},
        ).to_list(10)
        name_map = {
            d["user_id"]: (d.get("first_name") or d.get("username") or None)
            for d in user_docs
        }
    except Exception:
        name_map = {}

    # Step 3: batch-fetch unique character counts (one aggregation)
    # Group by (user_id, char_id) to deduplicate, then count distinct chars per user
    try:
        uniq_rows = await _col("user_characters").aggregate([
            {"$match": {"user_id": {"$in": uid_list}}},
            {"$group": {"_id": {"uid": "$user_id", "cid": "$char_id"}}},
            {"$group": {"_id": "$_id.uid", "unique": {"$sum": 1}}},
        ]).to_list(10)
        uniq_map = {r["_id"]: r["unique"] for r in uniq_rows}
    except Exception as e:
        log.warning("ctop unique count error: %s", e)
        uniq_map = {}

    lines = ["〔 🃏  ᴄʜᴀʀᴀᴄᴛᴇʀ  ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ  〕\n"]
    for i, row in enumerate(total_rows):
        medal  = MEDALS[i] if i < len(MEDALS) else f"{i+1}."
        uid    = row["_id"]
        name   = name_map.get(uid) or f"User {uid}"
        total  = row["total"]
        unique = uniq_map.get(uid, 0)
        lines.append(
            f"{medal}  **{name}**\n"
            f"     📦 `{_fmt(total)}` copies  ·  ✨ `{_fmt(unique)}` unique"
        )

    await wait.edit_text(
        "\n\n".join(lines[0:1]) + "\n" + "\n\n".join(lines[1:])
    )
