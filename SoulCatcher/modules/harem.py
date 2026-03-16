"""SoulCatcher/modules/harem.py

Commands  : /harem  /collection  /cmode  /collectionmode  /sort
            /gallery  /setfav  /fav  /hstats
Callbacks : harem:  filter:  apply_filter:  cmode_main:  cmode:
            joined_check:  gallery:  fav_action:  hstats_cb:

New / Improved in this version
───────────────────────────────
 ✦ /gallery   — Browse EVERY character uploaded to the bot (global)
 ✦ /setfav    — Mark a character as your favourite cover
 ✦ /fav       — Quick-view your favourite characters list
 ✦ /hstats    — Rarity breakdown pie-chart of your harem (text art)
 ✦ "Verse of" — Inline button now uses "verse of" instead of "Animated"
 ✦ Rarity     — Fully fixed ordering + emoji for every tier
 ✦ Lighter    — Fewer DB calls, cleaner text layout, compact buttons
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
    Message,
    InlineKeyboardMarkup as IKM,
    InlineKeyboardButton as IKB,
    InputMediaPhoto,
    InputMediaVideo,
)

from .. import app
from ..config import SUPPORT_GROUP, UPDATE_CHANNEL
from ..rarity import RARITIES, SUB_RARITIES, get_rarity
from ..database import get_or_create_user, get_user, update_user, _col

log = logging.getLogger("SoulCatcher.harem")

# ── Rate-limit guard ──────────────────────────────────────────────────────────
temp_block: dict[int, float] = {}
RATE_COOLDOWN = 3  # seconds between harem page flips

# ── Rarity ordering (lower index = more common) ───────────────────────────────
def _rarity_order() -> dict[str, int]:
    return {r.name: i for i, r in enumerate({**RARITIES, **SUB_RARITIES}.values())}

RARITY_ORDER = _rarity_order()

CHARS_PER_PAGE      = 15
GALLERY_PER_PAGE    = 12
FAV_MAX             = 10


# ─────────────────────────────────────────────────────────────────────────────
# Membership gate
# ─────────────────────────────────────────────────────────────────────────────

async def _check_one(client, chat_id: str, user_id: int) -> bool:
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
    except (ChatAdminRequired, ChannelPrivate, PeerIdInvalid, UsernameNotOccupied) as exc:
        log.warning("_check_one(%s): %s — failing open", chat_id, exc)
        return True
    except Exception as exc:
        log.warning("_check_one(%s, %d) unexpected: %s", chat_id, user_id, exc)
        return True


async def check_membership(user_id: int, client) -> tuple[bool, bool]:
    g, c = await asyncio.gather(
        _check_one(client, SUPPORT_GROUP,  user_id),
        _check_one(client, UPDATE_CHANNEL, user_id),
    )
    return g, c


def _join_card(user_id: int, name: str, group_ok: bool, channel_ok: bool, context: str) -> tuple[str, IKM]:
    g_icon = "✅" if group_ok   else "❌"
    c_icon = "✅" if channel_ok else "❌"

    missing = []
    if not group_ok:   missing.append("Support Group")
    if not channel_ok: missing.append("Updates Channel")

    if len(missing) == 2:
        header = "You haven't joined either chat yet."
    elif missing:
        header = f"Almost there — join the **{missing[0]}** to continue."
    else:
        header = "All joined!"

    text = (
        "🔐 **Access Required**\n"
        "──────────────────────\n"
        f"Hey **{escape(name)}**!\n\n"
        f"⚠️ {header}\n\n"
        f"  {g_icon} Support Group\n"
        f"  {c_icon} Updates Channel\n\n"
        "Join below then tap **🔄 Verify**."
    )

    rows = []
    if not group_ok:
        rows.append([IKB("🫂 Join Support Group", url=f"https://t.me/{SUPPORT_GROUP}")])
    else:
        rows.append([IKB("✅ Support Group — Joined", callback_data="noop")])

    if not channel_ok:
        rows.append([IKB("📢 Join Updates Channel", url=f"https://t.me/{UPDATE_CHANNEL}")])
    else:
        rows.append([IKB("✅ Updates Channel — Joined", callback_data="noop")])

    rows.append([IKB("🔄 Verify Membership", callback_data=f"joined_check:{user_id}:{context}")])
    return text, IKM(rows)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _fetch_user_characters(user_id: int):
    """Return (unique_chars, counts, user_doc).  Returns (None,None,None) if user not found."""
    user_doc = await _col("users").find_one({"user_id": user_id})
    if not user_doc:
        return None, None, None

    all_instances = await _col("user_characters").find({"user_id": user_id}).to_list(None)
    if not all_instances:
        return [], {}, user_doc

    cmode      = (user_doc.get("collection_mode", "all") or "all").lower()
    sort_field = (user_doc.get("harem_sort") or "rarity").lower()

    # Apply collection mode filter / sort
    if cmode in ("anime", "anime sorted"):
        filtered = sorted(all_instances, key=lambda x: x.get("anime", "").lower())
    elif cmode in ("characters", "characters sorted"):
        filtered = sorted(all_instances, key=lambda x: x.get("name", "").lower())
    elif cmode == "all":
        filtered = all_instances
    else:
        # rarity-name filter (case-insensitive)
        filtered = [c for c in all_instances if (c.get("rarity") or "").lower() == cmode.lower()]

    if not filtered:
        return [], {}, user_doc

    # Apply harem_sort when cmode didn't impose its own order
    if cmode == "all":
        if sort_field == "name":
            filtered = sorted(filtered, key=lambda x: x.get("name", "").lower())
        elif sort_field == "anime":
            filtered = sorted(filtered, key=lambda x: x.get("anime", "").lower())
        elif sort_field == "recent":
            filtered = sorted(filtered, key=lambda x: x.get("obtained_at") or "", reverse=True)
        else:  # rarity (default)
            filtered = sorted(filtered, key=lambda x: RARITY_ORDER.get(x.get("rarity", ""), 99))

    # Deduplicate — keep first occurrence per char_id, count extras
    counts:   dict[str, int]  = {}
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
    if not unique_chars:
        return None
    favs = user_doc.get("favorites") or []
    if isinstance(favs, list):
        for fav_id in favs:
            for c in unique_chars:
                cid = str(c.get("char_id") or c.get("id") or "")
                if cid == str(fav_id):
                    return c
    return random.choice(unique_chars)


def _rarity_line(char: dict) -> str:
    """Return  emoji + rarity name  for a character doc."""
    rarity_name = char.get("rarity") or "common"
    tier = get_rarity(rarity_name)
    emoji = tier.emoji if tier else "❓"
    return f"{emoji} {rarity_name.title()}"


# ─────────────────────────────────────────────────────────────────────────────
# Harem text builder
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
    start  = page * CHARS_PER_PAGE
    sliced = unique_chars[start: start + CHARS_PER_PAGE]

    favs = set(str(f) for f in (user_doc.get("favorites") or []))

    lines = [f"<b>❤️ {escape(user_name)}'s Harem</b>  〔{page + 1}/{total_pages}〕"]
    if filter_rarity:
        tier  = get_rarity(filter_rarity)
        emoji = tier.emoji if tier else "❓"
        lines.append(f"<i>Filter: {emoji} {filter_rarity}</i>")
    lines.append("")

    grouped: dict[str, list] = {}
    for c in sliced:
        anime = c.get("anime") or "Unknown"
        grouped.setdefault(anime, []).append(c)

    for anime, chars in grouped.items():
        try:
            total_in_db = await _col("characters").count_documents({"anime": anime})
        except Exception:
            total_in_db = "?"
        lines.append(f"<b>🎴 {escape(anime)}</b>  <code>{len(chars)}/{total_in_db}</code>")
        for char in chars:
            cid        = char.get("char_id") or char.get("id") or ""
            count      = counts.get(cid, 1)
            tier       = get_rarity(char.get("rarity") or "common")
            emoji      = tier.emoji if tier else "❓"
            star       = "⭐" if str(cid) in favs else ""
            display_id = char.get("instance_id") or cid
            dup_tag    = f" ×{count}" if count > 1 else ""
            lines.append(
                f"  {emoji}{star} <code>{display_id}</code> "
                f"{escape(char.get('name', 'Unknown'))}{dup_tag}"
            )
        lines.append("")

    lines.append(f"<b>Unique:</b> <code>{len(unique_chars)}</code>  •  "
                 f"<b>Page:</b> <code>{page + 1}/{total_pages}</code>")
    return "\n".join(lines)


def _build_nav_markup(
    user_id: int,
    page: int,
    total_pages: int,
    filter_rarity: str | None,
    total_chars: int,
) -> IKM:
    fr = filter_rarity or "None"

    def nb(label, target):
        if 0 <= target < total_pages:
            return IKB(label, callback_data=f"harem:{target}:{user_id}:{fr}")
        return IKB("·", callback_data="noop")

    return IKM([
        [nb("⬅️", page - 1),
         IKB(f"📖 {page + 1}/{total_pages}", callback_data="noop"),
         nb("➡️", page + 1)],
        [nb("⏪ ×2", page - 2), nb("×2 ⏩", page + 2)],
        [
            IKB(f"🎴 ({total_chars})", switch_inline_query_current_chat=f"collection.{user_id}."),
            IKB("🌸 Verse of", switch_inline_query_current_chat=f"collection.{user_id}.VerseSeries"),
        ],
        [
            IKB("🔎 Filter", callback_data=f"filter:{user_id}"),
            IKB("📊 Stats",  callback_data=f"hstats_cb:{user_id}"),
            IKB("⭐ Favs",  callback_data=f"fav_action:list:{user_id}"),
        ],
    ])


# ─────────────────────────────────────────────────────────────────────────────
# Core display engine
# ─────────────────────────────────────────────────────────────────────────────

async def display_harem(
    client,
    source,
    user_id: int,
    page: int,
    filter_rarity: str | None = None,
    is_initial: bool = False,
    callback_query=None,
):
    try:
        unique_chars, counts, user_doc = await _fetch_user_characters(user_id)
        cmode = user_doc.get("collection_mode", "All") if user_doc else "All"

        # Rarity filter on top of cmode
        if filter_rarity and unique_chars:
            unique_chars = [c for c in unique_chars if c.get("rarity") == filter_rarity]
            counts       = {
                (c.get("char_id") or c.get("id") or ""): counts.get(c.get("char_id") or c.get("id") or "", 1)
                for c in unique_chars
            }

        if not unique_chars:
            empty = (
                f"❌ No **{filter_rarity}** characters in your collection!"
                if filter_rarity
                else f"🌸 Your harem is empty!\n_(Collection mode: {cmode})_\nClaim characters when they spawn ❤️"
            )
            if callback_query:
                try:   await callback_query.message.edit_text(empty)
                except Exception: pass
            else:
                await source.reply_text(empty)
            return

        total_pages = max(1, math.ceil(len(unique_chars) / CHARS_PER_PAGE))
        page        = max(0, min(page, total_pages - 1))

        user_name = (
            callback_query.from_user.first_name if callback_query else source.from_user.first_name
        )

        text   = await _build_harem_text(unique_chars, counts, user_doc, page, total_pages, user_name, filter_rarity)
        markup = _build_nav_markup(user_id, page, total_pages, filter_rarity, len(unique_chars))
        cover  = await _best_cover(user_doc, unique_chars)

        if is_initial:
            await _send_new(source, cover, text, markup)
        else:
            await _edit_existing(callback_query, cover, text, markup)

    except Exception:
        log.exception("display_harem error user=%d", user_id)
        err = "❌ An error occurred. Please try again."
        if callback_query:
            try:   await callback_query.message.edit_text(err)
            except Exception: pass
        else:
            await source.reply_text(err)


async def _send_new(source: Message, cover: dict | None, text: str, markup: IKM):
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
    if cover:
        vid = cover.get("video_url") or (
            cover.get("img_url") if str(cover.get("img_url", "")).endswith((".mp4", ".gif")) else None
        )
        try:
            if vid:
                await cb.message.edit_media(InputMediaVideo(vid, caption=text, parse_mode=enums.ParseMode.HTML), reply_markup=markup)
                return
            elif cover.get("img_url"):
                await cb.message.edit_media(InputMediaPhoto(cover["img_url"], caption=text, parse_mode=enums.ParseMode.HTML), reply_markup=markup)
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
    uid = message.from_user.id

    if uid in temp_block and temp_block[uid] > time.time():
        return await message.reply_text("⏳ Slow down! Try again in a moment.")

    await get_or_create_user(
        uid,
        message.from_user.username or "",
        message.from_user.first_name or "",
        getattr(message.from_user, "last_name", "") or "",
    )

    group_ok, channel_ok = await check_membership(uid, client)
    if not (group_ok and channel_ok):
        text, markup = _join_card(uid, message.from_user.first_name or "there", group_ok, channel_ok, "harem")
        return await message.reply_text(text, reply_markup=markup)

    await display_harem(client, message, uid, page=0, is_initial=True)


@app.on_callback_query(filters.regex(r"^harem:"))
async def harem_cb(client, cb):
    data = cb.data

    if "close" in data:
        parts = data.split("_")
        if len(parts) == 2 and cb.from_user.id == int(parts[1]):
            await cb.answer(); await cb.message.delete()
        else:
            await cb.answer("This is not your Harem!", show_alert=True)
        return

    try:
        _, page_s, uid_s, fr_s = data.split(":")
        page = int(page_s); uid = int(uid_s)
        filter_rarity = None if fr_s == "None" else fr_s
    except ValueError:
        return await cb.answer("Invalid data.", show_alert=True)

    if cb.from_user.id != uid:
        return await cb.answer("It's not your Harem!", show_alert=True)

    # Light rate-limit
    now = time.time()
    if uid in temp_block and temp_block[uid] > now:
        return await cb.answer("⏳ Too fast!", show_alert=False)
    temp_block[uid] = now + RATE_COOLDOWN

    group_ok, channel_ok = await check_membership(uid, client)
    if not (group_ok and channel_ok):
        return await cb.answer("Join our group & channel first!", show_alert=True)

    await cb.answer()
    await display_harem(client, cb.message, uid, page, filter_rarity, is_initial=False, callback_query=cb)


# ─────────────────────────────────────────────────────────────────────────────
# Rarity filter callbacks
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
        row.append(IKB(f"{tier.emoji} {tier.display_name}", callback_data=f"apply_filter:{uid}:{tier.name}"))
        if i % 3 == 0:
            keyboard.append(row); row = []
    if row:
        keyboard.append(row)
    keyboard.append([IKB("✖️ Clear Filter", callback_data=f"apply_filter:{uid}:None")])
    keyboard.append([IKB("🔙 Back", callback_data=f"harem:0:{uid}:None")])

    try:
        await cb.message.edit_text("🔎 **Select rarity to filter:**", reply_markup=IKM(keyboard))
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
# /setfav — Set favourite character (cover image)
# Usage:  /setfav <instance_id>
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("setfav"))
async def cmd_setfav(_, message: Message):
    args = message.command
    if len(args) < 2:
        return await message.reply_text(
            "⭐ **Set Favourite**\n"
            "Usage: `/setfav <character_id>`\n\n"
            "The character will appear as your harem cover.\n"
            f"You can set up to **{FAV_MAX}** favourites.",
            parse_mode=enums.ParseMode.MARKDOWN,
        )

    uid   = message.from_user.id
    cid   = args[1].strip()

    # Verify the user owns this character
    char = await _col("user_characters").find_one(
        {"user_id": uid, "$or": [{"instance_id": cid}, {"char_id": cid}]}
    )
    if not char:
        return await message.reply_text("❌ Character not found in your collection.")

    user_doc = await _col("users").find_one({"user_id": uid}) or {}
    favs = list(user_doc.get("favorites") or [])

    char_cid = char.get("char_id") or char.get("id") or cid

    if char_cid in favs:
        # Toggle off
        favs.remove(char_cid)
        await _col("users").update_one({"user_id": uid}, {"$set": {"favorites": favs}}, upsert=True)
        return await message.reply_text(
            f"💔 **{escape(char.get('name', 'Unknown'))}** removed from favourites."
        )

    if len(favs) >= FAV_MAX:
        return await message.reply_text(
            f"⭐ You already have **{FAV_MAX}** favourites.\n"
            "Remove one first with `/setfav <id>` (same command toggles off).",
            parse_mode=enums.ParseMode.MARKDOWN,
        )

    favs.insert(0, char_cid)  # first fav = cover
    await _col("users").update_one({"user_id": uid}, {"$set": {"favorites": favs}}, upsert=True)

    tier  = get_rarity(char.get("rarity") or "common")
    emoji = tier.emoji if tier else "❓"
    await message.reply_text(
        f"⭐ **{emoji} {escape(char.get('name', 'Unknown'))}** added to favourites!\n"
        f"_(Now your harem cover)_",
        parse_mode=enums.ParseMode.MARKDOWN,
    )


# ─────────────────────────────────────────────────────────────────────────────
# /fav — View favourites list
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("fav"))
async def cmd_fav(_, message: Message):
    uid      = message.from_user.id
    user_doc = await _col("users").find_one({"user_id": uid})
    if not user_doc:
        return await message.reply_text("❌ No data found. Use /harem first.")

    favs = list(user_doc.get("favorites") or [])
    if not favs:
        return await message.reply_text(
            "💔 No favourites yet.\nUse `/setfav <id>` to set one!",
            parse_mode=enums.ParseMode.MARKDOWN,
        )

    lines = [f"⭐ **{escape(message.from_user.first_name)}'s Favourites**\n"]
    for i, fav_id in enumerate(favs, 1):
        char = await _col("user_characters").find_one(
            {"user_id": uid, "$or": [{"char_id": fav_id}, {"instance_id": fav_id}]}
        )
        if char:
            tier  = get_rarity(char.get("rarity") or "common")
            emoji = tier.emoji if tier else "❓"
            cover_tag = "  _(cover)_" if i == 1 else ""
            lines.append(
                f"  {i}. {emoji} **{escape(char.get('name','Unknown'))}**"
                f"  `{fav_id}`{cover_tag}"
            )
        else:
            lines.append(f"  {i}. ❓ `{fav_id}` _(not found)_")

    lines.append(f"\n_Use `/setfav <id>` to add/remove._")
    await message.reply_text("\n".join(lines), parse_mode=enums.ParseMode.MARKDOWN)


# Callback: quick fav list from inside harem
@app.on_callback_query(filters.regex(r"^fav_action:"))
async def fav_action_cb(client, cb):
    try:
        _, action, uid_s = cb.data.split(":", 2)
        uid = int(uid_s)
    except Exception:
        return await cb.answer("Invalid.", show_alert=True)

    if cb.from_user.id != uid:
        return await cb.answer("Not your harem!", show_alert=True)

    await cb.answer()

    if action == "list":
        user_doc = await _col("users").find_one({"user_id": uid}) or {}
        favs     = list(user_doc.get("favorites") or [])
        if not favs:
            return await cb.answer("💔 No favourites yet. Use /setfav <id>", show_alert=True)

        lines = ["⭐ **Your Favourites**\n"]
        for i, fid in enumerate(favs, 1):
            char = await _col("user_characters").find_one(
                {"user_id": uid, "$or": [{"char_id": fid}, {"instance_id": fid}]}
            )
            if char:
                tier  = get_rarity(char.get("rarity") or "common")
                emoji = tier.emoji if tier else "❓"
                lines.append(f"  {i}. {emoji} {escape(char.get('name','Unknown'))}  `{fid}`")
            else:
                lines.append(f"  {i}. ❓ `{fid}`")

        lines.append("\n_/setfav <id> to manage_")
        try:
            await cb.message.reply_text(
                "\n".join(lines),
                parse_mode=enums.ParseMode.MARKDOWN,
            )
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# /hstats — Harem rarity stats
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("hstats"))
async def cmd_hstats(_, message: Message):
    uid = message.from_user.id
    await _show_hstats(message, uid, is_cb=False)


@app.on_callback_query(filters.regex(r"^hstats_cb:"))
async def hstats_cb_handler(client, cb):
    try:
        uid = int(cb.data.split(":")[1])
    except Exception:
        return await cb.answer("Invalid.", show_alert=True)

    if cb.from_user.id != uid:
        return await cb.answer("Not your harem!", show_alert=True)

    await cb.answer()
    await _show_hstats(cb.message, uid, is_cb=True, callback_query=cb)


async def _show_hstats(source, user_id: int, is_cb: bool = False, callback_query=None):
    all_chars = await _col("user_characters").find({"user_id": user_id}).to_list(None)
    if not all_chars:
        msg = "📊 No characters yet! Use /harem after claiming some."
        if is_cb:
            try:   await source.reply_text(msg)
            except Exception: pass
        else:
            await source.reply_text(msg)
        return

    total = len(all_chars)
    tally: dict[str, int] = {}
    for c in all_chars:
        r = (c.get("rarity") or "unknown").title()
        tally[r] = tally.get(r, 0) + 1

    # Sort by rarity order
    sorted_tally = sorted(
        tally.items(),
        key=lambda kv: RARITY_ORDER.get(kv[0], 99),
    )

    BAR_LEN = 16
    lines = [
        f"📊 **Harem Stats** — {total} characters total\n"
        "──────────────────────────"
    ]
    for rarity, count in sorted_tally:
        tier  = get_rarity(rarity)
        emoji = tier.emoji if tier else "❓"
        pct   = count / total
        filled = round(pct * BAR_LEN)
        bar    = "█" * filled + "░" * (BAR_LEN - filled)
        lines.append(
            f"{emoji} **{rarity:<12}** `{bar}` {count:>3}  ({pct*100:.1f}%)"
        )

    lines.append("──────────────────────────")

    # Unique anime count
    anime_set = {c.get("anime") or "Unknown" for c in all_chars}
    lines.append(f"🎴 **Unique anime:** `{len(anime_set)}`")

    text = "\n".join(lines)
    back_markup = IKM([[IKB("🔙 Back to Harem", callback_data=f"harem:0:{user_id}:None")]])

    if is_cb and callback_query:
        try:
            await callback_query.message.edit_text(
                text, reply_markup=back_markup, parse_mode=enums.ParseMode.MARKDOWN
            )
        except Exception:
            await source.reply_text(text, parse_mode=enums.ParseMode.MARKDOWN)
    else:
        await source.reply_text(text, parse_mode=enums.ParseMode.MARKDOWN)


# ─────────────────────────────────────────────────────────────────────────────
# /gallery — Browse ALL characters uploaded to the bot
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("gallery"))
async def cmd_gallery(client, message: Message):
    uid = message.from_user.id

    group_ok, channel_ok = await check_membership(uid, client)
    if not (group_ok and channel_ok):
        text, markup = _join_card(uid, message.from_user.first_name or "there", group_ok, channel_ok, "gallery")
        return await message.reply_text(text, reply_markup=markup)

    args = message.command
    # Optional rarity filter: /gallery <rarity>
    rarity_filter = args[1].strip().lower() if len(args) > 1 else None

    await _display_gallery(client, message, page=0, rarity_filter=rarity_filter, is_initial=True)


@app.on_callback_query(filters.regex(r"^gallery:"))
async def gallery_cb(client, cb):
    try:
        parts  = cb.data.split(":")           # gallery:page:uid:rarity
        page   = int(parts[1])
        uid    = int(parts[2])
        r_str  = parts[3] if len(parts) > 3 else "None"
        rarity_filter = None if r_str == "None" else r_str
    except Exception:
        return await cb.answer("Invalid.", show_alert=True)

    if cb.from_user.id != uid:
        return await cb.answer("Not your session!", show_alert=True)

    await cb.answer()
    await _display_gallery(client, cb.message, page, rarity_filter, is_initial=False, callback_query=cb)


async def _display_gallery(
    client,
    source,
    page: int,
    rarity_filter: str | None = None,
    is_initial: bool = False,
    callback_query=None,
):
    query: dict = {}
    if rarity_filter:
        query["rarity"] = {"$regex": f"^{rarity_filter}$", "$options": "i"}

    user_id = (
        callback_query.from_user.id if callback_query else source.from_user.id
    )

    try:
        total_chars  = await _col("characters").count_documents(query)
        total_pages  = max(1, math.ceil(total_chars / GALLERY_PER_PAGE))
        page         = max(0, min(page, total_pages - 1))

        char_list = await _col("characters").find(query) \
            .skip(page * GALLERY_PER_PAGE) \
            .limit(GALLERY_PER_PAGE) \
            .to_list(None)

    except Exception:
        log.exception("_display_gallery error")
        err = "❌ Error fetching gallery. Try again."
        if callback_query:
            try:   await callback_query.message.edit_text(err)
            except Exception: pass
        else:
            await source.reply_text(err)
        return

    if not char_list:
        msg = "🖼️ No characters found" + (f" for **{rarity_filter}**" if rarity_filter else "") + "."
        if callback_query:
            try:   await callback_query.message.edit_text(msg)
            except Exception: pass
        else:
            await source.reply_text(msg)
        return

    rf_str = rarity_filter or "None"

    lines = [f"🖼️ **Global Gallery** — Page {page + 1}/{total_pages}"]
    if rarity_filter:
        tier  = get_rarity(rarity_filter)
        emoji = tier.emoji if tier else "❓"
        lines.append(f"<i>Filter: {emoji} {rarity_filter.title()}</i>")
    lines.append(f"<i>Total: {total_chars} characters</i>\n")

    for i, char in enumerate(char_list, start=page * GALLERY_PER_PAGE + 1):
        tier  = get_rarity(char.get("rarity") or "common")
        emoji = tier.emoji if tier else "❓"
        cid   = char.get("id") or char.get("char_id") or "?"
        lines.append(
            f"  {i}. {emoji} <code>{cid}</code>  "
            f"<b>{escape(char.get('name','Unknown'))}</b>"
            f"  <i>{escape(char.get('anime','?'))}</i>"
        )

    text = "\n".join(lines)

    def _gnb(label, target):
        if 0 <= target < total_pages:
            return IKB(label, callback_data=f"gallery:{target}:{user_id}:{rf_str}")
        return IKB("·", callback_data="noop")

    # Pick a random cover from this page
    cover_char = random.choice(char_list) if char_list else None
    cover_img  = cover_char.get("img_url") if cover_char else None

    markup = IKM([
        [_gnb("⬅️ Prev", page - 1),
         IKB(f"📖 {page + 1}/{total_pages}", callback_data="noop"),
         _gnb("Next ➡️", page + 1)],
        [_gnb("⏪ ×5", page - 5), _gnb("×5 ⏩", page + 5)],
        [IKB("🔎 Filter Rarity", callback_data=f"gallery_filter:{user_id}:{rf_str}")],
    ])

    if is_initial:
        try:
            if cover_img:
                await source.reply_photo(cover_img, caption=text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
                return
        except Exception:
            pass
        await source.reply_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
    else:
        try:
            if cover_img:
                await callback_query.message.edit_media(
                    InputMediaPhoto(cover_img, caption=text, parse_mode=enums.ParseMode.HTML),
                    reply_markup=markup,
                )
                return
        except Exception:
            pass
        try:
            await callback_query.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
        except Exception:
            pass


# Gallery rarity filter callback
@app.on_callback_query(filters.regex(r"^gallery_filter:"))
async def gallery_filter_cb(client, cb):
    try:
        _, uid_s, current = cb.data.split(":", 2)
        uid = int(uid_s)
    except Exception:
        return await cb.answer("Invalid.", show_alert=True)

    if cb.from_user.id != uid:
        return await cb.answer("Not your session!", show_alert=True)

    all_tiers = {**RARITIES, **SUB_RARITIES}
    keyboard, row = [], []
    for i, tier in enumerate(all_tiers.values(), 1):
        row.append(IKB(f"{tier.emoji} {tier.display_name}", callback_data=f"gallery:0:{uid}:{tier.name}"))
        if i % 3 == 0:
            keyboard.append(row); row = []
    if row:
        keyboard.append(row)
    keyboard.append([IKB("✖️ All Rarities", callback_data=f"gallery:0:{uid}:None")])

    await cb.answer()
    try:
        await cb.message.edit_text("🔎 **Filter gallery by rarity:**", reply_markup=IKM(keyboard))
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# /cmode  /collectionmode
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command(["cmode", "collectionmode"]) & filters.group)
async def cmd_cmode(client, message: Message):
    uid = message.from_user.id

    if uid in temp_block and temp_block[uid] > time.time():
        return

    group_ok, channel_ok = await check_membership(uid, client)
    if not (group_ok and channel_ok):
        text, markup = _join_card(uid, message.from_user.first_name or "there", group_ok, channel_ok, "cmode")
        return await message.reply_text(text, reply_markup=markup)

    user_doc     = await _col("users").find_one({"user_id": uid}) or {}
    current_mode = user_doc.get("collection_mode", "All")
    full_name    = message.from_user.first_name or ""
    if getattr(message.from_user, "last_name", None):
        full_name += " " + message.from_user.last_name

    caption = (
        f"**🗂️ Collection Mode — {escape(full_name)}**\n\n"
        f"Current: `{current_mode}`\n\n"
        "Choose how your harem is displayed:"
    )
    await message.reply_text(caption, reply_markup=IKM(_cmode_main_buttons(uid)))


def _cmode_main_buttons(user_id: int):
    return [
        [
            IKB("🌀 Rarity",       callback_data=f"cmode_main:rarity:{user_id}"),
            IKB("📋 All (Default)", callback_data=f"cmode_main:all:{user_id}"),
        ],
        [
            IKB("🎴 By Anime",      callback_data=f"cmode_main:anime:{user_id}"),
            IKB("👤 By Character",  callback_data=f"cmode_main:character:{user_id}"),
        ],
        [IKB("✖️ Cancel", callback_data=f"cmode_main:cancel:{user_id}")],
    ]


@app.on_callback_query(filters.regex(r"^cmode_main:"))
async def cmode_main_cb(client, cb):
    try:
        _, mode, uid_s = cb.data.split(":")
        uid = int(uid_s)
    except Exception:
        return await cb.answer("Invalid.", show_alert=True)

    if cb.from_user.id != uid:
        return await cb.answer("⚠️ Not your collection!", show_alert=True)

    full_name = escape(cb.from_user.first_name or "")
    if getattr(cb.from_user, "last_name", None):
        full_name += f" {escape(cb.from_user.last_name)}"

    if mode == "cancel":
        await cb.answer()
        try:   await cb.message.delete()
        except Exception: pass

    elif mode == "rarity":
        tiers = list({**RARITIES, **SUB_RARITIES}.values())
        rows, row = [], []
        for i, tier in enumerate(tiers, 1):
            row.append(IKB(f"{tier.emoji} {tier.display_name}", callback_data=f"cmode:{tier.name}:{uid}"))
            if i % 3 == 0:
                rows.append(row); row = []
        if row: rows.append(row)
        rows.append([IKB("🔙 Back", callback_data=f"cmode_main:back:{uid}")])
        await cb.answer()
        try:
            await cb.message.edit_text(f"**{full_name}, select rarity mode:**", reply_markup=IKM(rows))
        except Exception: pass

    elif mode in ("all", "anime", "character"):
        mode_map = {
            "all":       ("All",               "📋 All items"),
            "anime":     ("Anime Sorted",       "🎴 Anime A-Z"),
            "character": ("Characters Sorted",  "👤 Character A-Z"),
        }
        db_val, label = mode_map[mode]
        await _col("users").update_one({"user_id": uid}, {"$set": {"collection_mode": db_val}}, upsert=True)
        await cb.answer(f"✅ Mode: {label}")
        try:
            await cb.message.edit_text(
                f"**{full_name}**, collection mode updated!\n▸ Now showing: **{label}**",
                reply_markup=IKM([[IKB("🔙 Back", callback_data=f"cmode_main:back:{uid}")]]),
            )
        except Exception: pass

    elif mode == "back":
        await cb.answer()
        user_doc     = await _col("users").find_one({"user_id": uid}) or {}
        current_mode = user_doc.get("collection_mode", "All")
        try:
            await cb.message.edit_text(
                f"**🗂️ Collection Mode — {full_name}**\n\nCurrent: `{current_mode}`\n\nChoose a mode:",
                reply_markup=IKM(_cmode_main_buttons(uid)),
            )
        except Exception: pass


@app.on_callback_query(filters.regex(r"^cmode:"))
async def cmode_cb(client, cb):
    try:
        _, rarity_name, uid_s = cb.data.split(":")
        uid = int(uid_s)
    except Exception:
        return await cb.answer("Invalid.", show_alert=True)

    if cb.from_user.id != uid:
        return await cb.answer("⚠️ Not your collection!", show_alert=True)

    tier = get_rarity(rarity_name)
    if not tier:
        return await cb.answer("⚠️ Invalid rarity!", show_alert=True)

    display = f"{tier.emoji} {tier.display_name}"
    await _col("users").update_one({"user_id": uid}, {"$set": {"collection_mode": rarity_name}}, upsert=True)

    full_name = escape(cb.from_user.first_name or "")
    await cb.answer(f"✅ Mode: {display}")
    try:
        await cb.message.edit_text(
            f"**{full_name}**, collection mode updated!\n▸ Now showing: **{display}**",
            reply_markup=IKM([[IKB("🔙 Back", callback_data=f"cmode_main:back:{uid}")]]),
        )
    except Exception: pass


# ─────────────────────────────────────────────────────────────────────────────
# /sort
# ─────────────────────────────────────────────────────────────────────────────

SORT_OPTIONS = ["rarity", "name", "anime", "recent"]

@app.on_message(filters.command("sort"))
async def cmd_sort(_, message: Message):
    args = message.command
    if len(args) < 2 or args[1].lower() not in SORT_OPTIONS:
        opts = " | ".join(SORT_OPTIONS)
        return await message.reply_text(
            f"🔃 **Sort Harem**\nUsage: `/sort <{opts}>`",
            parse_mode=enums.ParseMode.MARKDOWN,
        )
    val = args[1].lower()
    await update_user(message.from_user.id, {"$set": {"harem_sort": val}})
    await message.reply_text(f"✅ Harem sorted by **{val}**!")


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
        return await cb.answer("⛔ Not your button.", show_alert=True)

    await cb.answer("🔄 Verifying...", show_alert=False)

    try:
        group_ok, channel_ok = await check_membership(uid, client)
    except Exception as exc:
        log.warning("joined_check error uid=%d: %s", uid, exc)
        return await cb.answer("⚠️ Verification failed. Try again.", show_alert=True)

    name = cb.from_user.first_name or "there"

    if not (group_ok and channel_ok):
        text, markup = _join_card(uid, name, group_ok, channel_ok, context)
        try:   await cb.message.edit_text(text, reply_markup=markup)
        except Exception: pass
        return

    # All joined — route to the right feature
    if context == "harem":
        try:
            await cb.message.edit_text(f"✅ Verified, **{escape(name)}**! Loading harem…")
        except Exception: pass
        await display_harem(client, cb.message, uid, 0, None, is_initial=False, callback_query=cb)

    elif context == "gallery":
        try:
            await cb.message.edit_text(f"✅ Verified! Loading gallery…")
        except Exception: pass
        await _display_gallery(client, cb.message, 0, None, is_initial=False, callback_query=cb)

    elif context == "cmode":
        user_doc     = await _col("users").find_one({"user_id": uid}) or {}
        current_mode = user_doc.get("collection_mode", "All")
        try:
            await cb.message.edit_text(
                f"**🗂️ Collection Mode — {escape(name)}**\n\nCurrent: `{current_mode}`\n\nChoose a mode:",
                reply_markup=IKM(_cmode_main_buttons(uid)),
            )
        except Exception: pass

    else:
        try:
            await cb.message.edit_text(
                f"✅ **All set, {escape(name)}!**\nUse the command again to continue.",
            )
        except Exception: pass
