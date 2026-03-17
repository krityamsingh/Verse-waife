from __future__ import annotations
import math
import logging
from html import escape

from pyrogram import filters, enums
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup as IKM,
    InlineKeyboardButton as IKB,
    InputMediaPhoto,
    InputMediaVideo,
)

from .. import app
from ..rarity import get_rarity
from ..database import get_or_create_user, _col

log = logging.getLogger("SoulCatcher.harem")

CHARS_PER_PAGE = 15


@app.on_message(filters.command("harem"))
async def cmd_harem(client, message: Message):
    uid = message.from_user.id
    await get_or_create_user(
        uid,
        message.from_user.username   or "",
        message.from_user.first_name or "",
        getattr(message.from_user, "last_name", "") or "",
    )
    await _show_harem(message, uid, page=0, is_initial=True)


@app.on_callback_query(filters.regex(r"^h:"))
async def harem_cb(_, cb):
    parts = cb.data.split(":")
    uid   = int(parts[1])
    page  = int(parts[2])

    if cb.from_user.id != uid:
        return await cb.answer("Not your harem!", show_alert=True)

    await cb.answer()
    await _show_harem(cb.message, uid, page=page, is_initial=False, cb=cb)


async def _show_harem(source, uid: int, page: int, is_initial: bool, cb=None):
    chars = await _col("user_characters").find(
        {"user_id": uid}
    ).sort("obtained_at", 1).to_list(None)

    if not chars:
        msg = "🌸 Your harem is empty! Claim characters when they spawn."
        if cb:
            try:   await cb.message.edit_text(msg)
            except Exception: pass
        else:
            await source.reply_text(msg)
        return

    total       = len(chars)
    total_pages = max(1, math.ceil(total / CHARS_PER_PAGE))
    page        = max(0, min(page, total_pages - 1))

    sliced = chars[page * CHARS_PER_PAGE: (page + 1) * CHARS_PER_PAGE]

    name = cb.from_user.first_name if cb else source.from_user.first_name

    lines = [
        f"<b>{escape(name)}'s Harem</b>",
        f"<i>{total} characters  ·  page {page + 1}/{total_pages}</i>",
        "",
    ]

    for char in sliced:
        cid     = char.get("char_id") or char.get("id") or "????"
        tier    = get_rarity(char.get("rarity") or "common")
        r_emoji = tier.emoji if tier else "❓"
        lines.append(
            f"{r_emoji} <code>{cid}</code>  {escape(char.get('name', 'Unknown'))}"
            f"  <i>{escape(char.get('anime', '?'))}</i>"
        )

    text = "\n".join(lines)

    def nb(label, target):
        if 0 <= target < total_pages:
            return IKB(label, callback_data=f"h:{uid}:{target}")
        return IKB("·", callback_data="noop")

    markup = IKM([[
        nb("⬅️", page - 1),
        IKB(f"{page + 1}/{total_pages}", callback_data="noop"),
        nb("➡️", page + 1),
    ]])

    # pick cover from first char on page
    cover = sliced[0] if sliced else None

    if is_initial:
        if cover and cover.get("img_url"):
            try:
                await source.reply_photo(
                    cover["img_url"], caption=text,
                    reply_markup=markup, parse_mode=enums.ParseMode.HTML,
                )
                return
            except Exception:
                pass
        await source.reply_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
    else:
        if cover and cover.get("img_url"):
            try:
                await cb.message.edit_media(
                    InputMediaPhoto(cover["img_url"], caption=text, parse_mode=enums.ParseMode.HTML),
                    reply_markup=markup,
                )
                return
            except Exception:
                pass
        try:
            await cb.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
        except Exception:
            pass
