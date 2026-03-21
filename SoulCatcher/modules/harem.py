from __future__ import annotations
import math
import random
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


def _is_video(url: str) -> bool:
    """Return True if the URL looks like a video file."""
    if not url:
        return False
    low = url.lower().split("?")[0]   # strip query params before checking extension
    return low.endswith((".mp4", ".mkv", ".webm", ".mov", ".avi"))


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

    # deduplicate by char_id, count dupes
    seen: dict[str, int] = {}
    unique: list = []
    for c in chars:
        cid = c.get("char_id") or c.get("id") or ""
        if cid not in seen:
            seen[cid] = 0
            unique.append(c)
        seen[cid] += 1

    total       = len(unique)
    total_pages = max(1, math.ceil(total / CHARS_PER_PAGE))
    page        = max(0, min(page, total_pages - 1))

    sliced = unique[page * CHARS_PER_PAGE: (page + 1) * CHARS_PER_PAGE]

    name = cb.from_user.first_name if cb else source.from_user.first_name

    lines = [
        f"<b>{escape(name)}'s Harem</b>",
        f"<i>{total} characters  ·  page {page + 1}/{total_pages}</i>",
        "",
    ]

    # group by anime
    grouped: dict[str, list] = {}
    for char in sliced:
        anime = char.get("anime") or "Unknown"
        grouped.setdefault(anime, []).append(char)

    for anime, anime_chars in grouped.items():
        lines.append(f"<b>{escape(anime)}</b>")
        for char in anime_chars:
            cid     = char.get("char_id") or char.get("id") or "????"
            count   = seen.get(cid, 1)
            tier    = get_rarity(char.get("rarity") or "common")
            r_emoji = tier.emoji if tier else "❓"
            dup     = f"  ×{count}" if count > 1 else ""
            lines.append(f"  {r_emoji} <code>{cid}</code>  {escape(char.get('name', 'Unknown'))}{dup}")
        lines.append("")

    text = "\n".join(lines)

    def nb(label, target):
        if 0 <= target < total_pages:
            return IKB(label, callback_data=f"h:{uid}:{target}")
        return IKB("·", callback_data="noop")

    markup = IKM([
        [
            nb("⬅️", page - 1),
            IKB(f"{page + 1}/{total_pages}", callback_data="noop"),
            nb("➡️", page + 1),
        ],
        [IKB("📊 Rarity", callback_data=f"h_rarity:{uid}")],
    ])

    # cover image — fav if set, else random from full harem
    user_doc = await _col("users").find_one({"user_id": uid}) or {}
    favs     = user_doc.get("favorites") or []
    cover    = None

    if favs:
        fav_id = str(favs[0])
        for c in chars:
            if str(c.get("char_id") or c.get("id") or "") == fav_id:
                cover = c
                break

    if not cover:
        cover = random.choice(chars)

    url = cover.get("img_url") if cover else None

    if is_initial:
        sent = False
        if url:
            try:
                if _is_video(url):
                    await source.reply_video(
                        url, caption=text,
                        reply_markup=markup, parse_mode=enums.ParseMode.HTML,
                    )
                else:
                    await source.reply_photo(
                        url, caption=text,
                        reply_markup=markup, parse_mode=enums.ParseMode.HTML,
                    )
                sent = True
            except Exception:
                pass
        if not sent:
            await source.reply_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
    else:
        sent = False
        if url:
            try:
                if _is_video(url):
                    await cb.message.edit_media(
                        InputMediaVideo(url, caption=text, parse_mode=enums.ParseMode.HTML),
                        reply_markup=markup,
                    )
                else:
                    await cb.message.edit_media(
                        InputMediaPhoto(url, caption=text, parse_mode=enums.ParseMode.HTML),
                        reply_markup=markup,
                    )
                sent = True
            except Exception:
                pass
        if not sent:
            try:
                await cb.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass


# /fav <char_id>  — set or remove a favourite (cover character)
@app.on_message(filters.command("fav"))
async def cmd_fav(_, message: Message):
    uid  = message.from_user.id
    args = message.command

    if len(args) < 2:
        # show current fav
        user_doc = await _col("users").find_one({"user_id": uid}) or {}
        favs     = user_doc.get("favorites") or []
        if not favs:
            return await message.reply_text(
                "You have no favourite set.\n"
                "Use <code>/fav &lt;char_id&gt;</code> to set one.",
                parse_mode=enums.ParseMode.HTML,
            )
        char = await _col("user_characters").find_one({
            "user_id": uid,
            "$or": [{"char_id": favs[0]}, {"id": favs[0]}],
        })
        if not char:
            return await message.reply_text(
                f"Current fav ID: <code>{favs[0]}</code> (not found in harem).",
                parse_mode=enums.ParseMode.HTML,
            )
        tier    = get_rarity(char.get("rarity") or "common")
        r_emoji = tier.emoji if tier else "❓"
        return await message.reply_text(
            f"⭐ Current fav: {r_emoji} <b>{escape(char.get('name', '?'))}</b>"
            f"  <code>{favs[0]}</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    cid  = args[1].strip()
    char = await _col("user_characters").find_one({
        "user_id": uid,
        "$or": [{"instance_id": cid}, {"char_id": cid}, {"id": cid}],
    })

    if not char:
        return await message.reply_text(
            f"❌ <code>{escape(cid)}</code> not found in your harem.",
            parse_mode=enums.ParseMode.HTML,
        )

    char_cid = char.get("char_id") or char.get("id") or cid

    user_doc = await _col("users").find_one({"user_id": uid}) or {}
    favs     = list(user_doc.get("favorites") or [])

    # toggle off
    if char_cid in favs:
        favs.remove(char_cid)
        await _col("users").update_one(
            {"user_id": uid}, {"$set": {"favorites": favs}}, upsert=True
        )
        return await message.reply_text(
            f"💔 <b>{escape(char.get('name', '?'))}</b> removed from favourites.",
            parse_mode=enums.ParseMode.HTML,
        )

    # set as fav (only keep one — first position = cover)
    if char_cid not in favs:
        favs.insert(0, char_cid)
    await _col("users").update_one(
        {"user_id": uid}, {"$set": {"favorites": favs}}, upsert=True
    )

    tier    = get_rarity(char.get("rarity") or "common")
    r_emoji = tier.emoji if tier else "❓"
    await message.reply_text(
        f"⭐ {r_emoji} <b>{escape(char.get('name', '?'))}</b> set as your harem cover!",
        parse_mode=enums.ParseMode.HTML,
    )


@app.on_callback_query(filters.regex(r"^h_rarity:"))
async def harem_rarity_cb(_, cb):
    uid = int(cb.data.split(":")[1])

    if cb.from_user.id != uid:
        return await cb.answer("Not your harem!", show_alert=True)

    rows = await _col("user_characters").aggregate([
        {"$match": {"user_id": uid}},
        {"$group": {"_id": "$rarity", "count": {"$sum": 1}}},
    ]).to_list(50)

    if not rows:
        return await cb.answer("No characters found.", show_alert=True)

    rows.sort(key=lambda r: r["count"], reverse=True)

    keyboard, row = [], []
    for i, r in enumerate(rows, 1):
        rkey    = r["_id"] or "unknown"
        count   = r["count"]
        tier    = get_rarity(rkey)
        r_emoji = tier.emoji if tier else "❓"
        r_name  = tier.display_name if tier else rkey.title()
        row.append(IKB(
            f"{r_emoji} {r_name} ({count})",
            callback_data=f"h_filter:{uid}:{rkey}:0",
        ))
        if i % 2 == 0:
            keyboard.append(row); row = []
    if row:
        keyboard.append(row)
    keyboard.append([IKB("✖️ Close", callback_data=f"h_rarity_close:{uid}")])

    await cb.answer()
    try:
        await cb.message.reply_text(
            "📊 <b>Your rarity breakdown — tap to filter:</b>",
            reply_markup=IKM(keyboard),
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception:
        pass


@app.on_callback_query(filters.regex(r"^h_rarity_close:"))
async def harem_rarity_close_cb(_, cb):
    uid = int(cb.data.split(":")[1])
    if cb.from_user.id != uid:
        return await cb.answer("Not yours!", show_alert=True)
    await cb.answer()
    try:   await cb.message.delete()
    except Exception: pass


@app.on_callback_query(filters.regex(r"^h_filter:"))
async def harem_filter_cb(_, cb):
    parts      = cb.data.split(":")
    uid        = int(parts[1])
    rarity_key = parts[2]
    page       = int(parts[3])

    if cb.from_user.id != uid:
        return await cb.answer("Not your harem!", show_alert=True)

    chars = await _col("user_characters").find(
        {"user_id": uid, "rarity": rarity_key}
    ).sort("obtained_at", 1).to_list(None)

    tier    = get_rarity(rarity_key)
    r_emoji = tier.emoji if tier else "❓"
    r_name  = tier.display_name if tier else rarity_key.title()

    if not chars:
        return await cb.answer(f"No {r_name} characters.", show_alert=True)

    # deduplicate by char_id, count dupes
    seen: dict[str, int] = {}
    unique: list = []
    for c in chars:
        cid = c.get("char_id") or c.get("id") or ""
        if cid not in seen:
            seen[cid] = 0
            unique.append(c)
        seen[cid] += 1

    total       = len(unique)
    total_pages = max(1, math.ceil(total / CHARS_PER_PAGE))
    page        = max(0, min(page, total_pages - 1))
    sliced      = unique[page * CHARS_PER_PAGE: (page + 1) * CHARS_PER_PAGE]

    name  = cb.from_user.first_name
    lines = [
        f"<b>{escape(name)}'s Harem</b>  —  {r_emoji} {r_name}",
        f"<i>{total} characters  ·  page {page + 1}/{total_pages}</i>",
        "",
    ]

    grouped: dict[str, list] = {}
    for char in sliced:
        grouped.setdefault(char.get("anime") or "Unknown", []).append(char)

    for anime, anime_chars in grouped.items():
        lines.append(f"<b>{escape(anime)}</b>")
        for char in anime_chars:
            cid   = char.get("char_id") or char.get("id") or "????"
            count = seen.get(cid, 1)
            dup   = f"  ×{count}" if count > 1 else ""
            lines.append(f"  {r_emoji} <code>{cid}</code>  {escape(char.get('name', 'Unknown'))}{dup}")
        lines.append("")

    text = "\n".join(lines)

    def fnb(label, target):
        if 0 <= target < total_pages:
            return IKB(label, callback_data=f"h_filter:{uid}:{rarity_key}:{target}")
        return IKB("·", callback_data="noop")

    markup = IKM([
        [fnb("⬅️", page - 1),
         IKB(f"{page + 1}/{total_pages}", callback_data="noop"),
         fnb("➡️", page + 1)],
        [IKB("🔙 Back", callback_data=f"h_rarity_close:{uid}")],
    ])

    # cover — random from this rarity, supports video
    cover = random.choice(chars)
    url   = cover.get("img_url") if cover else None

    await cb.answer()

    sent = False
    if url:
        try:
            if _is_video(url):
                await cb.message.edit_media(
                    InputMediaVideo(url, caption=text, parse_mode=enums.ParseMode.HTML),
                    reply_markup=markup,
                )
            else:
                await cb.message.edit_media(
                    InputMediaPhoto(url, caption=text, parse_mode=enums.ParseMode.HTML),
                    reply_markup=markup,
                )
            sent = True
        except Exception:
            pass
    if not sent:
        try:
            await cb.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
        except Exception:
            pass
