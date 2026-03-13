"""SoulCatcher/modules/tops.py

Commands:
  /ktop   — top 10 kakera holders
  /ctop   — top 10 character collectors

All MongoDB queries live directly in this file.
No leaderboard functions needed in database.py.
"""

from __future__ import annotations
import logging

from pyrogram import filters, enums
from pyrogram.types import Message

from .. import app
from ..database import _col

log  = logging.getLogger("SoulCatcher.tops")
HTML = enums.ParseMode.HTML

_DIV   = "━━━━━━━━━━━━━━━━━━━━━━━━"
MEDALS = ["🥇", "🥈", "🥉", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"]


# ── helpers ───────────────────────────────────────────────────────────────────

def _fmt(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def _esc(s) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _display_name(row: dict) -> str:
    uid      = row.get("user_id", "?")
    first    = (row.get("first_name") or "").strip()
    last     = (row.get("last_name")  or "").strip()
    username = (row.get("username")   or "").strip()
    if first:
        return _esc(f"{first} {last}".strip())
    if username:
        return _esc(f"@{username}")
    return _esc(f"User {uid}")


def _link(row: dict) -> str:
    uid = row.get("user_id", 0)
    return f'<a href="tg://user?id={uid}"><b>{_display_name(row)}</b></a>'


def _medal(i: int) -> str:
    return MEDALS[i] if i < len(MEDALS) else f"{i + 1}."


# ── DB queries ────────────────────────────────────────────────────────────────

async def _fetch_richest(limit: int = 10) -> list:
    """
    Top kakera holders sorted by balance descending.
    Banned users are excluded. Single query on users collection.
    Returns list of dicts: {user_id, balance, first_name, last_name, username}
    """
    return await (
        _col("users")
        .find(
            {"balance": {"$gt": 0}, "is_banned": {"$ne": True}},
            {"_id": 0, "user_id": 1, "balance": 1,
             "first_name": 1, "last_name": 1, "username": 1},
        )
        .sort("balance", -1)
        .limit(limit)
        .to_list(limit)
    )


async def _fetch_collectors(limit: int = 10) -> list:
    """
    Top collectors by total characters owned, unique count as tiebreaker.
    Banned users are excluded. 3 queries, zero per-user roundtrips.
    Returns list of dicts: {user_id, total, unique, first_name, last_name, username}
    """
    fetch = limit * 3  # headroom so ban-filtering doesn't shrink list below limit

    # Step 1 — total characters per user, rough top-N
    total_rows = await _col("user_characters").aggregate([
        {"$group": {"_id": "$user_id", "total": {"$sum": 1}}},
        {"$sort":  {"total": -1}},
        {"$limit": fetch},
    ]).to_list(fetch)

    if not total_rows:
        return []

    uid_list = [r["_id"] for r in total_rows]

    # Step 2 — batch name lookup; banned users are absent from result → dropped in step 4
    user_docs = await _col("users").find(
        {"user_id": {"$in": uid_list}, "is_banned": {"$ne": True}},
        {"_id": 0, "user_id": 1, "first_name": 1, "last_name": 1, "username": 1},
    ).to_list(fetch)
    name_map = {d["user_id"]: d for d in user_docs}

    # Drop banned users, cap to limit
    total_rows = [r for r in total_rows if r["_id"] in name_map][:limit]
    uid_list   = [r["_id"] for r in total_rows]

    if not uid_list:
        return []

    # Step 3 — unique char_id count for surviving users only
    uniq_rows = await _col("user_characters").aggregate([
        {"$match":  {"user_id": {"$in": uid_list}}},
        {"$group":  {"_id": {"uid": "$user_id", "cid": "$char_id"}}},
        {"$group":  {"_id": "$_id.uid", "unique": {"$sum": 1}}},
    ]).to_list(limit)
    uniq_map = {r["_id"]: r["unique"] for r in uniq_rows}

    # Step 4 — merge and sort by total desc, unique desc as tiebreaker
    results = []
    for row in total_rows:
        uid  = row["_id"]
        info = name_map.get(uid, {})
        results.append({
            "user_id":    uid,
            "total":      row["total"],
            "unique":     uniq_map.get(uid, 0),
            "first_name": info.get("first_name") or "",
            "last_name":  info.get("last_name")  or "",
            "username":   info.get("username")   or "",
        })

    results.sort(key=lambda x: (x["total"], x["unique"]), reverse=True)
    return results


# ── /ktop ─────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("ktop"))
async def cmd_ktop(_, message: Message):
    """Top 10 users by kakera balance."""
    wait = await message.reply_text(
        "⏳ <i>Loading kakera leaderboard…</i>", parse_mode=HTML
    )
    try:
        rows = await _fetch_richest(10)
    except Exception as exc:
        log.error("ktop error: %s", exc, exc_info=True)
        await wait.edit_text(
            f"❌ <b>Database error:</b>\n<code>{_esc(str(exc)[:300])}</code>",
            parse_mode=HTML,
        )
        return

    if not rows:
        await wait.edit_text(
            "📊 <i>No kakera holders yet. Start claiming characters!</i>",
            parse_mode=HTML,
        )
        return

    lines = ["🌸 <b>KAKERA TOP 10</b> 🌸", f"<code>{_DIV}</code>", ""]
    for i, row in enumerate(rows):
        bal = row.get("balance", 0)
        if i == 0:
            lines += [
                f"{_medal(i)} {_link(row)}",
                f"   🌸 <b><code>{_fmt(bal)}</code></b> kakera",
                "",
            ]
        else:
            lines.append(f"{_medal(i)} {_link(row)}  —  <code>{_fmt(bal)}</code> 🌸")
    lines += ["", f"<code>{_DIV}</code>"]

    await wait.edit_text(
        "\n".join(lines), parse_mode=HTML, disable_web_page_preview=True
    )


# ── /ctop ─────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("ctop"))
async def cmd_ctop(_, message: Message):
    """Top 10 users by total characters owned (unique count as tiebreaker)."""
    wait = await message.reply_text(
        "⏳ <i>Loading collector leaderboard…</i>", parse_mode=HTML
    )
    try:
        rows = await _fetch_collectors(10)
    except Exception as exc:
        log.error("ctop error: %s", exc, exc_info=True)
        await wait.edit_text(
            f"❌ <b>Database error:</b>\n<code>{_esc(str(exc)[:300])}</code>",
            parse_mode=HTML,
        )
        return

    if not rows:
        await wait.edit_text(
            "📊 <i>No collectors yet. Start claiming characters!</i>",
            parse_mode=HTML,
        )
        return

    lines = ["🃏 <b>CHARACTER TOP 10</b> 🃏", f"<code>{_DIV}</code>", ""]
    for i, row in enumerate(rows):
        total  = row.get("total",  0)
        unique = row.get("unique", 0)
        if i == 0:
            lines += [
                f"{_medal(i)} {_link(row)}",
                f"   📦 <b><code>{_fmt(total)}</code></b> total  ✨ <b><code>{_fmt(unique)}</code></b> unique",
                "",
            ]
        else:
            lines.append(
                f"{_medal(i)} {_link(row)}  —  "
                f"<code>{_fmt(total)}</code> total · <code>{_fmt(unique)}</code> unique"
            )
    lines += ["", f"<code>{_DIV}</code>"]

    await wait.edit_text(
        "\n".join(lines), parse_mode=HTML, disable_web_page_preview=True
    )
