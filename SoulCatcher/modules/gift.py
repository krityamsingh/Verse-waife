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
# Membership verification — checks support group + updates channel
# ─────────────────────────────────────────────────────────────────────────────

async def _check_one(client, chat_id: str, user_id: int) -> bool:
    """
    Returns True if user_id is an active member of chat_id.
    Handles @username and plain username strings.
    Fails open (returns True) if the bot lacks admin rights or the chat
    is misconfigured — so users are never wrongly blocked by a config error.
    """
    from pyrogram.errors import (
        UserNotParticipant, ChatAdminRequired,
        ChannelPrivate, PeerIdInvalid, UsernameNotOccupied,
    )
    from pyrogram.enums import ChatMemberStatus as S
    target = chat_id if str(chat_id).startswith("@") else f"@{chat_id}"
    try:
        member = await client.get_chat_member(target, user_id)
        return member.status not in (S.BANNED, S.LEFT)
    except UserNotParticipant:
        return False
    except ChatAdminRequired:
        log.warning("No admin rights to verify membership in %s — failing open", chat_id)
        return True
    except (ChannelPrivate, PeerIdInvalid, UsernameNotOccupied) as exc:
        log.error("Cannot resolve chat %s: %s — failing open", chat_id, exc)
        return True
    except Exception as exc:
        log.warning("_check_one(%s, %d) unexpected: %s", chat_id, user_id, exc)
        return True


async def check_membership(user_id: int, client) -> tuple[bool, bool]:
    """Returns (group_joined, channel_joined). Both must be True to access gated features."""
    group_ok   = await _check_one(client, SUPPORT_GROUP,  user_id)
    channel_ok = await _check_one(client, UPDATE_CHANNEL, user_id)
    return group_ok, channel_ok


def _join_card(user_id: int, name: str, group_ok: bool, channel_ok: bool, context: str) -> tuple[str, IKM]:
    """
    Build the full membership gate card — both the message text and keyboard together.
    Shows live ✅ / ❌ status for each chat so the user knows exactly what's missing.
    """
    pending = []
    if not group_ok:
        pending.append("Support Group")
    if not channel_ok:
        pending.append("Updates Channel")

    # Status lines
    g_icon = "✅" if group_ok   else "🔴"
    c_icon = "✅" if channel_ok else "🔴"
    g_line = f"{g_icon} Support Group{'  · joined' if group_ok else '  · not joined'}"
    c_line = f"{c_icon} Updates Channel{'  · joined' if channel_ok else '  · not joined'}"

    remaining = len(pending)
    if remaining == 2:
        status_header = "⚠️ You haven't joined either chat yet."
    elif remaining == 1:
        status_header = f"⚠️ Almost there — just join the **{pending[0]}** to continue."
    else:
        status_header = "✅ All joined!"

    text = (
        "🌸 **Access Required**\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Hey **{escape(name)}**!\n\n"
        f"{status_header}\n\n"
        "**Membership Status:**\n"
        f"  {g_line}\n"
        f"  {c_line}\n\n"
        "Join below, then tap **🔄 Verify** to continue."
    )

    rows = []
    # Support Group row
    if not group_ok:
        rows.append([IKB("🫂 Join Support Group", url=f"https://t.me/{SUPPORT_GROUP}")])
    else:
        rows.append([IKB("✅ Support Group — Joined", callback_data="noop")])

    # Updates Channel row
    if not channel_ok:
        rows.append([IKB("📢 Join Updates Channel", url=f"https://t.me/{UPDATE_CHANNEL}")])
    else:
        rows.append([IKB("✅ Updates Channel — Joined", callback_data="noop")])

    rows.append([IKB("🔄 Verify Membership", callback_data=f"joined_check:{user_id}:{context}")])

    return text, IKM(rows)


# ─────────────────────────────────────────────────────────────────────────────
# Data helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _fetch_user_characters(user_id: int):
    """Return (unique_chars, counts, user_doc) filtered by user's collection_mode.

    Always returns user_doc when the user exists, even if their harem is empty.
    Returns (None, None, None) only when the user is not found in DB at all.
    """
    user_doc = await _col("users").find_one({"user_id": user_id})
    if not user_doc:
        return None, None, None

    # user_characters collection holds instances
    all_instances = await _col("user_characters").find(
        {"user_id": user_id}
    ).to_list(None)

    if not all_instances:
        # FIX: return user_doc so callers can show the actual cmode in empty msg
        return [], {}, user_doc

    cmode = (user_doc.get("collection_mode", "all") or "all").lower()

    # FIX: respect the user's harem_sort field for default ordering
    sort_field = (user_doc.get("harem_sort") or "rarity").lower()

    if cmode == "all":
        filtered = all_instances
    elif cmode in ("anime", "anime sorted"):
        filtered = sorted(all_instances, key=lambda x: x.get("anime", "").lower())
    elif cmode in ("characters", "characters sorted"):
        filtered = sorted(all_instances, key=lambda x: x.get("name", "").lower())
    else:
        # FIX: case-insensitive rarity filter
        filtered = [c for c in all_instances if (c.get("rarity") or "").lower() == cmode.lower()]

    if not filtered:
        return [], {}, user_doc

    # FIX: Apply harem_sort when cmode did not already impose its own sort
    if cmode == "all":
        if sort_field == "name":
            filtered = sorted(filtered, key=lambda x: x.get("name", "").lower())
        elif sort_field == "anime":
            filtered = sorted(filtered, key=lambda x: x.get("anime", "").lower())
        elif sort_field == "recent":
            filtered = sorted(filtered, key=lambda x: x.get("obtained_at") or "", reverse=True)
        else:  # default: rarity
            RARITY_ORDER = {r.name: i for i, r in enumerate({**RARITIES, **SUB_RARITIES}.values())}
            filtered = sorted(filtered, key=lambda x: RARITY_ORDER.get(x.get("rarity", ""), 99))

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
            # Show instance_id as the usable ID for /gift /sell /burn.
            # If the doc pre-dates instance_id generation, show char_id as fallback.
            display_id = char.get('instance_id') or cid
            text += (
                f"◈⌠{emoji}⌡{fav_star} {display_id} "
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
        getattr(message.from_user, "last_name", "") or "",
    )

    group_ok, channel_ok = await check_membership(user_id, client)
    if not (group_ok and channel_ok):
        name = message.from_user.first_name or "there"
        text, markup = _join_card(user_id, name, group_ok, channel_ok, "harem")
        return await message.reply_text(text, reply_markup=markup)

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

    group_ok, channel_ok = await check_membership(uid, client)
    if not (group_ok and channel_ok):
        return await cb.answer("Please join our group & updates channel first!", show_alert=True)

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

    group_ok, channel_ok = await check_membership(user_id, client)
    if not (group_ok and channel_ok):
        name = message.from_user.first_name or "there"
        text, markup = _join_card(user_id, name, group_ok, channel_ok, "cmode")
        return await message.reply_text(text, reply_markup=markup)

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
# ─────────────────────────────────────────────────────────────────────────────
# joined_check callback — "🔄 Verify Membership" button
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^joined_check:"))
async def joined_check_cb(client, cb):
    try:
        _, uid_s, context = cb.data.split(":", 2)
        uid = int(uid_s)
    except Exception:
        return await cb.answer("Invalid request.", show_alert=True)

    if cb.from_user.id != uid:
        return await cb.answer("⛔ This button is not for you.", show_alert=True)

    await cb.answer("🔄 Verifying...", show_alert=False)

    try:
        group_ok, channel_ok = await check_membership(uid, client)
    except Exception as exc:
        log.warning("joined_check error uid=%d: %s", uid, exc)
        return await cb.answer("⚠️ Verification failed. Please try again.", show_alert=True)

    name = cb.from_user.first_name or "there"

    # ── Still missing at least one — refresh the card with updated status ─────
    if not (group_ok and channel_ok):
        text, markup = _join_card(uid, name, group_ok, channel_ok, context)
        try:
            await cb.message.edit_text(text, reply_markup=markup)
        except Exception:
            pass
        return

    # ── All joined — unlock the feature ──────────────────────────────────────
    if context == "harem":
        try:
            await cb.message.edit_text(
                f"✅ **Verified, {escape(name)}!** Loading your harem...",
                reply_markup=None,
            )
        except Exception:
            pass
        await display_harem(client, cb.message, uid, 0, None, is_initial=False, callback_query=cb)

    elif context == "cmode":
        user_doc     = await _col("users").find_one({"user_id": uid}) or {}
        current_mode = user_doc.get("collection_mode", "All")
        caption = (
            f"**ʜᴇʏʏᴀ {escape(name)}!**\n\n"
            "ʏᴏᴜ ᴄᴀɴ ᴄᴜsᴛᴏᴍɪᴢᴇ ʏᴏᴜʀ ᴄᴏʟʟᴇᴄᴛɪᴏɴ ᴍᴏᴅᴇ ʜᴇʀᴇ!\n\n"
            f"**ᴄᴜʀʀᴇɴᴛ ᴍᴏᴅᴇ:** `{current_mode}`"
        )
        try:
            await cb.message.edit_text(caption, reply_markup=IKM(_cmode_main_buttons(uid)))
        except Exception:
            pass

    else:
        try:
            await cb.message.edit_text(
                f"✅ **All set, {escape(name)}!**\n\n"
                "You're now verified. Use the command again to continue.",
                reply_markup=None,
            )
        except Exception:
            pass
