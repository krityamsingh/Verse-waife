"""SoulCatcher/modules/harem.py — /harem, /view, /burn, /setfav, /sort, /note, /search."""
from __future__ import annotations

import logging

import SoulCatcher as _soul
from pyrogram import filters
from pyrogram.types import Message, InlineKeyboardMarkup as IKM, InlineKeyboardButton as IKB, CallbackQuery

from SoulCatcher.database import (
    get_or_create_user,
    get_user,
    update_user,
    get_harem,
    get_harem_char,
    get_harem_count,
    get_harem_rarity_counts,
    remove_from_harem,
    add_balance,
    set_favorite,
    set_char_note,
    search_characters,
    get_character,
)
from SoulCatcher.rarity import get_sell_price, rarity_display, RARITIES, SUB_RARITIES

log = logging.getLogger("SoulCatcher.harem")

PER_PAGE = 10


def _rarity_sort_key(rarity_name: str) -> int:
    all_r = {**RARITIES, **SUB_RARITIES}
    r = all_r.get(rarity_name)
    return r.id if r else 99


def _format_harem_page(chars: list[dict], page: int, total: int) -> str:
    lines = []
    for i, c in enumerate(chars, start=(page - 1) * PER_PAGE + 1):
        r      = rarity_display(c["rarity"])
        fav    = "⭐" if c.get("is_favorite") else ""
        lines.append(f"`{i:>3}.` {r} **{c['name']}** {fav}\n       📺 *{c.get('anime','Unknown')}* | `{c['instance_id']}`")
    return "\n".join(lines)


# ── /harem ────────────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command(["harem", "collection", "h"]))
async def harem_cmd(_, m: Message):
    target  = m.reply_to_message.from_user if m.reply_to_message else m.from_user
    parts   = m.text.split()
    page    = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
    u_data  = await get_or_create_user(target.id, target.username or "", target.first_name or "", target.last_name or "")
    sort_by = u_data.get("harem_sort", "rarity")

    chars, total = await get_harem(target.id, page=page, per_page=PER_PAGE, sort_by=sort_by)
    if not chars:
        await m.reply(f"📭 **{target.first_name}'s harem** is empty!")
        return

    pages = (total + PER_PAGE - 1) // PER_PAGE
    text  = (
        f"🌸 **{target.first_name}'s Harem** | `{total:,}` chars | Page {page}/{pages}\n\n"
        f"{_format_harem_page(chars, page, total)}"
    )

    buttons = []
    row = []
    if page > 1:
        row.append(IKB("◀ Prev", callback_data=f"harem:{target.id}:{page-1}:{sort_by}"))
    if page < pages:
        row.append(IKB("Next ▶", callback_data=f"harem:{target.id}:{page+1}:{sort_by}"))
    if row:
        buttons.append(row)

    markup = IKM(buttons) if buttons else None
    await m.reply(text, reply_markup=markup)


@_soul.app.on_callback_query(filters.regex(r"^harem:(\d+):(\d+):(\w+)$"))
async def harem_page_cb(_, cq: CallbackQuery):
    _, uid, page, sort_by = cq.data.split(":")
    uid  = int(uid)
    page = int(page)

    if cq.from_user.id != uid:
        await cq.answer("❌ This is not your harem!", show_alert=True)
        return

    chars, total = await get_harem(uid, page=page, per_page=PER_PAGE, sort_by=sort_by)
    if not chars:
        await cq.answer("No more pages.", show_alert=True)
        return

    pages = (total + PER_PAGE - 1) // PER_PAGE
    text  = (
        f"🌸 **Your Harem** | `{total:,}` chars | Page {page}/{pages}\n\n"
        f"{_format_harem_page(chars, page, total)}"
    )

    buttons, row = [], []
    if page > 1:
        row.append(IKB("◀ Prev", callback_data=f"harem:{uid}:{page-1}:{sort_by}"))
    if page < pages:
        row.append(IKB("Next ▶", callback_data=f"harem:{uid}:{page+1}:{sort_by}"))
    if row:
        buttons.append(row)

    await cq.message.edit_text(text, reply_markup=IKM(buttons) if buttons else None)
    await cq.answer()


# ── /view ─────────────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command(["view", "card", "v"]))
async def view_cmd(_, m: Message):
    parts = m.text.split()
    if len(parts) < 2:
        await m.reply("Usage: `/view <instanceID>`")
        return

    instance_id = parts[1].upper()
    uid         = m.from_user.id
    char        = await get_harem_char(uid, instance_id)

    if not char:
        await m.reply("❌ Character not found in your harem.")
        return

    r_str   = rarity_display(char["rarity"])
    fav     = "⭐ Favourite" if char.get("is_favorite") else ""
    note    = f"\n📝 Note: *{char['note']}*" if char.get("note") else ""
    obtained = char.get("obtained_at", "?")
    if hasattr(obtained, "strftime"):
        obtained = obtained.strftime("%Y-%m-%d")

    caption = (
        f"🎴 **{char['name']}** {fav}\n"
        f"📺 *{char.get('anime', 'Unknown')}*\n"
        f"✨ {r_str}\n"
        f"🆔 Instance: `{instance_id}`\n"
        f"📅 Obtained: `{obtained}`"
        f"{note}"
    )

    buttons = IKM([[
        IKB("⭐ Fav", callback_data=f"fav:{uid}:{instance_id}"),
        IKB("🔥 Burn", callback_data=f"burn_confirm:{uid}:{instance_id}"),
    ]])

    try:
        if char.get("video_url"):
            await m.reply_video(char["video_url"], caption=caption, reply_markup=buttons)
        elif char.get("img_url"):
            await m.reply_photo(char["img_url"], caption=caption, reply_markup=buttons)
        else:
            await m.reply(caption, reply_markup=buttons)
    except Exception:
        await m.reply(caption, reply_markup=buttons)


# ── /burn ─────────────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command(["burn", "sell"]))
async def burn_cmd(_, m: Message):
    parts = m.text.split()
    if len(parts) < 2:
        await m.reply("Usage: `/burn <instanceID>`")
        return

    instance_id = parts[1].upper()
    uid         = m.from_user.id
    char        = await get_harem_char(uid, instance_id)

    if not char:
        await m.reply("❌ Character not found in your harem.")
        return

    price   = get_sell_price(char["rarity"])
    r_str   = rarity_display(char["rarity"])

    buttons = IKM([[
        IKB("✅ Confirm Burn", callback_data=f"burn_do:{uid}:{instance_id}:{price}"),
        IKB("❌ Cancel",       callback_data=f"burn_cancel:{uid}"),
    ]])

    await m.reply(
        f"🔥 Burn **{char['name']}** ({r_str})?\n"
        f"💰 You'll receive: `{price:,}` kakera\n\n"
        "⚠️ **This cannot be undone!**",
        reply_markup=buttons,
    )


@_soul.app.on_callback_query(filters.regex(r"^burn_do:(\d+):(\w+):(\d+)$"))
async def burn_do_cb(_, cq: CallbackQuery):
    _, uid, instance_id, price = cq.data.split(":")
    uid, price = int(uid), int(price)

    if cq.from_user.id != uid:
        await cq.answer("❌ Not your character!", show_alert=True)
        return

    char    = await get_harem_char(uid, instance_id)
    if not char:
        await cq.answer("❌ Character not found.", show_alert=True)
        return

    removed = await remove_from_harem(uid, instance_id)
    if removed:
        await add_balance(uid, price)
        await cq.message.edit_text(
            f"🔥 **{char['name']}** was burned!\n💰 +{price:,} kakera added to your balance."
        )
    else:
        await cq.answer("❌ Could not burn character.", show_alert=True)


@_soul.app.on_callback_query(filters.regex(r"^burn_cancel:(\d+)$"))
async def burn_cancel_cb(_, cq: CallbackQuery):
    if cq.from_user.id != int(cq.data.split(":")[1]):
        await cq.answer("Not yours!", show_alert=True)
        return
    await cq.message.edit_text("❌ Burn cancelled.")


@_soul.app.on_callback_query(filters.regex(r"^burn_confirm:(\d+):(\w+)$"))
async def burn_confirm_cb(_, cq: CallbackQuery):
    _, uid, instance_id = cq.data.split(":")
    uid = int(uid)
    if cq.from_user.id != uid:
        await cq.answer("Not yours!", show_alert=True)
        return
    char = await get_harem_char(uid, instance_id)
    if not char:
        await cq.answer("Not found!", show_alert=True)
        return
    price = get_sell_price(char["rarity"])
    buttons = IKM([[
        IKB("✅ Confirm", callback_data=f"burn_do:{uid}:{instance_id}:{price}"),
        IKB("❌ Cancel",  callback_data=f"burn_cancel:{uid}"),
    ]])
    await cq.message.edit_text(
        f"🔥 Burn **{char['name']}**?\n💰 Reward: `{price:,}` kakera\n\n⚠️ Cannot be undone!",
        reply_markup=buttons,
    )


# ── Favourite callback ────────────────────────────────────────────────────────

@_soul.app.on_callback_query(filters.regex(r"^fav:(\d+):(\w+)$"))
async def fav_cb(_, cq: CallbackQuery):
    _, uid, instance_id = cq.data.split(":")
    uid = int(uid)
    if cq.from_user.id != uid:
        await cq.answer("Not yours!", show_alert=True)
        return
    char = await get_harem_char(uid, instance_id)
    if not char:
        await cq.answer("Not found!", show_alert=True)
        return
    new_val = not char.get("is_favorite", False)
    await set_favorite(uid, instance_id, new_val)
    await cq.answer("⭐ Added to favourites!" if new_val else "Removed from favourites.")


# ── /setfav ───────────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command(["setfav", "fav", "favourite"]))
async def setfav_cmd(_, m: Message):
    parts = m.text.split()
    if len(parts) < 2:
        await m.reply("Usage: `/setfav <instanceID>`")
        return

    instance_id = parts[1].upper()
    uid         = m.from_user.id
    char        = await get_harem_char(uid, instance_id)

    if not char:
        await m.reply("❌ Character not found in your harem.")
        return

    new_val = not char.get("is_favorite", False)
    await set_favorite(uid, instance_id, new_val)
    status = "⭐ Added to" if new_val else "Removed from"
    await m.reply(f"{status} favourites: **{char['name']}**")


# ── /note ─────────────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command("note"))
async def note_cmd(_, m: Message):
    parts = m.text.split(maxsplit=2)
    if len(parts) < 3:
        await m.reply("Usage: `/note <instanceID> <text>`")
        return

    instance_id = parts[1].upper()
    note_text   = parts[2][:200]
    uid         = m.from_user.id
    char        = await get_harem_char(uid, instance_id)

    if not char:
        await m.reply("❌ Character not found.")
        return

    await set_char_note(uid, instance_id, note_text)
    await m.reply(f"📝 Note set on **{char['name']}**: *{note_text}*")


# ── /sort ─────────────────────────────────────────────────────────────────────

VALID_SORTS = {"rarity", "name", "anime", "recent"}


@_soul.app.on_message(filters.command("sort"))
async def sort_cmd(_, m: Message):
    parts = m.text.split()
    if len(parts) < 2 or parts[1] not in VALID_SORTS:
        await m.reply(f"Usage: `/sort <{'|'.join(VALID_SORTS)}>`")
        return

    sort_by = parts[1]
    await update_user(m.from_user.id, {"$set": {"harem_sort": sort_by}})
    await m.reply(f"✅ Harem sorted by **{sort_by}**.")


# ── /search ───────────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command(["search", "find", "lookup"]))
async def search_cmd(_, m: Message):
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2:
        await m.reply("Usage: `/search <character name>`")
        return

    query   = parts[1].strip()
    results = await search_characters(query, limit=8)

    if not results:
        await m.reply(f"🔍 No characters found for: **{query}**")
        return

    lines = []
    for c in results:
        r_str = rarity_display(c["rarity"])
        lines.append(f"🆔 `{c['id']}` — **{c['name']}** | {r_str} | 📺 *{c.get('anime','?')}*")

    await m.reply(
        f"🔍 Search results for **{query}** ({len(results)} found):\n\n"
        + "\n".join(lines)
    )
