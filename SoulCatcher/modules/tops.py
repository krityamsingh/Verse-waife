"""SoulCatcher/modules/tops.py

  /ktop   — top 10 kakera holders
  /ctop   — top 10 character collectors (by total copies + unique count)

Uses HTML parse mode for rich inline formatting.
"""

from __future__ import annotations
import logging

from pyrogram import filters, enums
from pyrogram.types import Message

from .. import app
from ..database import _col

log  = logging.getLogger("SoulCatcher.tops")
HTML = enums.ParseMode.HTML


def _fmt(n) -> str:
    try:
        return f"{int(n):,}"
    except Exception:
        return str(n)


# Position decorations: medal emoji + colour tag for rank 1-3, plain for rest
_POS = [
    ('<b>🥇</b>', '#FFD700'),   # gold
    ('<b>🥈</b>', '#C0C0C0'),   # silver
    ('<b>🥉</b>', '#CD7F32'),   # bronze
    ('④',         None),
    ('⑤',         None),
    ('⑥',         None),
    ('⑦',         None),
    ('⑧',         None),
    ('⑨',         None),
    ('⑩',         None),
]

# Telegram HTML does NOT support <font color=...>, but it supports:
#   <b> <i> <u> <s> <code> <pre> <a href> <tg-spoiler>
# We use <code> for numeric values (monospace highlight) and <b> for names.
# For "colour" we rely on emoji anchors + strategic bold/code/italic combos.

_KDIVIDER = "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄"
_CDIVIDER = "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄"


# ─────────────────────────────────────────────────────────────────────────────
# /ktop — top 10 kakera holders
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("ktop"))
async def cmd_ktop(client, message: Message):
    wait = await message.reply_text("⏳ <i>Fetching kakera leaderboard…</i>", parse_mode=HTML)
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
        return await wait.edit_text("❌ <b>Failed to load leaderboard.</b> Please try again.", parse_mode=HTML)

    if not rows:
        return await wait.edit_text("📊 <i>No kakera holders yet.</i>", parse_mode=HTML)

    lines = [
        "🌸 <b>KAKERA  LEADERBOARD</b> 🌸\n"
        f"<code>{_KDIVIDER}</code>\n"
    ]

    for i, row in enumerate(rows):
        pos_emoji, _ = _POS[i] if i < len(_POS) else (f"{i+1}.", None)
        uid  = row.get("user_id")
        name = row.get("first_name") or row.get("username") or f"User {uid}"
        bal  = row.get("balance", 0)

        # Top 3 get a highlighted value line, rest get standard style
        if i == 0:
            val_line = f"    🌸 <b><code>{_fmt(bal)}</code></b> kakera"
        elif i == 1:
            val_line = f"    🌸 <code>{_fmt(bal)}</code> kakera"
        elif i == 2:
            val_line = f"    🌸 <code>{_fmt(bal)}</code> kakera"
        else:
            val_line = f"    ✦ <code>{_fmt(bal)}</code> kakera"

        lines.append(
            f"{pos_emoji} <a href=\"tg://user?id={uid}\"><b>{name}</b></a>\n"
            f"{val_line}"
        )

    lines.append(f"\n<code>{_KDIVIDER}</code>")
    await wait.edit_text("\n".join(lines), parse_mode=HTML, disable_web_page_preview=True)


# ─────────────────────────────────────────────────────────────────────────────
# /ctop — top 10 character collectors
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("ctop"))
async def cmd_ctop(client, message: Message):
    wait = await message.reply_text("⏳ <i>Fetching collector leaderboard…</i>", parse_mode=HTML)

    try:
        total_rows = await _col("user_characters").aggregate([
            {"$group": {"_id": "$user_id", "total": {"$sum": 1}}},
            {"$sort":  {"total": -1}},
            {"$limit": 10},
        ]).to_list(10)
    except Exception as e:
        log.error("ctop aggregate error: %s", e)
        return await wait.edit_text("❌ <b>Failed to load leaderboard.</b> Please try again.", parse_mode=HTML)

    if not total_rows:
        return await wait.edit_text("📊 <i>No collectors yet.</i>", parse_mode=HTML)

    uid_list = [r["_id"] for r in total_rows]

    # Batch name lookup — one query
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

    # Batch unique-char counts — one aggregation
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

    lines = [
        "🃏 <b>CHARACTER  LEADERBOARD</b> 🃏\n"
        f"<code>{_CDIVIDER}</code>\n"
    ]

    for i, row in enumerate(total_rows):
        pos_emoji, _ = _POS[i] if i < len(_POS) else (f"{i+1}.", None)
        uid    = row["_id"]
        name   = name_map.get(uid) or f"User {uid}"
        total  = row["total"]
        unique = uniq_map.get(uid, 0)

        if i == 0:
            copies_line = f"    📦 <b><code>{_fmt(total)}</code></b> copies  ·  ✨ <b><code>{_fmt(unique)}</code></b> unique"
        else:
            copies_line = f"    📦 <code>{_fmt(total)}</code> copies  ·  ✨ <code>{_fmt(unique)}</code> unique"

        lines.append(
            f"{pos_emoji} <a href=\"tg://user?id={uid}\"><b>{name}</b></a>\n"
            f"{copies_line}"
        )

    lines.append(f"\n<code>{_CDIVIDER}</code>")
    await wait.edit_text("\n".join(lines), parse_mode=HTML, disable_web_page_preview=True)
