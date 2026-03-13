"""SoulCatcher/modules/tops.py

Commands:
  /ktop   — top 10 kakera holders
  /ctop   — top 10 character collectors

Uses top_richest() and top_collectors() from database.py.
Those functions do batch queries against the real MongoDB schema.
"""

from __future__ import annotations
import logging

from pyrogram import filters, enums
from pyrogram.types import Message

from .. import app
from ..database import top_richest, top_collectors

log  = logging.getLogger("SoulCatcher.tops")
HTML = enums.ParseMode.HTML

_DIV   = "━━━━━━━━━━━━━━━━━━━━━━━━"
MEDALS = ["🥇", "🥈", "🥉", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"]


def _fmt(n) -> str:
    try:    return f"{int(n):,}"
    except: return str(n)


def _esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _name(row: dict) -> str:
    uid = row.get("user_id", "?")
    return _esc(
        row.get("first_name") or
        row.get("username") or
        f"User {uid}"
    )


def _link(row: dict) -> str:
    uid = row.get("user_id", 0)
    return f'<a href="tg://user?id={uid}"><b>{_name(row)}</b></a>'


# /ktop — top 10 kakera holders

@app.on_message(filters.command("ktop"))
async def cmd_ktop(_, message: Message):
    wait = await message.reply_text(
        "⏳ <i>Loading kakera leaderboard from database…</i>", parse_mode=HTML
    )
    try:
        rows = await top_richest(10)
    except Exception as e:
        log.error("ktop error: %s", e, exc_info=True)
        return await wait.edit_text(
            f"❌ <b>DB error:</b> <code>{_esc(str(e)[:300])}</code>", parse_mode=HTML
        )

    if not rows:
        return await wait.edit_text("📊 <i>No kakera holders yet.</i>", parse_mode=HTML)

    lines = ["🌸 <b>KAKERA LEADERBOARD</b> 🌸", f"<code>{_DIV}</code>", ""]
    for i, row in enumerate(rows):
        medal = MEDALS[i] if i < len(MEDALS) else f"{i+1}."
        bal   = row.get("balance", 0)
        if i == 0:
            lines += [f"{medal} {_link(row)}", f"   🌸 <b><code>{_fmt(bal)}</code></b> kakera", ""]
        else:
            lines.append(f"{medal} {_link(row)}  —  <code>{_fmt(bal)}</code> 🌸")
    lines += ["", f"<code>{_DIV}</code>"]

    await wait.edit_text("\n".join(lines), parse_mode=HTML, disable_web_page_preview=True)


# /ctop — top 10 character collectors

@app.on_message(filters.command("ctop"))
async def cmd_ctop(_, message: Message):
    wait = await message.reply_text(
        "⏳ <i>Loading collector leaderboard from database…</i>", parse_mode=HTML
    )
    try:
        rows = await top_collectors(10)
    except Exception as e:
        log.error("ctop error: %s", e, exc_info=True)
        return await wait.edit_text(
            f"❌ <b>DB error:</b> <code>{_esc(str(e)[:300])}</code>", parse_mode=HTML
        )

    if not rows:
        return await wait.edit_text("📊 <i>No collectors yet.</i>", parse_mode=HTML)

    lines = ["🃏 <b>CHARACTER LEADERBOARD</b> 🃏", f"<code>{_DIV}</code>", ""]
    for i, row in enumerate(rows):
        medal  = MEDALS[i] if i < len(MEDALS) else f"{i+1}."
        total  = row.get("total",  0)
        unique = row.get("unique", 0)
        if i == 0:
            lines += [
                f"{medal} {_link(row)}",
                f"   📦 <b><code>{_fmt(total)}</code></b> total  ✨ <b><code>{_fmt(unique)}</code></b> unique",
                ""
            ]
        else:
            lines.append(
                f"{medal} {_link(row)}  —  "
                f"<code>{_fmt(total)}</code> total · <code>{_fmt(unique)}</code> unique"
            )
    lines += ["", f"<code>{_DIV}</code>"]

    await wait.edit_text("\n".join(lines), parse_mode=HTML, disable_web_page_preview=True)
