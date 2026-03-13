"""SoulCatcher/modules/tops.py

  /ktop   — top 10 kakera holders  (reads from users collection)
  /ctop   — top 10 character collectors (reads from user_characters + joins users)

Strategy: load ALL required data in a single aggregation pipeline per command
using $lookup so names and stats are always in sync. No separate queries.
"""

from __future__ import annotations
import logging

from pyrogram import filters, enums
from pyrogram.types import Message

from .. import app
from ..database import _col

log  = logging.getLogger("SoulCatcher.tops")
HTML = enums.ParseMode.HTML

_DIV   = "━━━━━━━━━━━━━━━━━━━━"
MEDALS = ["🥇", "🥈", "🥉", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"]


def _fmt(n) -> str:
    try:    return f"{int(n):,}"
    except: return str(n)


def _esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _link(name: str, uid: int) -> str:
    return f'<a href="tg://user?id={uid}"><b>{_esc(name)}</b></a>'


def _display_name(doc: dict, uid: int) -> str:
    """Pick best display name from a user doc or fallback."""
    return (
        doc.get("first_name")
        or doc.get("username")
        or f"User {uid}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# /ktop — top 10 kakera holders
# Reads ONLY from users collection — single sorted query, no joins needed.
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("ktop"))
async def cmd_ktop(client, message: Message):
    wait = await message.reply_text("⏳ <i>Loading kakera leaderboard…</i>", parse_mode=HTML)
    try:
        # Pull all required fields in one query from users collection
        rows = await (
            _col("users")
            .find(
                {"balance": {"$gt": 0}},
                {"user_id": 1, "balance": 1, "first_name": 1, "username": 1, "_id": 0},
            )
            .sort("balance", -1)
            .limit(10)
            .to_list(10)
        )

        if not rows:
            return await wait.edit_text("📊 <i>No kakera holders yet.</i>", parse_mode=HTML)

        text = f"🌸 <b>KAKERA LEADERBOARD</b> 🌸\n<code>{_DIV}</code>\n\n"

        for i, row in enumerate(rows):
            medal = MEDALS[i] if i < len(MEDALS) else f"{i + 1}."
            uid   = row.get("user_id", 0)
            name  = _display_name(row, uid)
            bal   = row.get("balance", 0)

            if i == 0:
                text += (
                    f"{medal} {_link(name, uid)}\n"
                    f"   🌸 <b><code>{_fmt(bal)}</code></b> kakera\n\n"
                )
            else:
                text += f"{medal} {_link(name, uid)} — <code>{_fmt(bal)}</code> 🌸\n"

        text += f"\n<code>{_DIV}</code>"
        await wait.edit_text(text, parse_mode=HTML, disable_web_page_preview=True)

    except Exception as e:
        log.error("ktop error: %s", e, exc_info=True)
        await wait.edit_text(f"❌ <b>Error:</b> <code>{_esc(str(e)[:200])}</code>", parse_mode=HTML)


# ─────────────────────────────────────────────────────────────────────────────
# /ctop — top 10 character collectors
#
# Single aggregation pipeline on user_characters that:
#   1. Groups by user_id → total copies count
#   2. Sorts descending, takes top 10
#   3. $lookup into users collection to fetch name fields in same pipeline
#   4. Also computes unique char count via a sub-pipeline $lookup
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("ctop"))
async def cmd_ctop(client, message: Message):
    wait = await message.reply_text("⏳ <i>Loading collector leaderboard…</i>", parse_mode=HTML)
    try:
        # One aggregation — joins user data and unique count together
        pipeline = [
            # Step 1: count total chars per user
            {"$group": {"_id": "$user_id", "total": {"$sum": 1}}},
            {"$sort":  {"total": -1}},
            {"$limit": 10},

            # Step 2: join user profile (name fields) from users collection
            {"$lookup": {
                "from":         "users",
                "localField":   "_id",        # user_id from user_characters group
                "foreignField": "user_id",    # user_id field in users collection
                "as":           "user_info",
                "pipeline": [
                    {"$project": {"_id": 0, "first_name": 1, "username": 1}}
                ],
            }},

            # Step 3: join unique char count from same user_characters collection
            {"$lookup": {
                "from": "user_characters",
                "let":  {"uid": "$_id"},
                "pipeline": [
                    {"$match":  {"$expr": {"$eq": ["$user_id", "$$uid"]}}},
                    {"$group":  {"_id": "$char_id"}},
                    {"$count":  "unique"},
                ],
                "as": "unique_info",
            }},

            # Step 4: flatten the joined arrays into single fields
            {"$project": {
                "user_id": "$_id",
                "total":   1,
                "first_name": {"$ifNull": [{"$arrayElemAt": ["$user_info.first_name", 0]}, ""]},
                "username":   {"$ifNull": [{"$arrayElemAt": ["$user_info.username",   0]}, ""]},
                "unique":     {"$ifNull": [{"$arrayElemAt": ["$unique_info.unique",   0]}, 0]},
            }},
        ]

        rows = await _col("user_characters").aggregate(pipeline).to_list(10)

        if not rows:
            return await wait.edit_text("📊 <i>No collectors yet.</i>", parse_mode=HTML)

        text = f"🃏 <b>CHARACTER LEADERBOARD</b> 🃏\n<code>{_DIV}</code>\n\n"

        for i, row in enumerate(rows):
            medal  = MEDALS[i] if i < len(MEDALS) else f"{i + 1}."
            uid    = row.get("user_id", row.get("_id", 0))
            name   = _display_name(row, uid)
            total  = row.get("total", 0)
            unique = row.get("unique", 0)

            if i == 0:
                text += (
                    f"{medal} {_link(name, uid)}\n"
                    f"   📦 <b><code>{_fmt(total)}</code></b> copies  ·  "
                    f"✨ <b><code>{_fmt(unique)}</code></b> unique\n\n"
                )
            else:
                text += (
                    f"{medal} {_link(name, uid)} — "
                    f"<code>{_fmt(total)}</code> copies · "
                    f"<code>{_fmt(unique)}</code> unique\n"
                )

        text += f"\n<code>{_DIV}</code>"
        await wait.edit_text(text, parse_mode=HTML, disable_web_page_preview=True)

    except Exception as e:
        log.error("ctop error: %s", e, exc_info=True)
        await wait.edit_text(f"❌ <b>Error:</b> <code>{_esc(str(e)[:200])}</code>", parse_mode=HTML)
