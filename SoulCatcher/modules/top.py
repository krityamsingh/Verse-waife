"""SoulCatcher/modules/top.py — /top, /topcollectors, /toprich, /topmarried."""
from __future__ import annotations

import SoulCatcher as _soul
from pyrogram import filters
from pyrogram.types import Message, InlineKeyboardMarkup as IKM, InlineKeyboardButton as IKB, CallbackQuery

from SoulCatcher.database import get_top_users, get_top_collectors


def _user_display(u: dict, rank: int) -> str:
    name = u.get("first_name") or u.get("username") or f"User {u['user_id']}"
    uid  = u["user_id"]
    return f"`{rank:>2}.` [{name}](tg://user?id={uid})"


BOARDS = {
    "balance":       ("💰 Richest Users",     "balance",       "kakera"),
    "total_claimed": ("🎴 Top Collectors",     "total_claimed", "chars"),
    "level":         ("⭐ Top by Level",       "level",         "lvl"),
    "total_married": ("💍 Most Married",       "total_married", "marriages"),
    "total_gifted":  ("🎁 Most Generous",      "total_gifted",  "gifted"),
    "total_traded":  ("🔄 Most Traders",       "total_traded",  "trades"),
}


async def _build_board(field: str, limit: int = 10) -> str:
    title, db_field, unit = BOARDS[field]
    users = await get_top_users(field=db_field, limit=limit)
    if not users:
        return f"{title}\n\n_No data yet._"

    lines = []
    for i, u in enumerate(users, 1):
        name  = u.get("first_name") or u.get("username") or f"User {u['user_id']}"
        uid   = u["user_id"]
        value = u.get(db_field, 0)
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"`{i:>2}.`")
        lines.append(f"{medal} [{name}](tg://user?id={uid}) — `{value:,}` {unit}")

    return f"🏆 **{title}**\n\n" + "\n".join(lines)


def _board_buttons(current: str) -> IKM:
    order = list(BOARDS.keys())
    idx   = order.index(current)
    prev  = order[(idx - 1) % len(order)]
    next_ = order[(idx + 1) % len(order)]
    return IKM([[
        IKB("◀", callback_data=f"top:{prev}"),
        IKB("🔄 Refresh", callback_data=f"top:{current}"),
        IKB("▶", callback_data=f"top:{next_}"),
    ]])


@_soul.app.on_message(filters.command(["top", "lb", "leaderboard"]))
async def top_cmd(_, m: Message):
    field = "balance"
    text  = await _build_board(field)
    await m.reply(text, reply_markup=_board_buttons(field), disable_web_page_preview=True)


@_soul.app.on_callback_query(filters.regex(r"^top:(\w+)$"))
async def top_cb(_, cq: CallbackQuery):
    field = cq.data.split(":")[1]
    if field not in BOARDS:
        await cq.answer("Unknown board.", show_alert=True)
        return
    text = await _build_board(field)
    await cq.message.edit_text(text, reply_markup=_board_buttons(field), disable_web_page_preview=True)
    await cq.answer()


# Convenience aliases
@_soul.app.on_message(filters.command(["toprich", "richlist"]))
async def toprich_cmd(_, m: Message):
    text = await _build_board("balance")
    await m.reply(text, reply_markup=_board_buttons("balance"), disable_web_page_preview=True)


@_soul.app.on_message(filters.command(["topcollectors", "bestcollectors"]))
async def top_collectors_cmd(_, m: Message):
    text = await _build_board("total_claimed")
    await m.reply(text, reply_markup=_board_buttons("total_claimed"), disable_web_page_preview=True)


@_soul.app.on_message(filters.command(["topmarried", "mostmarried"]))
async def top_married_cmd(_, m: Message):
    text = await _build_board("total_married")
    await m.reply(text, reply_markup=_board_buttons("total_married"), disable_web_page_preview=True)
