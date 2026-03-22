"""
SoulCatcher — harem.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Commands
  /harem  [page]          — view your full harem (paginated, grouped by anime)
  /fav    <char_id>       — toggle a favourite (used as harem cover)
  /cmode                  — change collection view mode

Callbacks (internal)
  h:<uid>:<page>:<filter>         — pagination
  h_rar:<uid>                     — open rarity breakdown panel
  h_rar_close:<uid>               — close breakdown panel
  h_fil:<uid>:<rarity_key>:<page> — filtered view
  cmode_main:<mode>:<uid>         — cmode top-level choice
  cmode_set:<rarity_key>:<uid>    — set rarity filter mode
  noop                            — dead button (no action)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
from __future__ import annotations

import logging
import math
import random
from html import escape
from typing import Optional

from pyrogram import enums, filters
from pyrogram.types import (
    InlineKeyboardButton as IKB,
    InlineKeyboardMarkup as IKM,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)

from .. import app
from ..database import (
    _col,
    get_or_create_user,
    get_harem_rarity_counts,
)
from ..rarity import RARITIES, SUB_RARITIES, get_rarity

log = logging.getLogger("SoulCatcher.harem")

# ─────────────────────────────────────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────────────────────────────────────

CHARS_PER_PAGE = 15

# Full rarity order for display (main tiers ordered by id, then sub-rarities)
_RARITY_ORDER: dict[str, int] = {
    r.name: r.id for r in sorted(RARITIES.values(), key=lambda x: x.id)
}
_RARITY_ORDER.update({
    r.name: r.id for r in sorted(SUB_RARITIES.values(), key=lambda x: x.id)
})

# Collection mode keys → display label
_CMODE_LABELS: dict[str, str] = {
    "all":      "All Characters",
    "anime":    "Sorted by Anime (A–Z)",
    "name":     "Sorted by Name (A–Z)",
    "recent":   "Recently Obtained",
    # rarity keys — built dynamically below
}
for _r in sorted(RARITIES.values(), key=lambda x: x.id):
    _CMODE_LABELS[_r.name] = f"{_r.emoji} {_r.display_name} Only"
for _r in sorted(SUB_RARITIES.values(), key=lambda x: x.id):
    _CMODE_LABELS[_r.name] = f"{_r.emoji} {_r.display_name} Only"

# Cover fallback image when a character has no media
_FALLBACK_IMG = "https://files.catbox.moe/43vfsu.jpg"


# ─────────────────────────────────────────────────────────────────────────────
#  Rarity helpers
# ─────────────────────────────────────────────────────────────────────────────

def _rarity_emoji(rarity_key: str) -> str:
    """Return the emoji for any rarity key (main or sub)."""
    tier = get_rarity(rarity_key)
    return tier.emoji if tier else "❓"


def _rarity_display(rarity_key: str) -> str:
    """Return 'emoji DisplayName' for a rarity key."""
    tier = get_rarity(rarity_key)
    if tier:
        return f"{tier.emoji} {tier.display_name}"
    return f"❓ {rarity_key.title()}"


def _is_video(url: str) -> bool:
    if not url:
        return False
    return url.lower().split("?")[0].endswith((".mp4", ".mkv", ".webm", ".mov", ".avi", ".gif"))


# ─────────────────────────────────────────────────────────────────────────────
#  Database helpers (thin wrappers using actual DB schema)
# ─────────────────────────────────────────────────────────────────────────────

async def _get_user_doc(uid: int) -> dict:
    return await _col("users").find_one({"user_id": uid}) or {}


async def _fetch_all_chars(uid: int) -> list[dict]:
    """Fetch all user_characters for this user, sorted by rarity then name."""
    return (
        await _col("user_characters")
        .find({"user_id": uid})
        .sort([("rarity", 1), ("name", 1)])
        .to_list(None)
    )


async def _fetch_chars_filtered(uid: int, rarity_key: str) -> list[dict]:
    return (
        await _col("user_characters")
        .find({"user_id": uid, "rarity": rarity_key})
        .sort([("name", 1)])
        .to_list(None)
    )


def _apply_cmode(chars: list[dict], cmode: str) -> list[dict]:
    """Sort/filter character list according to collection_mode."""
    if cmode == "all":
        # Sort by rarity tier order, then name
        return sorted(
            chars,
            key=lambda c: (_RARITY_ORDER.get(c.get("rarity", ""), 99), c.get("name", "").lower()),
        )
    if cmode == "anime":
        return sorted(chars, key=lambda c: (c.get("anime", "").lower(), c.get("name", "").lower()))
    if cmode == "name":
        return sorted(chars, key=lambda c: c.get("name", "").lower())
    if cmode == "recent":
        # obtained_at descending — already fetched ascending, just reverse
        return sorted(chars, key=lambda c: c.get("obtained_at") or 0, reverse=True)
    # rarity-specific filter
    return [c for c in chars if c.get("rarity") == cmode]


def _dedup(chars: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """
    Deduplicate by char_id, keeping the first occurrence.
    Returns (unique_chars, {char_id: total_count}).
    """
    seen: dict[str, int] = {}
    unique: list[dict] = []
    for c in chars:
        cid = c.get("char_id") or c.get("id") or ""
        seen[cid] = seen.get(cid, 0) + 1
        if seen[cid] == 1:
            unique.append(c)
    return unique, seen


# ─────────────────────────────────────────────────────────────────────────────
#  Cover media helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _pick_cover(uid: int, chars: list[dict]) -> Optional[dict]:
    """Return favourite character if set and in chars, else random choice."""
    if not chars:
        return None
    user_doc = await _get_user_doc(uid)
    favs = user_doc.get("favorites") or []
    if favs:
        fav_id = str(favs[0])
        for c in chars:
            if str(c.get("char_id") or c.get("id") or "") == fav_id:
                return c
    return random.choice(chars)


def _cover_url(char: Optional[dict]) -> Optional[str]:
    """Return best available media URL from a character doc."""
    if not char:
        return None
    return char.get("video_url") or char.get("img_url") or None


async def _send_media(
    chat_id: int,
    url: Optional[str],
    text: str,
    markup: IKM,
    delete_first: Optional[object] = None,
) -> None:
    """
    Delete old message if provided, then send photo / video / text.
    We always delete + resend (rather than edit_media) because Telegram
    does not allow switching media type in place, and animated characters
    must show as actual videos — not silently fall back to text.
    """
    if delete_first:
        try:
            await delete_first.delete()
        except Exception:
            pass

    effective_url = url or _FALLBACK_IMG
    try:
        if _is_video(effective_url):
            await app.send_video(
                chat_id, effective_url,
                caption=text, reply_markup=markup,
                parse_mode=enums.ParseMode.HTML,
            )
        else:
            await app.send_photo(
                chat_id, effective_url,
                caption=text, reply_markup=markup,
                parse_mode=enums.ParseMode.HTML,
            )
        return
    except Exception as exc:
        log.warning("cover send failed (%s): %s — falling back to text", effective_url, exc)

    # Last-resort plain text
    try:
        await app.send_message(
            chat_id, text,
            reply_markup=markup,
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as exc:
        log.error("cover text fallback also failed: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
#  Harem text builder
# ─────────────────────────────────────────────────────────────────────────────

async def _build_harem_text(
    uid: int,
    unique: list[dict],
    counts: dict[str, int],
    page: int,
    total_pages: int,
    user_name: str,
    cmode: str,
    filter_rarity: Optional[str] = None,
) -> str:
    chars_per_page = CHARS_PER_PAGE
    sliced = unique[page * chars_per_page: (page + 1) * chars_per_page]

    mode_label = _CMODE_LABELS.get(cmode, cmode.title())
    header = (
        f"<b>🌸 {escape(user_name)}'s Harem</b>\n"
        f"<i>Page {page + 1}/{total_pages}  ·  {len(unique)} unique characters</i>\n"
    )
    if filter_rarity:
        header += f"<i>Filter: {_rarity_display(filter_rarity)}</i>\n"
    elif cmode != "all":
        header += f"<i>Mode: {mode_label}</i>\n"
    header += "\n"

    # Group by anime for clean display
    grouped: dict[str, list[dict]] = {}
    for c in sliced:
        anime = c.get("anime") or "Unknown"
        grouped.setdefault(anime, []).append(c)

    body_lines: list[str] = []
    for anime, anime_chars in grouped.items():
        # Count how many total chars exist in DB for this anime
        try:
            total_in_db = await _col("characters").count_documents({"anime": anime})
        except Exception:
            total_in_db = "?"

        user_count_for_anime = len(anime_chars)
        body_lines.append(
            f"<b>📌 {escape(anime)}</b>  "
            f"<code>{user_count_for_anime}/{total_in_db}</code>"
        )

        for char in anime_chars:
            cid     = char.get("char_id") or char.get("id") or "????"
            count   = counts.get(cid, 1)
            r_emoji = _rarity_emoji(char.get("rarity", ""))
            name    = escape(char.get("name") or "Unknown")
            dup_tag = f" ×{count}" if count > 1 else ""
            # Show 🎬 badge for video/animated characters
            vid_tag = " 🎬" if char.get("video_url") else ""
            body_lines.append(
                f"  {r_emoji} <code>{cid}</code> {name}{dup_tag}{vid_tag}"
            )
        body_lines.append("")

    return header + "\n".join(body_lines)


# ─────────────────────────────────────────────────────────────────────────────
#  Navigation markup builder
# ─────────────────────────────────────────────────────────────────────────────

def _nav_markup(
    uid: int,
    page: int,
    total_pages: int,
    filter_rarity: Optional[str],
) -> IKM:
    fr = filter_rarity or "None"

    def nb(label: str, target: int) -> IKB:
        if 0 <= target < total_pages:
            return IKB(label, callback_data=f"h:{uid}:{target}:{fr}")
        return IKB("·", callback_data="noop")

    def fnb(label: str, target: int) -> IKB:
        """Fast-nav ×2 buttons."""
        if 0 <= target < total_pages:
            return IKB(label, callback_data=f"h:{uid}:{target}:{fr}")
        return IKB("·", callback_data="noop")

    rows = [
        [
            nb("⬅️ Prev", page - 1),
            IKB(f"📖 {page + 1}/{total_pages}", callback_data="noop"),
            nb("Next ➡️", page + 1),
        ],
        [
            fnb("⏪ ×2", max(0, page - 2)),
            fnb("×2 ⏩", min(total_pages - 1, page + 2)),
        ],
        [
            IKB("📊 Rarity Stats", callback_data=f"h_rar:{uid}"),
            IKB("🔎 Filter", callback_data=f"h_rar:{uid}"),
        ],
        [IKB("⚙️ Collection Mode", callback_data=f"cmode_main:menu:{uid}")],
    ]
    return IKM(rows)


# ─────────────────────────────────────────────────────────────────────────────
#  Core display function
# ─────────────────────────────────────────────────────────────────────────────

async def _show_harem(
    source,
    uid: int,
    page: int,
    is_initial: bool,
    filter_rarity: Optional[str] = None,
    cb=None,
) -> None:
    user_doc = await _get_user_doc(uid)
    cmode    = (user_doc.get("collection_mode") or "all").lower()

    all_chars = await _fetch_all_chars(uid)
    if not all_chars:
        msg = "🌸 Your harem is empty! Claim characters when they spawn."
        if cb:
            try:
                await cb.message.edit_text(msg)
            except Exception:
                pass
        else:
            await source.reply_text(msg)
        return

    # Apply collection mode sort/filter
    filtered = _apply_cmode(all_chars, cmode)

    # Optional rarity filter on top
    if filter_rarity:
        filtered = [c for c in filtered if c.get("rarity") == filter_rarity]

    if not filtered:
        err = (
            f"❌ No <b>{_rarity_display(filter_rarity)}</b> characters in your harem!"
            if filter_rarity else
            f"❌ No characters match your current mode (<b>{_CMODE_LABELS.get(cmode, cmode)}</b>)."
        )
        if cb:
            try:
                await cb.message.edit_text(err, parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass
        else:
            await source.reply_text(err, parse_mode=enums.ParseMode.HTML)
        return

    unique, counts = _dedup(filtered)
    total_pages    = max(1, math.ceil(len(unique) / CHARS_PER_PAGE))
    page           = max(0, min(page, total_pages - 1))

    user_name = (
        cb.from_user.first_name if cb else source.from_user.first_name
    ) or "Collector"

    text   = await _build_harem_text(uid, unique, counts, page, total_pages, user_name, cmode, filter_rarity)
    markup = _nav_markup(uid, page, total_pages, filter_rarity)

    cover     = await _pick_cover(uid, unique)
    cover_url = _cover_url(cover)
    chat_id   = (cb.message if cb else source).chat.id

    if is_initial:
        await _send_media(chat_id, cover_url, text, markup, delete_first=None)
    else:
        await _send_media(chat_id, cover_url, text, markup, delete_first=cb.message)


# ─────────────────────────────────────────────────────────────────────────────
#  /harem command
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command(["harem", "collection"]))
async def cmd_harem(client, message: Message):
    uid = message.from_user.id
    await get_or_create_user(
        uid,
        message.from_user.username   or "",
        message.from_user.first_name or "",
        getattr(message.from_user, "last_name", "") or "",
    )

    # Optional: /harem <page>
    page = 0
    args = message.command
    if len(args) > 1 and args[1].isdigit():
        page = max(0, int(args[1]) - 1)   # user types 1-indexed

    await _show_harem(message, uid, page=page, is_initial=True)


# ─────────────────────────────────────────────────────────────────────────────
#  Pagination callback  h:<uid>:<page>:<filter>
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^h:"))
async def harem_cb(_, cb):
    parts = cb.data.split(":")
    if len(parts) < 4:
        return await cb.answer("Invalid data.", show_alert=True)

    uid          = int(parts[1])
    page         = int(parts[2])
    filter_str   = parts[3]
    filter_rarity = None if filter_str == "None" else filter_str

    if cb.from_user.id != uid:
        return await cb.answer("❌ This is not your harem!", show_alert=True)

    await cb.answer()
    await _show_harem(cb.message, uid, page=page, is_initial=False, filter_rarity=filter_rarity, cb=cb)


# ─────────────────────────────────────────────────────────────────────────────
#  Dead-button handler
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^noop$"))
async def noop_cb(_, cb):
    await cb.answer()


# ─────────────────────────────────────────────────────────────────────────────
#  Rarity breakdown / filter panel  h_rar:<uid>
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^h_rar:"))
async def harem_rarity_cb(_, cb):
    uid = int(cb.data.split(":")[1])

    if cb.from_user.id != uid:
        return await cb.answer("❌ Not your harem!", show_alert=True)

    rarity_counts = await get_harem_rarity_counts(uid)
    if not rarity_counts:
        return await cb.answer("No characters found.", show_alert=True)

    # Sort by rarity tier order
    sorted_rarities = sorted(
        rarity_counts.items(),
        key=lambda kv: _RARITY_ORDER.get(kv[0], 99),
    )

    keyboard: list[list[IKB]] = []
    row: list[IKB] = []
    for i, (rkey, count) in enumerate(sorted_rarities, 1):
        emoji   = _rarity_emoji(rkey)
        tier    = get_rarity(rkey)
        r_name  = tier.display_name if tier else rkey.title()
        row.append(IKB(
            f"{emoji} {r_name} ({count})",
            callback_data=f"h_fil:{uid}:{rkey}:0",
        ))
        if i % 2 == 0:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([IKB("✖️ Close", callback_data=f"h_rar_close:{uid}")])

    total = sum(rarity_counts.values())
    await cb.answer()
    try:
        await cb.message.reply_text(
            f"📊 <b>{escape(cb.from_user.first_name)}'s Rarity Breakdown</b>\n"
            f"<i>Total: {total} characters — tap to filter</i>",
            reply_markup=IKM(keyboard),
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as exc:
        log.warning("rarity panel send failed: %s", exc)


@app.on_callback_query(filters.regex(r"^h_rar_close:"))
async def harem_rarity_close_cb(_, cb):
    uid = int(cb.data.split(":")[1])
    if cb.from_user.id != uid:
        return await cb.answer("❌ Not yours!", show_alert=True)
    await cb.answer()
    try:
        await cb.message.delete()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  Filtered view  h_fil:<uid>:<rarity_key>:<page>
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^h_fil:"))
async def harem_filter_cb(_, cb):
    parts      = cb.data.split(":")
    uid        = int(parts[1])
    rarity_key = parts[2]
    page       = int(parts[3])

    if cb.from_user.id != uid:
        return await cb.answer("❌ Not your harem!", show_alert=True)

    chars = await _fetch_chars_filtered(uid, rarity_key)
    if not chars:
        tier = get_rarity(rarity_key)
        r_name = tier.display_name if tier else rarity_key.title()
        return await cb.answer(f"No {r_name} characters in your harem.", show_alert=True)

    unique, counts = _dedup(chars)
    total_pages    = max(1, math.ceil(len(unique) / CHARS_PER_PAGE))
    page           = max(0, min(page, total_pages - 1))
    sliced         = unique[page * CHARS_PER_PAGE: (page + 1) * CHARS_PER_PAGE]

    tier    = get_rarity(rarity_key)
    r_emoji = tier.emoji if tier else "❓"
    r_name  = tier.display_name if tier else rarity_key.title()
    name    = cb.from_user.first_name or "Collector"

    # Build grouped text
    grouped: dict[str, list[dict]] = {}
    for c in sliced:
        grouped.setdefault(c.get("anime") or "Unknown", []).append(c)

    lines: list[str] = [
        f"<b>{r_emoji} {r_name} Collection — {escape(name)}</b>",
        f"<i>{len(unique)} unique  ·  page {page + 1}/{total_pages}</i>\n",
    ]
    for anime, anime_chars in grouped.items():
        lines.append(f"<b>📌 {escape(anime)}</b>")
        for char in anime_chars:
            cid     = char.get("char_id") or char.get("id") or "????"
            count   = counts.get(cid, 1)
            dup_tag = f" ×{count}" if count > 1 else ""
            vid_tag = " 🎬" if char.get("video_url") else ""
            lines.append(
                f"  {r_emoji} <code>{cid}</code> {escape(char.get('name') or 'Unknown')}{dup_tag}{vid_tag}"
            )
        lines.append("")

    text = "\n".join(lines)

    def fnb(label: str, target: int) -> IKB:
        if 0 <= target < total_pages:
            return IKB(label, callback_data=f"h_fil:{uid}:{rarity_key}:{target}")
        return IKB("·", callback_data="noop")

    markup = IKM([
        [
            fnb("⬅️ Prev", page - 1),
            IKB(f"📖 {page + 1}/{total_pages}", callback_data="noop"),
            fnb("Next ➡️", page + 1),
        ],
        [IKB("🔙 Back to Breakdown", callback_data=f"h_rar:{uid}")],
    ])

    # Pick cover — prefer animated (video) for animated rarity
    cover     = random.choice(chars)
    cover_url = _cover_url(cover)

    await cb.answer()
    await _send_media(cb.message.chat.id, cover_url, text, markup, delete_first=cb.message)


# ─────────────────────────────────────────────────────────────────────────────
#  /fav <char_id>  —  toggle favourite cover character
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("fav"))
async def cmd_fav(_, message: Message):
    uid  = message.from_user.id
    args = message.command

    if len(args) < 2:
        user_doc = await _get_user_doc(uid)
        favs     = user_doc.get("favorites") or []
        if not favs:
            return await message.reply_text(
                "You have no favourite set.\n"
                "Use <code>/fav &lt;char_id&gt;</code> to pin a character as your harem cover.",
                parse_mode=enums.ParseMode.HTML,
            )
        char = await _col("user_characters").find_one({
            "user_id": uid,
            "$or": [{"char_id": favs[0]}, {"id": favs[0]}],
        })
        if not char:
            return await message.reply_text(
                f"⭐ Current fav: <code>{favs[0]}</code> (character not found in harem).",
                parse_mode=enums.ParseMode.HTML,
            )
        r_emoji = _rarity_emoji(char.get("rarity", ""))
        return await message.reply_text(
            f"⭐ Current fav: {r_emoji} <b>{escape(char.get('name', '?'))}</b>  "
            f"<code>{favs[0]}</code>",
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
    user_doc = await _get_user_doc(uid)
    favs     = list(user_doc.get("favorites") or [])

    # Toggle off if already fav
    if char_cid in favs:
        favs.remove(char_cid)
        await _col("users").update_one(
            {"user_id": uid}, {"$set": {"favorites": favs}}, upsert=True
        )
        return await message.reply_text(
            f"💔 <b>{escape(char.get('name', '?'))}</b> removed from favourites.",
            parse_mode=enums.ParseMode.HTML,
        )

    # Set as fav (first position = cover)
    favs.insert(0, char_cid)
    await _col("users").update_one(
        {"user_id": uid}, {"$set": {"favorites": favs}}, upsert=True
    )
    r_emoji = _rarity_emoji(char.get("rarity", ""))
    await message.reply_text(
        f"⭐ {r_emoji} <b>{escape(char.get('name', '?'))}</b> is now your harem cover!",
        parse_mode=enums.ParseMode.HTML,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  /cmode  —  collection mode chooser
# ─────────────────────────────────────────────────────────────────────────────

def _cmode_main_markup(uid: int) -> IKM:
    # Main categories
    rarity_rows: list[list[IKB]] = []
    row: list[IKB] = []
    for i, r in enumerate(
        sorted(list(RARITIES.values()) + list(SUB_RARITIES.values()), key=lambda x: x.id),
        1,
    ):
        row.append(IKB(
            f"{r.emoji} {r.display_name}",
            callback_data=f"cmode_set:{r.name}:{uid}",
        ))
        if i % 3 == 0:
            rarity_rows.append(row)
            row = []
    if row:
        rarity_rows.append(row)

    return IKM([
        [
            IKB("📋 All",         callback_data=f"cmode_set:all:{uid}"),
            IKB("📖 By Anime",    callback_data=f"cmode_set:anime:{uid}"),
            IKB("🔤 By Name",     callback_data=f"cmode_set:name:{uid}"),
            IKB("🕒 Recent",      callback_data=f"cmode_set:recent:{uid}"),
        ],
        *rarity_rows,
        [IKB("❌ Cancel", callback_data=f"cmode_main:cancel:{uid}")],
    ])


@app.on_message(filters.command(["cmode", "collectionmode"]))
async def cmd_cmode(_, message: Message):
    uid      = message.from_user.id
    user_doc = await _get_user_doc(uid)
    cmode    = user_doc.get("collection_mode") or "all"
    name     = message.from_user.first_name or "Collector"

    await message.reply_text(
        f"<b>⚙️ Collection Mode</b>\n\n"
        f"<b>{escape(name)}</b>, choose how your harem is displayed:\n\n"
        f"Current: <code>{_CMODE_LABELS.get(cmode, cmode)}</code>",
        reply_markup=_cmode_main_markup(uid),
        parse_mode=enums.ParseMode.HTML,
    )


@app.on_callback_query(filters.regex(r"^cmode_main:"))
async def cmode_main_cb(_, cb):
    parts = cb.data.split(":")
    mode  = parts[1]
    uid   = int(parts[2])

    if cb.from_user.id != uid:
        return await cb.answer("❌ Not your panel!", show_alert=True)

    if mode == "cancel":
        await cb.answer("Cancelled.")
        try:
            await cb.message.delete()
        except Exception:
            pass
        return

    if mode == "menu":
        user_doc = await _get_user_doc(uid)
        cmode    = user_doc.get("collection_mode") or "all"
        name     = cb.from_user.first_name or "Collector"
        await cb.answer()
        try:
            await cb.message.edit_text(
                f"<b>⚙️ Collection Mode</b>\n\n"
                f"<b>{escape(name)}</b>, choose how your harem is displayed:\n\n"
                f"Current: <code>{_CMODE_LABELS.get(cmode, cmode)}</code>",
                reply_markup=_cmode_main_markup(uid),
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass


@app.on_callback_query(filters.regex(r"^cmode_set:"))
async def cmode_set_cb(_, cb):
    parts     = cb.data.split(":")
    rarity_key = parts[1]
    uid        = int(parts[2])

    if cb.from_user.id != uid:
        return await cb.answer("❌ Not your panel!", show_alert=True)

    if rarity_key not in _CMODE_LABELS:
        return await cb.answer("❌ Invalid mode.", show_alert=True)

    await _col("users").update_one(
        {"user_id": uid},
        {"$set": {"collection_mode": rarity_key}},
        upsert=True,
    )

    label = _CMODE_LABELS[rarity_key]
    name  = cb.from_user.first_name or "Collector"
    await cb.answer(f"✅ Mode set to: {label}")
    try:
        await cb.message.edit_text(
            f"<b>✅ Collection mode updated!</b>\n\n"
            f"<b>{escape(name)}</b>, your harem now shows: <b>{label}</b>\n\n"
            f"Use /harem to view your collection.",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception:
        pass
