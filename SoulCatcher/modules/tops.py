"""SoulCatcher/modules/tops.py

Commands:
  /ktop  —  top 10 users by kakera balance
  /ctop  —  top 10 users by total characters owned

How /ctop works:
  Scans the entire user_characters collection, groups by user_id,
  counts every document (total owned including duplicates), joins
  names from users collection in one batch query, drops banned users,
  computes unique char count, sorts and shows top 10.

Only imports _col from database.py — no leaderboard functions needed there.
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
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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


# ── /ktop queries ─────────────────────────────────────────────────────────────

async def _fetch_richest(limit: int = 10) -> list:
    """
    Single query on users collection.
    Filters out zero-balance and banned users.
    Sorts by balance descending.
    """
    return await (
        _col("users")
        .find(
            {
                "balance":   {"$gt": 0},
                "is_banned": {"$ne": True},
            },
            {
                "_id":        0,
                "user_id":    1,
                "balance":    1,
                "first_name": 1,
                "last_name":  1,
                "username":   1,
            },
        )
        .sort("balance", -1)
        .limit(limit)
        .to_list(limit)
    )


# ── /ctop queries ─────────────────────────────────────────────────────────────

async def _fetch_collectors(limit: int = 10) -> list:
    """
    Scans the entire user_characters collection.

    Step 1 — group every document by user_id and count → total owned.
             Fetches limit*3 so we have headroom after dropping banned users.

    Step 2 — batch lookup from users collection to get names.
             is_banned filter applied here: banned user_ids simply won't
             appear in name_map so they get silently dropped in step 4.

    Step 3 — second aggregation on user_characters for surviving user_ids:
             deduplicate by (user_id, char_id) then count → unique chars.

    Step 4 — merge, sort by total desc (unique desc as tiebreaker), cap.
    """
    headroom = limit * 3

    # Step 1: total characters per user across whole collection
    total_agg = await _col("user_characters").aggregate([
        {
            "$group": {
                "_id":   "$user_id",
                "total": {"$sum": 1},
            }
        },
        {"$sort":  {"total": -1}},
        {"$limit": headroom},
    ]).to_list(headroom)

    if not total_agg:
        return []

    candidate_ids = [row["_id"] for row in total_agg]

    # Step 2: batch name lookup, banned users excluded
    user_docs = await _col("users").find(
        {
            "user_id":   {"$in": candidate_ids},
            "is_banned": {"$ne": True},
        },
        {
            "_id":        0,
            "user_id":    1,
            "first_name": 1,
            "last_name":  1,
            "username":   1,
        },
    ).to_list(headroom)

    name_map = {doc["user_id"]: doc for doc in user_docs}

    # Drop banned/unknown, cap to limit
    total_agg  = [r for r in total_agg if r["_id"] in name_map][:limit]
    active_ids = [r["_id"] for r in total_agg]

    if not active_ids:
        return []

    # Step 3: unique char_id count for active users only
    unique_agg = await _col("user_characters").aggregate([
        {"$match": {"user_id": {"$in": active_ids}}},
        {
            "$group": {
                "_id": {
                    "uid": "$user_id",
                    "cid": "$char_id",
                }
            }
        },
        {
            "$group": {
                "_id":    "$_id.uid",
                "unique": {"$sum": 1},
            }
        },
    ]).to_list(limit)

    unique_map = {row["_id"]: row["unique"] for row in unique_agg}

    # Step 4: merge + sort
    results = []
    for row in total_agg:
        uid = row["_id"]
        results.append({
            **name_map[uid],
            "total":  row["total"],
            "unique": unique_map.get(uid, 0),
        })

    results.sort(key=lambda x: (x["total"], x["unique"]), reverse=True)
    return results


# ── /ktop handler ─────────────────────────────────────────────────────────────

@app.on_message(filters.command("ktop"))
async def cmd_ktop(_, message: Message):
    wait = await message.reply_text(
        "⏳ <i>Loading kakera leaderboard…</i>", parse_mode=HTML
    )
    try:
        rows = await _fetch_richest(10)
    except Exception as exc:
        log.error("ktop error: %s", exc, exc_info=True)
        await wait.edit_text(
            f"❌ <b>Error:</b> <code>{_esc(str(exc)[:300])}</code>",
            parse_mode=HTML,
        )
        return

    if not rows:
        await wait.edit_text("📊 <i>No kakera holders yet.</i>", parse_mode=HTML)
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


# ── /ctop handler ─────────────────────────────────────────────────────────────

@app.on_message(filters.command("ctop"))
async def cmd_ctop(_, message: Message):
    wait = await message.reply_text(
        "⏳ <i>Scanning character collection…</i>", parse_mode=HTML
    )
    try:
        rows = await _fetch_collectors(10)
    except Exception as exc:
        log.error("ctop error: %s", exc, exc_info=True)
        await wait.edit_text(
            f"❌ <b>Error:</b> <code>{_esc(str(exc)[:300])}</code>",
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
