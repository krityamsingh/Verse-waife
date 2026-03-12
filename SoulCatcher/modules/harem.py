"""SoulCatcher/modules/harem.py
Commands: /harem  /collection  /cmode  /collectionmode  /sort
Callbacks: harem:  filter:  apply_filter:  cmode_main:  cmode:  joined_check:

Ported from the reference harem.py (Grabber bot) and adapted to use this
bot's database layer (_col / get_harem / etc.) and rarity system.
"""

from __future__ import annotations
import asyncio
import logging
import math
import random
import time
from html import escape

from pyrogram import filters, enums
from pyrogram.types import (
    Message, InlineKeyboardMarkup as IKM,
    InlineKeyboardButton as IKB,
    InputMediaPhoto, InputMediaVideo,
)

from .. import app
from ..config import SUPPORT_GROUP, UPDATE_CHANNEL
from ..rarity import RARITIES, SUB_RARITIES, get_rarity
from ..database import (
    get_or_create_user, get_user, update_user,
    _col,
)

log = logging.getLogger("SoulCatcher.harem")

# ── Rate-limit guard (per user) ───────────────────────────────────────────────
temp_block: dict[int, float] = {}

# ── Rarity display map (emoji → rarity name) ─────────────────────────────────
def _build_rarity_map() -> dict[str, str]:
    out = {}
    for r in {**RARITIES, **SUB_RARITIES}.values():
        out[r.name] = r.emoji
    return out

RARITY_EMOJI_MAP = _build_rarity_map()

# ── Harem display styles ──────────────────────────────────────────────────────
HAREM_STYLES: dict[int, str] = {
    1: "➤ {anime} ﴾{index}/{total}﴿\n⤷〔{rarity}〕 {id} {name} ×{count}",
    2: "⊙ {anime} ⦋{index}/{total}⦌\n⤷〔{rarity}〕 {id} {name} ×{count}",
    3: "⦾ {anime} 「{index}/{total}」\n✤ 〔{rarity}〕 {id} {name} ×{count}",
    4: "🈴 {anime} 「{index}/{total}」\nID: {id} 〔{rarity}〕 {name} ×{count}",
    5: "⌥ {anime} 〔{index}/{total}〕\n❖ ⌠ {rarity} ⌡ {id} {name} ×{count}",
    6: "⥱ {anime} {index}/{total}\n➥ {id} | {rarity} | {name} ×{count}",
    7: "● {anime} 〔{index}/{total}〕\n𝐈𝐃 : {id} ⌠ {rarity} ⌡ {name} ×{count}",
    8: "🍁 Name: {name} (×{count})\n{rarity} Rarity\n🍀 Anime: {anime} ({index}/{total})",
    9: "❂ {anime} ❘ {index}/{total}\n⌲ {rarity} ❘ {id} ❘ {name} ×{count}",
    10: "⭑ {anime} ╽ {index}/{total}\n┊ {rarity} ╽ {id} ╽ {name} ×{count}",
}

CHARS_PER_PAGE = 15


# ─────────────────────────────────────────────────────────────────────────────
# Helper: membership check (join group + channel)
# ─────────────────────────────────────────────────────────────────────────────

async def check_membership(user_id: int, client) -> bool:
    async def _is_member(chat_id: str, is_channel: bool = False) -> bool:
        from pyrogram.errors import UserNotParticipant
        variants = [chat_id]
        if not str(chat_id).startswith("@"):
            variants.append("@" + str(chat_id))
        for tid in variants:
            try:
                await client.get_chat_member(tid, user_id)
                return True
            except UserNotParticipant:
                return False
            except Exception as exc:
                err = str(exc).lower()
                if is_channel and ("chat_admin_required" in err or "channels.getparticipant" in err):
                    try:
                        await client.get_chat(tid)
                        return True
                    except Exception:
                        return False
        return False

    if not await _is_member(SUPPORT_GROUP):
        return False
    if not await _is_member(UPDATE_CHANNEL, is_channel=True):
        return False
    return True


def get_join_buttons(user_id: int, context: str = "harem") -> IKM:
    return IKM([
        [IKB("🥂 ᴊᴏɪɴ sᴜᴘᴘᴏʀᴛ ɢʀᴏᴜᴘ", url=f"https://t.me/{SUPPORT_GROUP}")],
        [IKB("🧃 ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇ ᴄʜᴀɴɴᴇʟ", url=f"https://t.me/{UPDATE_CHANNEL}")],
        [IKB("✅ I HAVE JOINED", callback_data=f"joined_check:{user_id}:{context}")],
    ])


# ─────────────────────────────────────────────────────────────────────────────
# Data helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _fetch_user_characters(user_id: int):
    """Return (unique_chars, counts, user_doc) filtered by user's collection_mode."""
    user_doc = await _col("users").find_one({"user_id": user_id})
    if not user_doc:
        return None, None, None

    # user_characters collection holds instances
    all_instances = await _col("user_characters").find(
        {"user_id": user_id}
    ).to_list(None)

    if not all_instances:
        return None, None, None

    cmode = (user_doc.get("collection_mode", "all") or "all").lower()

    if cmode == "all":
        filtered = all_instances
    elif cmode in ("anime", "anime sorted"):
        filtered = sorted(all_instances, key=lambda x: x.get("anime", "").lower())
    elif cmode in ("characters", "characters sorted"):
        filtered = sorted(all_instances, key=lambda x: x.get("name", "").lower())
    else:
        # filter by rarity name
        filtered = [c for c in all_instances if (c.get("rarity") or "").lower() == cmode]

    if not filtered:
        return None, None, None

    # Deduplicate by char_id, count occurrences
    counts: dict[str, int] = {}
    char_map: dict[str, dict] = {}
    for c in filtered:
        cid = c.get("char_id") or c.get("id") or c.get("instance_id")
        if not cid:
            continue
        counts[cid] = counts.get(cid, 0) + 1
        if cid not in char_map:
            char_map[cid] = c

    return list(char_map.values()), counts, user_doc


async def _best_cover(user_doc: dict, unique_chars: list) -> dict | None:
    """Return favourite character (as cover image), else random."""
    if not unique_chars:
        return None
    favs = user_doc.get("favorites") or []
    if isinstance(favs, list) and favs:
        fav_id = str(favs[0])
        for c in unique_chars:
            cid = str(c.get("char_id") or c.get("id") or "")
            if cid == fav_id:
                return c
    return random.choice(unique_chars)


# ─────────────────────────────────────────────────────────────────────────────
# Text builder
# ─────────────────────────────────────────────────────────────────────────────

async def _build_harem_text(
    unique_chars: list,
    counts: dict,
    user_doc: dict,
    page: int,
    total_pages: int,
    user_name: str,
    filter_rarity: str | None = None,
) -> str:
    start = page * CHARS_PER_PAGE
    sliced = unique_chars[start: start + CHARS_PER_PAGE]

    text = f"<b>{escape(user_name)}'s Harem — Page {page + 1}/{total_pages}</b>\n"
    if filter_rarity:
        tier = get_rarity(filter_rarity)
        emoji = tier.emoji if tier else "❓"
        text += f"<b>Filtered: {emoji} {filter_rarity}</b>\n"

    # Group by anime
    grouped: dict[str, list] = {}
    for c in sliced:
        anime = c.get("anime") or "Unknown"
        grouped.setdefault(anime, []).append(c)

    char_index = start + 1
    for anime, chars in grouped.items():
        try:
            total_in_db = await _col("characters").count_documents({"anime": anime})
        except Exception:
            total_in_db = "?"
        text += f"\n<b>{escape(anime)}  {len(chars)}/{total_in_db}</b>\n"
        for char in chars:
            cid = char.get("char_id") or char.get("id") or ""
            count = counts.get(cid, 1)
            rarity_name = char.get("rarity") or "common"
            tier = get_rarity(rarity_name)
            emoji = tier.emoji if tier else "❓"
            fav_star = "⭐" if char.get("is_favorite") else ""
            text += (
                f"◈⌠{emoji}⌡{fav_star} {char.get('instance_id', cid)} "
                f"{escape(char.get('name', 'Unknown'))} ×{count}\n"
            )
            char_index += 1

    text += f"\n<b>ᴛᴏᴛᴀʟ ᴜɴɪQᴜᴇ:</b> <code>{len(unique_chars)}</code>"
    return text


def _build_nav_markup(
    user_id: int,
    page: int,
    total_pages: int,
    filter_rarity: str | None,
    total_chars: int,
) -> IKM:
    fr = filter_rarity or "None"

    def _nav_btn(label, target_page):
        if 0 <= target_page < total_pages:
            return IKB(label, callback_data=f"harem:{target_page}:{user_id}:{fr}")
        return IKB(label, callback_data="noop")

    return IKM([
        [_nav_btn("⬅️ ᴘʀᴇᴠ", page - 1),
         IKB(f"📖 {page + 1}/{total_pages}", callback_data="noop"),
         _nav_btn("ɴᴇxᴛ ➡️", page + 1)],
        [_nav_btn("⏪ 2x", page - 2), _nav_btn("2x ⏩", page + 2)],
        [
            IKB(f"🧃 ({total_chars})", switch_inline_query_current_chat=f"collection.{user_id}."),
            IKB("⚜️ Animated", switch_inline_query_current_chat=f"collection.{user_id}.Animated"),
        ],
        [IKB("🔎 Filter by Rarity", callback_data=f"filter:{user_id}")],
    ])


# ─────────────────────────────────────────────────────────────────────────────
# Core display (used by command + all callbacks)
# ─────────────────────────────────────────────────────────────────────────────

async def display_harem(
    client,
    source,          # Message (for initial) or anything
    user_id: int,
    page: int,
    filter_rarity: str | None = None,
    is_initial: bool = False,
    callback_query=None,
):
    try:
        unique_chars, counts, user_doc = await _fetch_user_characters(user_id)
        cmode = user_doc.get("collection_mode", "All") if user_doc else "All"

        # Apply rarity filter on top of cmode
        if filter_rarity and unique_chars:
            unique_chars = [c for c in unique_chars if c.get("rarity") == filter_rarity]
            counts = {(c.get("char_id") or c.get("id") or ""): counts.get(c.get("char_id") or c.get("id") or "", 1) for c in unique_chars}

        if not unique_chars:
            msg = (
                f"❌ No characters with **{filter_rarity}** rarity in your collection!"
                if filter_rarity
                else f"🌸 Your harem is empty! (collection mode: {cmode})\nClaim characters by pressing ❤️ when they spawn."
            )
            if callback_query:
                try:
                    await callback_query.message.edit_text(msg)
                except Exception:
                    pass
            else:
                await source.reply_text(msg)
            return

        total_pages = max(1, math.ceil(len(unique_chars) / CHARS_PER_PAGE))
        page = max(0, min(page, total_pages - 1))

        user_name = (
            callback_query.from_user.first_name
            if callback_query else source.from_user.first_name
        )

        harem_text = await _build_harem_text(
            unique_chars, counts, user_doc, page, total_pages, user_name, filter_rarity
        )
        markup = _build_nav_markup(user_id, page, total_pages, filter_rarity, len(unique_chars))
        cover = await _best_cover(user_doc, unique_chars)

        if is_initial:
            await _send_new(source, cover, harem_text, markup)
        else:
            await _edit_existing(callback_query, cover, harem_text, markup)

    except Exception as exc:
        log.exception("display_harem error user=%d", user_id)
        err = "❌ An error occurred. Please try again."
        if callback_query:
            try:
                await callback_query.message.edit_text(err)
            except Exception:
                pass
        else:
            await source.reply_text(err)


async def _send_new(source: Message, cover: dict | None, text: str, markup: IKM):
    """Send a fresh harem message."""
    if cover:
        vid = cover.get("video_url") or (
            cover.get("img_url") if str(cover.get("img_url", "")).endswith((".mp4", ".gif")) else None
        )
        try:
            if vid:
                await source.reply_video(vid, caption=text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
                return
            elif cover.get("img_url"):
                await source.reply_photo(cover["img_url"], caption=text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
                return
        except Exception:
            pass
    await source.reply_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)


async def _edit_existing(cb, cover: dict | None, text: str, markup: IKM):
    """Edit existing harem message."""
    if cover:
        vid = cover.get("video_url") or (
            cover.get("img_url") if str(cover.get("img_url", "")).endswith((".mp4", ".gif")) else None
        )
        try:
            if vid:
                await cb.message.edit_media(InputMediaVideo(vid, caption=text), reply_markup=markup)
                return
            elif cover.get("img_url"):
                await cb.message.edit_media(InputMediaPhoto(cover["img_url"], caption=text), reply_markup=markup)
                return
        except Exception:
            pass
    try:
        await cb.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# /harem  /collection
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command(["harem", "collection"]))
async def cmd_harem(client, message: Message):
    user_id = message.from_user.id

    if user_id in temp_block and temp_block[user_id] > time.time():
        return

    await get_or_create_user(
        user_id,
        message.from_user.username or "",
        message.from_user.first_name or "",
    )

    if not await check_membership(user_id, client):
        return await message.reply_text(
            f"**🧃 {message.from_user.first_name}, please join our support group & channel first!**",
            reply_markup=get_join_buttons(user_id, context="harem"),
        )

    await display_harem(client, message, user_id, page=0, is_initial=True)


@app.on_callback_query(filters.regex(r"^harem:"))
async def harem_cb(client, cb):
    data = cb.data

    # close button
    if "close" in data:
        parts = data.split("_")
        if len(parts) == 2 and cb.from_user.id == int(parts[1]):
            await cb.answer()
            await cb.message.delete()
        else:
            await cb.answer("This is not your Harem", show_alert=True)
        return

    try:
        _, page_s, uid_s, fr_s = data.split(":")
        page = int(page_s)
        uid = int(uid_s)
        filter_rarity = None if fr_s == "None" else fr_s
    except ValueError:
        return await cb.answer("Invalid data.", show_alert=True)

    if cb.from_user.id != uid:
        return await cb.answer("It's not your Harem!", show_alert=True)

    if not await check_membership(uid, client):
        return await cb.answer("Please join our group & channel first!", show_alert=True)

    await cb.answer()
    await display_harem(client, cb.message, uid, page, filter_rarity, is_initial=False, callback_query=cb)


# ─────────────────────────────────────────────────────────────────────────────
# Rarity filter
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^filter:"))
async def filter_cb(client, cb):
    try:
        uid = int(cb.data.split(":")[1])
    except (IndexError, ValueError):
        return await cb.answer("Invalid.", show_alert=True)

    if cb.from_user.id != uid:
        return await cb.answer("It's not your Harem!", show_alert=True)

    all_tiers = {**RARITIES, **SUB_RARITIES}
    keyboard, row = [], []
    for i, tier in enumerate(all_tiers.values(), 1):
        row.append(IKB(tier.emoji, callback_data=f"apply_filter:{uid}:{tier.name}"))
        if i % 4 == 0:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([IKB("❌ Clear Filter", callback_data=f"apply_filter:{uid}:None")])

    try:
        await cb.message.edit_text(
            "**🔎 Select a rarity to filter your collection:**",
            reply_markup=IKM(keyboard),
        )
    except Exception:
        pass
    await cb.answer()


@app.on_callback_query(filters.regex(r"^apply_filter:"))
async def apply_filter_cb(client, cb):
    try:
        _, uid_s, fr_s = cb.data.split(":", 2)
        uid = int(uid_s)
        filter_rarity = None if fr_s == "None" else fr_s
    except Exception:
        return await cb.answer("Invalid.", show_alert=True)

    if cb.from_user.id != uid:
        return await cb.answer("It's not your Harem!", show_alert=True)

    await cb.answer()
    await display_harem(client, cb.message, uid, 0, filter_rarity, is_initial=False, callback_query=cb)


# ─────────────────────────────────────────────────────────────────────────────
# /cmode  /collectionmode
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command(["cmode", "collectionmode"]) & filters.group)
async def cmd_cmode(client, message: Message):
    user_id = message.from_user.id

    if user_id in temp_block and temp_block[user_id] > time.time():
        return

    if not await check_membership(user_id, client):
        return await message.reply_text(
            f"**ʜᴇʏ {message.from_user.first_name} — please join our support group & channel!**",
            reply_markup=get_join_buttons(user_id, context="cmode"),
        )

    user_doc = await _col("users").find_one({"user_id": user_id}) or {}
    current_mode = user_doc.get("collection_mode", "All")

    full_name = message.from_user.first_name or ""
    if getattr(message.from_user, "last_name", None):
        full_name += " " + message.from_user.last_name

    buttons = _cmode_main_buttons(user_id)
    caption = (
        f"**ʜᴇʏʏᴀ {escape(full_name)}!\n\n"
        "ʏᴏᴜ ᴄᴀɴ ᴄᴜsᴛᴏᴍɪᴢᴇ ʏᴏᴜʀ ᴄᴏʟʟᴇᴄᴛɪᴏɴ ᴍᴏᴅᴇ ʜᴇʀᴇ!**\n\n"
        f"**ᴄᴜʀʀᴇɴᴛ ᴍᴏᴅᴇ:** `{current_mode}`"
    )
    await message.reply_text(caption, reply_markup=IKM(buttons))


def _cmode_main_buttons(user_id: int):
    return [
        [IKB("ʀᴀʀɪᴛʏ", callback_data=f"cmode_main:rarity:{user_id}"),
         IKB("ᴅᴇғᴀᴜʟᴛ (ᴀʟʟ)", callback_data=f"cmode_main:all:{user_id}")],
        [IKB("ᴀɴɪᴍᴇs", callback_data=f"cmode_main:anime:{user_id}"),
         IKB("ᴄʜᴀʀᴀᴄᴛᴇʀs", callback_data=f"cmode_main:character:{user_id}")],
        [IKB("ᴄᴀɴᴄᴇʟ", callback_data=f"cmode_main:cancel:{user_id}")],
    ]


@app.on_callback_query(filters.regex(r"^cmode_main:"))
async def cmode_main_cb(client, cb):
    try:
        _, mode, uid_s = cb.data.split(":")
        uid = int(uid_s)
    except Exception:
        return await cb.answer("Invalid.", show_alert=True)

    if cb.from_user.id != uid:
        return await cb.answer("⚠️ ɴᴏᴛ ʏᴏᴜʀ ᴄᴏʟʟᴇᴄᴛɪᴏɴ!", show_alert=True)

    full_name = cb.from_user.first_name or ""
    if getattr(cb.from_user, "last_name", None):
        full_name += " " + cb.from_user.last_name
    full_name = escape(full_name)

    if mode == "cancel":
        await cb.answer()
        try:
            await cb.message.delete()
        except Exception:
            pass

    elif mode == "rarity":
        tiers = list({**RARITIES, **SUB_RARITIES}.values())
        rarity_rows, row = [], []
        for i, tier in enumerate(tiers, 1):
            row.append(IKB(f"{tier.emoji} {tier.display_name}", callback_data=f"cmode:{tier.name}:{uid}"))
            if i % 3 == 0:
                rarity_rows.append(row)
                row = []
        if row:
            rarity_rows.append(row)
        rarity_rows.append([IKB("🔙 ʙᴀᴄᴋ", callback_data=f"cmode_main:back:{uid}")])
        await cb.answer()
        try:
            await cb.message.edit_text(
                f"**{full_name}, sᴇʟᴇᴄᴛ ʏᴏᴜʀ ʀᴀʀɪᴛʏ ᴍᴏᴅᴇ:**",
                reply_markup=IKM(rarity_rows),
            )
        except Exception:
            pass

    elif mode == "all":
        await _col("users").update_one({"user_id": uid}, {"$set": {"collection_mode": "All"}}, upsert=True)
        await cb.answer("✅ Mode set to All")
        try:
            await cb.message.edit_text(
                f"**{full_name}, ᴄᴏʟʟᴇᴄᴛɪᴏɴ ᴍᴏᴅᴇ ᴜᴘᴅᴀᴛᴇᴅ!**\n\n▸ ɴᴏᴡ sʜᴏᴡɪɴɢ: **ᴀʟʟ ɪᴛᴇᴍs**",
                reply_markup=IKM([[IKB("🔙 ʙᴀᴄᴋ", callback_data=f"cmode_main:back:{uid}")]]),
            )
        except Exception:
            pass

    elif mode == "anime":
        await _col("users").update_one({"user_id": uid}, {"$set": {"collection_mode": "Anime Sorted"}}, upsert=True)
        await cb.answer("✅ Mode set to Anime Sorted")
        try:
            await cb.message.edit_text(
                f"**{full_name}, ᴄᴏʟʟᴇᴄᴛɪᴏɴ ꜱᴏʀᴛᴇᴅ!**\n\n▸ ɴᴏᴡ sʜᴏᴡɪɴɢ: **ᴀɴɪᴍᴇ ᴍᴏᴅᴇ** (A-Z)",
                reply_markup=IKM([[IKB("🔙 ʙᴀᴄᴋ", callback_data=f"cmode_main:back:{uid}")]]),
            )
        except Exception:
            pass

    elif mode == "character":
        await _col("users").update_one({"user_id": uid}, {"$set": {"collection_mode": "Characters Sorted"}}, upsert=True)
        await cb.answer("✅ Mode set to Characters Sorted")
        try:
            await cb.message.edit_text(
                f"**{full_name}, ᴄᴏʟʟᴇᴄᴛɪᴏɴ ꜱᴏʀᴛᴇᴅ!**\n\n▸ ɴᴏᴡ sʜᴏᴡɪɴɢ: **ᴄʜᴀʀᴀᴄᴛᴇʀ ᴍᴏᴅᴇ** (A-Z)",
                reply_markup=IKM([[IKB("🔙 ʙᴀᴄᴋ", callback_data=f"cmode_main:back:{uid}")]]),
            )
        except Exception:
            pass

    elif mode == "back":
        await cb.answer()
        try:
            await cb.message.edit_text(
                f"**ʜᴇʏ {full_name}!\n\nʏᴏᴜ ᴄᴀɴ ᴄᴜsᴛᴏᴍɪᴢᴇ ʏᴏᴜʀ ᴄᴏʟʟᴇᴄᴛɪᴏɴ ᴍᴏᴅᴇ ʜᴇʀᴇ!**",
                reply_markup=IKM(_cmode_main_buttons(uid)),
            )
        except Exception:
            pass


@app.on_callback_query(filters.regex(r"^cmode:"))
async def cmode_cb(client, cb):
    try:
        _, rarity_name, uid_s = cb.data.split(":")
        uid = int(uid_s)
    except Exception:
        return await cb.answer("Invalid.", show_alert=True)

    if cb.from_user.id != uid:
        return await cb.answer("⚠️ ɴᴏᴛ ʏᴏᴜʀ ᴄᴏʟʟᴇᴄᴛɪᴏɴ!", show_alert=True)

    tier = get_rarity(rarity_name)
    if not tier:
        return await cb.answer("⚠️ ɪɴᴠᴀʟɪᴅ ᴍᴏᴅᴇ!", show_alert=True)

    display = f"{tier.emoji} {tier.display_name}"
    await _col("users").update_one(
        {"user_id": uid},
        {"$set": {"collection_mode": rarity_name}},
        upsert=True,
    )

    full_name = cb.from_user.first_name or ""
    if getattr(cb.from_user, "last_name", None):
        full_name += " " + cb.from_user.last_name

    await cb.answer(f"✅ Mode: {display}")
    try:
        await cb.message.edit_text(
            f"**{escape(full_name)}, ᴄᴏʟʟᴇᴄᴛɪᴏɴ ᴍᴏᴅᴇ ᴜᴘᴅᴀᴛᴇᴅ!**\n\n▸ ɴᴏᴡ sʜᴏᴡɪɴɢ: **{display}**",
            reply_markup=IKM([[IKB("🔙 ʙᴀᴄᴋ", callback_data=f"cmode_main:back:{uid}")]]),
        )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# /sort
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("sort"))
async def cmd_sort(_, message: Message):
    valid = ["rarity", "name", "anime", "recent"]
    args = message.command
    if len(args) < 2 or args[1].lower() not in valid:
        return await message.reply_text(f"Usage: `/sort <{'|'.join(valid)}>`")
    val = args[1].lower()
    await update_user(message.from_user.id, {"$set": {"harem_sort": val}})
    await message.reply_text(f"✅ Harem sort order set to **{val}**!")


# ─────────────────────────────────────────────────────────────────────────────
# joined_check callback
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^joined_check:"))
async def joined_check_cb(client, cb):
    await cb.answer("Checking membership...", show_alert=False)
    try:
        _, uid_s, context = cb.data.split(":", 2)
        uid = int(uid_s)
    except Exception:
        return await cb.answer("Invalid request.", show_alert=True)

    if cb.from_user.id != uid:
        return await cb.answer("This button isn't for you.", show_alert=True)

    try:
        ok = await check_membership(uid, client)
    except Exception:
        return await cb.answer("Error checking membership. Try again.", show_alert=True)

    if not ok:
        return await cb.answer("You haven't joined both chats yet!", show_alert=True)

    if context == "harem":
        await display_harem(client, cb.message, uid, 0, None, is_initial=False, callback_query=cb)
    elif context == "cmode":
        user_doc = await _col("users").find_one({"user_id": uid}) or {}
        current_mode = user_doc.get("collection_mode", "All")
        full_name = cb.from_user.first_name or ""
        caption = (
            f"**ʜᴇʏʏᴀ {escape(full_name)}!\n\n"
            "ʏᴏᴜ ᴄᴀɴ ᴄᴜsᴛᴏᴍɪᴢᴇ ʏᴏᴜʀ ᴄᴏʟʟᴇᴄᴛɪᴏɴ ᴍᴏᴅᴇ ʜᴇʀᴇ!**\n\n"
            f"**ᴄᴜʀʀᴇɴᴛ ᴍᴏᴅᴇ:** `{current_mode}`"
        )
        try:
            await cb.message.edit_text(caption, reply_markup=IKM(_cmode_main_buttons(uid)))
        except Exception:
            pass
    else:
        await cb.answer("Membership confirmed — run the command again.", show_alert=True)
