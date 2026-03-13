"""SoulCatcher/modules/tops.py

  /ktop   — top 10 kakera holders
  /ctop   — top 10 character collectors
"""

from __future__ import annotations
import logging

from pyrogram import filters, enums
from pyrogram.types import Message

from .. import app
from ..database import _col

log  = logging.getLogger("SoulCatcher.tops")
HTML = enums.ParseMode.HTML

_DIV  = "━━━━━━━━━━━━━━━━━━━━"
_SDIV = "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄"

MEDALS = ["🥇", "🥈", "🥉", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"]


def _fmt(n) -> str:
    try:
        return f"{int(n):,}"
    except Exception:
        return str(n)


def _esc(text: str) -> str:
    """Escape HTML entities so names never break the message parser."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _link(name: str, uid: int) -> str:
    return f'<a href="tg://user?id={uid}"><b>{_esc(name)}</b></a>'


# ─────────────────────────────────────────────────────────────────────────────
# /ktop — top 10 kakera holders
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("ktop"))
async def cmd_ktop(client, message: Message):
    wait = await message.reply_text("⏳ <i>Loading kakera leaderboard…</i>", parse_mode=HTML)
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

        if not rows:
            return await wait.edit_text("📊 <i>No kakera holders yet.</i>", parse_mode=HTML)

        text = (
            "🌸 <b>KAKERA LEADERBOARD</b> 🌸\n"
            f"<code>{_DIV}</code>\n\n"
        )

        for i, row in enumerate(rows):
            medal = MEDALS[i] if i < len(MEDALS) else f"{i + 1}."
            uid   = row.get("user_id", 0)
            name  = row.get("first_name") or row.get("username") or f"User {uid}"
            bal   = row.get("balance", 0)

            if i == 0:
                text += f"{medal} {_link(name, uid)}\n   🌸 <b><code>{_fmt(bal)}</code></b> kakera\n\n"
            else:
                text += f"{medal} {_link(name, uid)} — <code>{_fmt(bal)}</code> 🌸\n"

        text += f"\n<code>{_DIV}</code>"
        await wait.edit_text(text, parse_mode=HTML, disable_web_page_preview=True)

    except Exception as e:
        log.error("ktop error: %s", e)
        await wait.edit_text("❌ <b>Failed to load leaderboard.</b>", parse_mode=HTML)


# ─────────────────────────────────────────────────────────────────────────────
# /ctop — top 10 character collectors
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("ctop"))
async def cmd_ctop(client, message: Message):
    wait = await message.reply_text("⏳ <i>Loading collector leaderboard…</i>", parse_mode=HTML)
    try:
        # Step 1: rank by total character count
        total_rows = await _col("user_characters").aggregate([
            {"$group": {"_id": "$user_id", "total": {"$sum": 1}}},
            {"$sort":  {"total": -1}},
            {"$limit": 10},
        ]).to_list(10)

        if not total_rows:
            return await wait.edit_text("📊 <i>No collectors yet.</i>", parse_mode=HTML)

        uid_list = [r["_id"] for r in total_rows]

        # Step 2: batch name lookup — one DB query
        user_docs = await _col("users").find(
            {"user_id": {"$in": uid_list}},
            {"user_id": 1, "first_name": 1, "username": 1},
        ).to_list(10)
        name_map = {
            d["user_id"]: (d.get("first_name") or d.get("username") or None)
            for d in user_docs
        }

        # Step 3: batch unique-char count — one aggregation
        try:
            uniq_rows = await _col("user_characters").aggregate([
                {"$match": {"user_id": {"$in": uid_list}}},
                {"$group": {"_id": {"uid": "$user_id", "cid": "$char_id"}}},
                {"$group": {"_id": "$_id.uid", "unique": {"$sum": 1}}},
            ]).to_list(10)
            uniq_map = {r["_id"]: r["unique"] for r in uniq_rows}
        except Exception:
            uniq_map = {}

        text = (
            "🃏 <b>CHARACTER LEADERBOARD</b> 🃏\n"
            f"<code>{_DIV}</code>\n\n"
        )

        for i, row in enumerate(total_rows):
            medal  = MEDALS[i] if i < len(MEDALS) else f"{i + 1}."
            uid    = row["_id"]
            name   = name_map.get(uid) or f"User {uid}"
            total  = row["total"]
            unique = uniq_map.get(uid, 0)

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
        log.error("ctop error: %s", e)
        await wait.edit_text("❌ <b>Failed to load leaderboard.</b>", parse_mode=HTML)
