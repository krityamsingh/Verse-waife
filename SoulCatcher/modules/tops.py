"""SoulCatcher/modules/tops.py

Commands:
  /ktop  —  top 10 users by kakera balance
  /ctop  —  top 10 users by total characters owned

How /ctop works:
  1. Scans the entire user_characters collection
  2. Groups every document by user_id and counts them
  3. Sorts highest → lowest, takes top 30 as headroom
  4. Joins names from the users collection in one batch query
  5. Drops any banned users, caps to 10, re-sorts
  6. Shows total owned + unique character count per user

All queries run here — nothing imported from database.py except _col.
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


# ── tiny helpers ──────────────────────────────────────────────────────────────

def _fmt(n) -> str:
    """1234567  →  '1,234,567'"""
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def _esc(s) -> str:
    """Escape HTML so no name ever breaks the message markup."""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _name(row: dict) -> str:
    """
    Build display name from a users-collection document.
    Priority: first_name [+ last_name]  →  @username  →  User <id>
    """
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
    """Clickable Telegram mention that opens the user profile."""
    uid = row.get("user_id", 0)
    return f'<a href="tg://user?id={uid}"><b>{_name(row)}</b></a>'


def _medal(i: int) -> str:
    return MEDALS[i] if i < len(MEDALS) else f"{i + 1}."


# ── /ktop — kakera leaderboard ────────────────────────────────────────────────

async def _query_richest(limit: int = 10) -> list[dict]:
    """
    Reads the users collection directly.
    Filters:  balance > 0  AND  not banned.
    Sorts:    balance descending.
    Projects: user_id, balance, first_name, last_name, username.
    Single query — no aggregation needed.
    """
    docs = await (
        _col("users")
        .find(
            {
                "balance":   {"$gt": 0},
                "is_banned": {"$ne": True},
            },
            {
                "_id": 0,
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
    return docs


@app.on_message(filters.command("ktop"))
async def cmd_ktop(_, message: Message):
    wait = await message.reply_text(
        "⏳ <i>Loading kakera leaderboard…</i>", parse_mode=HTML
    )

    try:
        rows = await _query_richest(10)
    except Exception as exc:
        log.error("ktop error: %s", exc, exc_info=True)
        await wait.edit_text(
            f"❌ <b>Error:</b> <code>{_esc(str(exc)[:300])}</code>",
            parse_mode=HTML,
        )
        return

    if not rows:
        await wait.edit_text(
            "📊 <i>No kakera holders yet.</i>", parse_mode=HTML
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
            lines.append(
                f"{_medal(i)} {_link(row)}  —  <code>{_fmt(bal)}</code> 🌸"
            )

    lines += ["", f"<code>{_DIV}</code>"]
    await wait.edit_text(
        "\n".join(lines), parse_mode=HTML, disable_web_page_preview=True
    )


# ── /ctop — character collector leaderboard ───────────────────────────────────

async def _query_collectors(limit: int = 10) -> list[dict]:
    """
    Scans the ENTIRE user_characters collection, groups by user_id,
    counts total documents per user (= total characters owned including
    duplicates), and computes unique char_id count as a tiebreaker.

    Steps
    ─────
    1. Aggregate user_characters → total count per user_id.
       Fetch limit*3 rows so we have headroom after dropping banned users.

    2. Batch-fetch name + ban status from users collection in ONE query.
       Any user_id absent from the result (banned or deleted) is silently
       dropped — no per-user roundtrips.

    3. Aggregate user_characters again → unique char_id count, but only
       for the surviving user_ids.

    4. Merge the three datasets, sort by (total desc, unique desc), cap.
    """
    headroom = limit * 3

    # ── Step 1: count total characters per user across entire collection ──
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

    # ── Step 2: batch name lookup — skip banned users ─────────────────────
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

    # Map user_id → name doc; absent means banned/unknown → excluded
    name_map = {doc["user_id"]: doc for doc in user_docs}

    # Filter and cap
    total_agg   = [r for r in total_agg if r["_id"] in name_map][:limit]
    active_ids  = [r["_id"] for r in total_agg]

    if not active_ids:
        return []

    # ── Step 3: unique char_id count for active users only ────────────────
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

    # ── Step 4: merge + final sort ────────────────────────────────────────
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


@app.on_message(filters.command("ctop"))
async def cmd_ctop(_, message: Message):
    wait = await message.reply_text(
        "⏳ <i>Scanning character collection…</i>", parse_mode=HTML
    )

    try:
        rows = await _query_collectors(10)
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
