"""
SoulCatcher/modules/harem.py
════════════════════════════════════════════════════════════════════
Commands  : /harem  /sort  /setfav  /gallery
Callbacks : harem:  filter:  apply_filter:
            joined_check:  gallery:  gallery_filter:  setfav_hint:
════════════════════════════════════════════════════════════════════

Rarity keys matched to rarity.py
  Main  : common · rare · cosmos · infernal · seasonal · mythic · eternal
  Sub   : festival · limited_edition · sports · fantasy · cartoon (Verse)

DB collections used (database.py)
  users            – harem_sort, collection_mode, favorites
  user_characters  – instance_id, char_id, name, anime, rarity,
                     img_url, video_url, obtained_at, is_favorite
  characters       – master catalogue  (for /gallery)
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
from ..database import get_or_create_user, update_user, _col

log = logging.getLogger("SoulCatcher.harem")

# ── Constants ─────────────────────────────────────────────────────────────────
CHARS_PER_PAGE   = 15
GALLERY_PER_PAGE = 12
FAV_MAX          = 10
RATE_COOLDOWN    = 3          # seconds between page-flip callbacks

# ── Rarity ordering: id-based so common(1) comes before eternal(7)
#    Main  tiers: id * 100  → 100, 200, 300 … 700
#    Sub   tiers: id * 10   → 510, 610, 611, 612, 710  (sorts within parent)
def _build_rarity_order() -> dict[str, int]:
    out: dict[str, int] = {}
    for r in RARITIES.values():
        out[r.name] = r.id * 100
    for s in SUB_RARITIES.values():
        out[s.name] = s.id * 10
    return out

RARITY_ORDER: dict[str, int] = _build_rarity_order()

# per-user rate-limit store
_rate: dict[int, float] = {}


# ══════════════════════════════════════════════════════════════════════════════
# Membership gate helpers
# ══════════════════════════════════════════════════════════════════════════════

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
    except (ChatAdminRequired, ChannelPrivate, PeerIdInvalid, UsernameNotOccupied) as e:
        log.warning("_check_one(%s): %s — failing open", chat_id, e)
        return True
    except Exception as e:
        log.warning("_check_one unexpected (%s, %d): %s", chat_id, user_id, e)
        return True


async def check_membership(user_id: int, client) -> tuple[bool, bool]:
    """Run both checks in parallel; returns (group_ok, channel_ok)."""
    g, c = await asyncio.gather(
        _check_one(client, SUPPORT_GROUP,  user_id),
        _check_one(client, UPDATE_CHANNEL, user_id),
    )
    return g, c


def _join_card(
    user_id: int,
    name: str,
    group_ok: bool,
    channel_ok: bool,
    context: str,
) -> tuple[str, IKM]:
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
        "Join below, then tap **🔄 Verify**."
    )

    rows = []
    if not group_ok:
        rows.append([IKB("🫂 Join Support Group",  url=f"https://t.me/{SUPPORT_GROUP}")])
    else:
        rows.append([IKB("✅ Support Group — Joined", callback_data="noop")])

    if not channel_ok:
        rows.append([IKB("📢 Join Updates Channel", url=f"https://t.me/{UPDATE_CHANNEL}")])
    else:
        rows.append([IKB("✅ Updates Channel — Joined", callback_data="noop")])

    rows.append([IKB("🔄 Verify Membership", callback_data=f"joined_check:{user_id}:{context}")])
    return text, IKM(rows)


# ══════════════════════════════════════════════════════════════════════════════
# Data helpers
# ══════════════════════════════════════════════════════════════════════════════

async def _fetch_user_characters(user_id: int):
    """
    Returns (unique_chars, counts, user_doc).

    unique_chars  list[dict]      – one entry per char_id, sorted per prefs
    counts        dict[str, int]  – {char_id: total owned}
    user_doc      dict | None

    Returns (None, None, None)  when user not found in DB.
    Returns ([], {}, user_doc)  when harem is empty.
    """
    user_doc = await _col("users").find_one({"user_id": user_id})
    if not user_doc:
        return None, None, None

    all_inst = await _col("user_characters").find({"user_id": user_id}).to_list(None)
    if not all_inst:
        return [], {}, user_doc

    cmode      = (user_doc.get("collection_mode") or "all").lower().strip()
    sort_field = (user_doc.get("harem_sort")       or "rarity").lower().strip()

    # ── collection_mode: filter or pre-sort ───────────────────────────────────
    if cmode in ("anime", "anime sorted"):
        filtered = sorted(all_inst, key=lambda x: x.get("anime", "").lower())
    elif cmode in ("characters", "characters sorted"):
        filtered = sorted(all_inst, key=lambda x: x.get("name", "").lower())
    elif cmode == "all":
        filtered = all_inst
    else:
        # rarity-name mode (e.g. "common", "cosmos", "cartoon" …)
        filtered = [
            c for c in all_inst
            if (c.get("rarity") or "").lower() == cmode
        ]

    if not filtered:
        return [], {}, user_doc

    # ── harem_sort (only when cmode didn't impose its own order) ──────────────
    if cmode == "all":
        if sort_field == "name":
            filtered = sorted(filtered, key=lambda x: x.get("name", "").lower())
        elif sort_field == "anime":
            filtered = sorted(filtered, key=lambda x: x.get("anime", "").lower())
        elif sort_field == "recent":
            filtered = sorted(
                filtered,
                key=lambda x: x.get("obtained_at") or "",
                reverse=True,
            )
        else:  # "rarity" default — common(100) first … eternal(700) last
            filtered = sorted(
                filtered,
                key=lambda x: RARITY_ORDER.get(x.get("rarity", ""), 9999),
            )

    # ── Deduplicate by char_id, count duplicates ──────────────────────────────
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
    """First favourited character becomes cover; otherwise a random pick."""
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


# ══════════════════════════════════════════════════════════════════════════════
# Text / keyboard builders
# ══════════════════════════════════════════════════════════════════════════════

async def _build_harem_text(
    unique_chars: list,
    counts: dict,
    user_doc: dict,
    page: int,
    total_pages: int,
    user_name: str,
    filter_rarity: str | None,
) -> str:
    start  = page * CHARS_PER_PAGE
    sliced = unique_chars[start: start + CHARS_PER_PAGE]
    favs   = {str(f) for f in (user_doc.get("favorites") or [])}

    lines = [f"<b>❤️ {escape(user_name)}'s Harem</b>  〔{page + 1}/{total_pages}〕"]
    if filter_rarity:
        tier  = get_rarity(filter_rarity)
        emoji = tier.emoji if tier else "❓"
        lines.append(f"<i>Filter: {emoji} {filter_rarity.title()}</i>")
    lines.append("")

    # Group this slice by anime
    grouped: dict[str, list] = {}
    for c in sliced:
        grouped.setdefault(c.get("anime") or "Unknown", []).append(c)

    for anime, chars in grouped.items():
        try:
            total_in_db = await _col("characters").count_documents({"anime": anime})
        except Exception:
            total_in_db = "?"
        lines.append(
            f"<b>🎴 {escape(anime)}</b>  "
            f"<code>{len(chars)}/{total_in_db}</code>"
        )
        for char in chars:
            cid        = char.get("char_id") or char.get("id") or ""
            count      = counts.get(cid, 1)
            tier       = get_rarity(char.get("rarity") or "common")
            r_emoji    = tier.emoji if tier else "❓"
            fav_tag    = "⭐" if str(cid) in favs else ""
            dup_tag    = f" ×{count}" if count > 1 else ""
            display_id = char.get("instance_id") or cid
            lines.append(
                f"  {r_emoji}{fav_tag} <code>{display_id}</code>"
                f"  {escape(char.get('name', 'Unknown'))}{dup_tag}"
            )
        lines.append("")

    lines.append(
        f"<b>Unique:</b> <code>{len(unique_chars)}</code>  •  "
        f"<b>Page:</b> <code>{page + 1}/{total_pages}</code>"
    )
    return "\n".join(lines)


def _build_nav_markup(
    user_id: int,
    page: int,
    total_pages: int,
    filter_rarity: str | None,
    total_chars: int,
) -> IKM:
    fr = filter_rarity or "None"

    def nb(label: str, target: int) -> IKB:
        if 0 <= target < total_pages:
            return IKB(label, callback_data=f"harem:{target}:{user_id}:{fr}")
        return IKB("·", callback_data="noop")

    return IKM([
        [nb("⬅️", page - 1),
         IKB(f"📖 {page + 1}/{total_pages}", callback_data="noop"),
         nb("➡️", page + 1)],
        [nb("⏪ ×2", page - 2), nb("×2 ⏩", page + 2)],
        [
            IKB(
                f"🎴 Collection ({total_chars})",
                switch_inline_query_current_chat=f"collection.{user_id}.",
            ),
            IKB(
                "🎠 Verse of",
                switch_inline_query_current_chat=f"collection.{user_id}.VerseSeries",
            ),
        ],
        [
            IKB("🔎 Filter",  callback_data=f"filter:{user_id}"),
            IKB("⭐ Set Fav", callback_data=f"setfav_hint:{user_id}"),
        ],
    ])


# ══════════════════════════════════════════════════════════════════════════════
# Core display engine
# ══════════════════════════════════════════════════════════════════════════════

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

        # Optional rarity filter on top of collection_mode
        if filter_rarity and unique_chars:
            unique_chars = [
                c for c in unique_chars if c.get("rarity") == filter_rarity
            ]
            counts = {
                (c.get("char_id") or c.get("id") or ""): counts.get(
                    c.get("char_id") or c.get("id") or "", 1
                )
                for c in unique_chars
            }

        if not unique_chars:
            empty_msg = (
                f"❌ No <b>{escape(filter_rarity)}</b> characters in your collection!"
                if filter_rarity
                else (
                    "🌸 Your harem is empty!\n"
                    f"<i>Collection mode: {escape(cmode)}</i>\n"
                    "Claim characters when they spawn by pressing ❤️"
                )
            )
            if callback_query:
                try:
                    await callback_query.message.edit_text(
                        empty_msg, parse_mode=enums.ParseMode.HTML
                    )
                except Exception:
                    pass
            else:
                await source.reply_text(empty_msg, parse_mode=enums.ParseMode.HTML)
            return

        total_pages = max(1, math.ceil(len(unique_chars) / CHARS_PER_PAGE))
        page        = max(0, min(page, total_pages - 1))

        user_name = (
            callback_query.from_user.first_name
            if callback_query else source.from_user.first_name
        )

        text   = await _build_harem_text(
            unique_chars, counts, user_doc,
            page, total_pages, user_name, filter_rarity,
        )
        markup = _build_nav_markup(
            user_id, page, total_pages, filter_rarity, len(unique_chars)
        )
        cover  = await _best_cover(user_doc, unique_chars)

        if is_initial:
            await _send_new(source, cover, text, markup)
        else:
            await _edit_existing(callback_query, cover, text, markup)

    except Exception:
        log.exception("display_harem error user=%d", user_id)
        err = "❌ Something went wrong. Please try again."
        if callback_query:
            try:   await callback_query.message.edit_text(err)
            except Exception: pass
        else:
            await source.reply_text(err)


async def _send_new(source: Message, cover: dict | None, text: str, markup: IKM):
    if cover:
        vid = cover.get("video_url") or (
            cover["img_url"]
            if str(cover.get("img_url", "")).endswith((".mp4", ".gif"))
            else None
        )
        try:
            if vid:
                await source.reply_video(
                    vid, caption=text, reply_markup=markup,
                    parse_mode=enums.ParseMode.HTML,
                )
                return
            if cover.get("img_url"):
                await source.reply_photo(
                    cover["img_url"], caption=text, reply_markup=markup,
                    parse_mode=enums.ParseMode.HTML,
                )
                return
        except Exception:
            pass
    await source.reply_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)


async def _edit_existing(cb, cover: dict | None, text: str, markup: IKM):
    if cover:
        vid = cover.get("video_url") or (
            cover["img_url"]
            if str(cover.get("img_url", "")).endswith((".mp4", ".gif"))
            else None
        )
        try:
            if vid:
                await cb.message.edit_media(
                    InputMediaVideo(
                        vid, caption=text, parse_mode=enums.ParseMode.HTML
                    ),
                    reply_markup=markup,
                )
                return
            if cover.get("img_url"):
                await cb.message.edit_media(
                    InputMediaPhoto(
                        cover["img_url"], caption=text,
                        parse_mode=enums.ParseMode.HTML,
                    ),
                    reply_markup=markup,
                )
                return
        except Exception:
            pass
    try:
        await cb.message.edit_text(
            text, reply_markup=markup, parse_mode=enums.ParseMode.HTML
        )
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# /harem
# ══════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("harem"))
async def cmd_harem(client, message: Message):
    uid = message.from_user.id

    if uid in _rate and _rate[uid] > time.time():
        return await message.reply_text("⏳ Slow down! Try again in a moment.")

    await get_or_create_user(
        uid,
        message.from_user.username   or "",
        message.from_user.first_name or "",
        getattr(message.from_user, "last_name", "") or "",
    )

    group_ok, channel_ok = await check_membership(uid, client)
    if not (group_ok and channel_ok):
        text, markup = _join_card(
            uid,
            message.from_user.first_name or "there",
            group_ok, channel_ok, "harem",
        )
        return await message.reply_text(text, reply_markup=markup)

    await display_harem(client, message, uid, page=0, is_initial=True)


@app.on_callback_query(filters.regex(r"^harem:"))
async def harem_cb(client, cb):
    data = cb.data

    # Legacy close button: "harem:close_<uid>"
    if "close" in data:
        parts = data.split("_")
        if len(parts) == 2 and cb.from_user.id == int(parts[1]):
            await cb.answer()
            try:   await cb.message.delete()
            except Exception: pass
        else:
            await cb.answer("This is not your Harem!", show_alert=True)
        return

    try:
        _, page_s, uid_s, fr_s = data.split(":")
        page          = int(page_s)
        uid           = int(uid_s)
        filter_rarity = None if fr_s == "None" else fr_s
    except ValueError:
        return await cb.answer("Invalid data.", show_alert=True)

    if cb.from_user.id != uid:
        return await cb.answer("It's not your Harem!", show_alert=True)

    now = time.time()
    if uid in _rate and _rate[uid] > now:
        return await cb.answer("⏳ Too fast!", show_alert=False)
    _rate[uid] = now + RATE_COOLDOWN

    group_ok, channel_ok = await check_membership(uid, client)
    if not (group_ok and channel_ok):
        return await cb.answer(
            "Please join our group & updates channel first!", show_alert=True
        )

    await cb.answer()
    await display_harem(
        client, cb.message, uid, page,
        filter_rarity, is_initial=False, callback_query=cb,
    )


# "⭐ Set Fav" button in harem nav just shows usage hint
@app.on_callback_query(filters.regex(r"^setfav_hint:"))
async def setfav_hint_cb(_, cb):
    try:
        uid = int(cb.data.split(":")[1])
    except Exception:
        return await cb.answer("Invalid.", show_alert=True)

    if cb.from_user.id != uid:
        return await cb.answer("Not your harem!", show_alert=True)

    await cb.answer(
        "Use /setfav <character_id> to set a favourite cover!",
        show_alert=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Rarity filter  (inside harem)
# ══════════════════════════════════════════════════════════════════════════════

@app.on_callback_query(filters.regex(r"^filter:"))
async def filter_cb(_, cb):
    try:
        uid = int(cb.data.split(":")[1])
    except (IndexError, ValueError):
        return await cb.answer("Invalid.", show_alert=True)

    if cb.from_user.id != uid:
        return await cb.answer("It's not your Harem!", show_alert=True)

    all_tiers = {**RARITIES, **SUB_RARITIES}
    keyboard, row = [], []
    for i, tier in enumerate(
        sorted(all_tiers.values(), key=lambda t: RARITY_ORDER.get(t.name, 9999)),
        start=1,
    ):
        row.append(
            IKB(
                f"{tier.emoji} {tier.display_name}",
                callback_data=f"apply_filter:{uid}:{tier.name}",
            )
        )
        if i % 3 == 0:
            keyboard.append(row); row = []
    if row:
        keyboard.append(row)

    keyboard.append([IKB("✖️ Clear Filter", callback_data=f"apply_filter:{uid}:None")])
    keyboard.append([IKB("🔙 Back", callback_data=f"harem:0:{uid}:None")])

    await cb.answer()
    try:
        await cb.message.edit_text(
            "🔎 <b>Filter by Rarity:</b>",
            reply_markup=IKM(keyboard),
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception:
        pass


@app.on_callback_query(filters.regex(r"^apply_filter:"))
async def apply_filter_cb(client, cb):
    try:
        _, uid_s, fr_s = cb.data.split(":", 2)
        uid           = int(uid_s)
        filter_rarity = None if fr_s == "None" else fr_s
    except Exception:
        return await cb.answer("Invalid.", show_alert=True)

    if cb.from_user.id != uid:
        return await cb.answer("It's not your Harem!", show_alert=True)

    await cb.answer()
    await display_harem(
        client, cb.message, uid, 0,
        filter_rarity, is_initial=False, callback_query=cb,
    )


# ══════════════════════════════════════════════════════════════════════════════
# /setfav  —  toggle a favourite / harem cover
# Usage: /setfav <instance_id or char_id>
# ══════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("setfav"))
async def cmd_setfav(_, message: Message):
    args = message.command
    if len(args) < 2:
        return await message.reply_text(
            "⭐ **Set Favourite**\n"
            "Usage: `/setfav <character_id>`\n\n"
            "• The first favourite becomes your harem cover.\n"
            f"• Up to **{FAV_MAX}** favourites allowed.\n"
            "• Running the command again on the same ID removes it.",
            parse_mode=enums.ParseMode.MARKDOWN,
        )

    uid = message.from_user.id
    cid = args[1].strip()

    # Verify ownership — accept both instance_id and char_id
    char = await _col("user_characters").find_one(
        {
            "user_id": uid,
            "$or": [{"instance_id": cid}, {"char_id": cid}],
        }
    )
    if not char:
        return await message.reply_text(
            "❌ Character not found in your collection.\n"
            "Check the ID with /harem."
        )

    user_doc  = await _col("users").find_one({"user_id": uid}) or {}
    favs      = list(user_doc.get("favorites") or [])
    char_cid  = char.get("char_id") or char.get("id") or cid
    char_name = char.get("name", "Unknown")
    tier      = get_rarity(char.get("rarity") or "common")
    r_emoji   = tier.emoji if tier else "❓"

    # Toggle off if already a favourite
    if char_cid in favs:
        favs.remove(char_cid)
        await _col("users").update_one(
            {"user_id": uid}, {"$set": {"favorites": favs}}, upsert=True
        )
        return await message.reply_text(
            f"💔 **{r_emoji} {escape(char_name)}** removed from favourites."
        )

    if len(favs) >= FAV_MAX:
        return await message.reply_text(
            f"⭐ You already have **{FAV_MAX}** favourites.\n"
            "Remove one first with `/setfav <id>`.",
            parse_mode=enums.ParseMode.MARKDOWN,
        )

    favs.insert(0, char_cid)      # first item = cover character
    await _col("users").update_one(
        {"user_id": uid}, {"$set": {"favorites": favs}}, upsert=True
    )
    cover_note = "  _— now your harem cover!_" if len(favs) == 1 else ""
    await message.reply_text(
        f"⭐ **{r_emoji} {escape(char_name)}** added to favourites!{cover_note}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# /gallery  —  browse all characters in the bot's database
# Usage: /gallery [rarity_name]
# ══════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("gallery"))
async def cmd_gallery(client, message: Message):
    uid = message.from_user.id

    group_ok, channel_ok = await check_membership(uid, client)
    if not (group_ok and channel_ok):
        text, markup = _join_card(
            uid,
            message.from_user.first_name or "there",
            group_ok, channel_ok, "gallery",
        )
        return await message.reply_text(text, reply_markup=markup)

    rarity_filter = (
        message.command[1].strip().lower()
        if len(message.command) > 1 else None
    )
    if rarity_filter and not get_rarity(rarity_filter):
        valid = " · ".join(
            r.name for r in sorted(
                {**RARITIES, **SUB_RARITIES}.values(),
                key=lambda t: RARITY_ORDER.get(t.name, 9999),
            )
        )
        return await message.reply_text(
            f"❌ Unknown rarity `{escape(rarity_filter)}`.\n"
            f"<i>Valid: {valid}</i>",
            parse_mode=enums.ParseMode.HTML,
        )

    await _display_gallery(
        client, message, uid,
        page=0, rarity_filter=rarity_filter, is_initial=True,
    )


@app.on_callback_query(filters.regex(r"^gallery:"))
async def gallery_cb(client, cb):
    try:
        parts         = cb.data.split(":")    # gallery:page:uid:rarity
        page          = int(parts[1])
        uid           = int(parts[2])
        r_str         = parts[3] if len(parts) > 3 else "None"
        rarity_filter = None if r_str == "None" else r_str
    except Exception:
        return await cb.answer("Invalid.", show_alert=True)

    if cb.from_user.id != uid:
        return await cb.answer("Not your session!", show_alert=True)

    await cb.answer()
    await _display_gallery(
        client, cb.message, uid,
        page=page, rarity_filter=rarity_filter,
        is_initial=False, callback_query=cb,
    )


@app.on_callback_query(filters.regex(r"^gallery_filter:"))
async def gallery_filter_cb(_, cb):
    try:
        _, uid_s = cb.data.split(":", 1)
        uid = int(uid_s)
    except Exception:
        return await cb.answer("Invalid.", show_alert=True)

    if cb.from_user.id != uid:
        return await cb.answer("Not your session!", show_alert=True)

    all_tiers = {**RARITIES, **SUB_RARITIES}
    keyboard, row = [], []
    for i, tier in enumerate(
        sorted(all_tiers.values(), key=lambda t: RARITY_ORDER.get(t.name, 9999)),
        start=1,
    ):
        row.append(
            IKB(
                f"{tier.emoji} {tier.display_name}",
                callback_data=f"gallery:0:{uid}:{tier.name}",
            )
        )
        if i % 3 == 0:
            keyboard.append(row); row = []
    if row:
        keyboard.append(row)

    keyboard.append([IKB("✖️ All Rarities", callback_data=f"gallery:0:{uid}:None")])

    await cb.answer()
    try:
        await cb.message.edit_text(
            "🔎 <b>Filter Gallery by Rarity:</b>",
            reply_markup=IKM(keyboard),
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception:
        pass


async def _display_gallery(
    client,
    source,
    user_id: int,
    page: int,
    rarity_filter: str | None = None,
    is_initial: bool = False,
    callback_query=None,
):
    query: dict = {"enabled": True}
    if rarity_filter:
        query["rarity"] = rarity_filter

    try:
        total       = await _col("characters").count_documents(query)
        total_pages = max(1, math.ceil(total / GALLERY_PER_PAGE))
        page        = max(0, min(page, total_pages - 1))

        char_list = (
            await _col("characters")
            .find(query)
            .skip(page * GALLERY_PER_PAGE)
            .limit(GALLERY_PER_PAGE)
            .to_list(None)
        )
    except Exception:
        log.exception("_display_gallery DB error")
        err = "❌ Error fetching gallery. Please try again."
        if callback_query:
            try:   await callback_query.message.edit_text(err)
            except Exception: pass
        else:
            await source.reply_text(err)
        return

    if not char_list:
        msg = (
            f"🖼️ No characters found for <b>{escape(rarity_filter)}</b>."
            if rarity_filter
            else "🖼️ No characters in the database yet."
        )
        if callback_query:
            try:   await callback_query.message.edit_text(msg, parse_mode=enums.ParseMode.HTML)
            except Exception: pass
        else:
            await source.reply_text(msg, parse_mode=enums.ParseMode.HTML)
        return

    rf_str = rarity_filter or "None"

    lines = [f"🖼️ <b>Global Gallery</b>  〔{page + 1}/{total_pages}〕"]
    if rarity_filter:
        tier  = get_rarity(rarity_filter)
        emoji = tier.emoji if tier else "❓"
        lines.append(f"<i>Filter: {emoji} {rarity_filter.title()}</i>")
    lines.append(f"<i>{total} characters total</i>\n")

    for i, char in enumerate(char_list, start=page * GALLERY_PER_PAGE + 1):
        tier    = get_rarity(char.get("rarity") or "common")
        r_emoji = tier.emoji if tier else "❓"
        cid     = char.get("id") or char.get("char_id") or "?"
        vid_tag = " 🎬" if char.get("video_url") else ""
        lines.append(
            f"  {i}. {r_emoji} <code>{cid}</code>  "
            f"<b>{escape(char.get('name', 'Unknown'))}</b>"
            f"  <i>{escape(char.get('anime', '?'))}</i>{vid_tag}"
        )

    text = "\n".join(lines)

    def gnb(label: str, target: int) -> IKB:
        if 0 <= target < total_pages:
            return IKB(label, callback_data=f"gallery:{target}:{user_id}:{rf_str}")
        return IKB("·", callback_data="noop")

    markup = IKM([
        [gnb("⬅️ Prev", page - 1),
         IKB(f"📖 {page + 1}/{total_pages}", callback_data="noop"),
         gnb("Next ➡️", page + 1)],
        [gnb("⏪ ×5", page - 5), gnb("×5 ⏩", page + 5)],
        [IKB("🔎 Filter Rarity", callback_data=f"gallery_filter:{user_id}")],
    ])

    cover_img = None
    try:
        cover_char = random.choice(char_list)
        cover_img  = cover_char.get("img_url") or None
    except Exception:
        pass

    if is_initial:
        try:
            if cover_img:
                await source.reply_photo(
                    cover_img, caption=text, reply_markup=markup,
                    parse_mode=enums.ParseMode.HTML,
                )
                return
        except Exception:
            pass
        await source.reply_text(
            text, reply_markup=markup, parse_mode=enums.ParseMode.HTML
        )
    else:
        try:
            if cover_img:
                await callback_query.message.edit_media(
                    InputMediaPhoto(
                        cover_img, caption=text,
                        parse_mode=enums.ParseMode.HTML,
                    ),
                    reply_markup=markup,
                )
                return
        except Exception:
            pass
        try:
            await callback_query.message.edit_text(
                text, reply_markup=markup, parse_mode=enums.ParseMode.HTML
            )
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# /sort
# ══════════════════════════════════════════════════════════════════════════════

SORT_OPTIONS = ["rarity", "name", "anime", "recent"]

@app.on_message(filters.command("sort"))
async def cmd_sort(_, message: Message):
    args = message.command
    if len(args) < 2 or args[1].lower() not in SORT_OPTIONS:
        opts = " | ".join(SORT_OPTIONS)
        return await message.reply_text(
            f"🔃 **Sort Harem**\n`/sort <{opts}>`",
            parse_mode=enums.ParseMode.MARKDOWN,
        )
    val = args[1].lower()
    await update_user(message.from_user.id, {"$set": {"harem_sort": val}})
    await message.reply_text(f"✅ Harem sorted by **{val}**!")


# ══════════════════════════════════════════════════════════════════════════════
# joined_check  —  "🔄 Verify Membership" button
# ══════════════════════════════════════════════════════════════════════════════

@app.on_callback_query(filters.regex(r"^joined_check:"))
async def joined_check_cb(client, cb):
    try:
        _, uid_s, context = cb.data.split(":", 2)
        uid = int(uid_s)
    except Exception:
        return await cb.answer("Invalid request.", show_alert=True)

    if cb.from_user.id != uid:
        return await cb.answer("⛔ Not your button.", show_alert=True)

    await cb.answer("🔄 Verifying…", show_alert=False)

    try:
        group_ok, channel_ok = await check_membership(uid, client)
    except Exception as e:
        log.warning("joined_check error uid=%d: %s", uid, e)
        return await cb.answer("⚠️ Verification failed. Try again.", show_alert=True)

    name = cb.from_user.first_name or "there"

    # Still missing at least one chat
    if not (group_ok and channel_ok):
        text, markup = _join_card(uid, name, group_ok, channel_ok, context)
        try:   await cb.message.edit_text(text, reply_markup=markup)
        except Exception: pass
        return

    # All joined — route to the correct feature
    if context == "harem":
        try:
            await cb.message.edit_text(
                f"✅ Verified, <b>{escape(name)}</b>! Loading your harem…",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception: pass
        await display_harem(
            client, cb.message, uid, 0,
            None, is_initial=False, callback_query=cb,
        )

    elif context == "gallery":
        try:
            await cb.message.edit_text("✅ Verified! Loading gallery…")
        except Exception: pass
        await _display_gallery(
            client, cb.message, uid,
            page=0, rarity_filter=None,
            is_initial=False, callback_query=cb,
        )

    else:
        try:
            await cb.message.edit_text(
                f"✅ <b>All set, {escape(name)}!</b>\n"
                "Use the command again to continue.",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception: pass
