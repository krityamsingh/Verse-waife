"""SoulCatcher 🌸  check.py

Commands:
  /check         — paginated list of ALL uploaded characters
  /check <id>    — show full details + media for one character  (e.g. /check 0042 or /check 42)
"""

import logging
from pyrogram import filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from .. import app
from ..rarity import get_rarity
from ..database import _col, get_character

log = logging.getLogger("SoulCatcher.check")

PER_PAGE = 15   # characters per page in list mode


def _pad(raw: str) -> str:
    """Normalise any ID input to 4-digit zero-padded form. '1' → '0001', '042' → '0042'."""
    try:
        return str(int(raw)).zfill(4)
    except (ValueError, TypeError):
        return raw.strip()


def _rarity_line(char: dict) -> str:
    tier = get_rarity(char.get("rarity", ""))
    if not tier:
        return f"`{char.get('rarity','?')}`"
    line = f"{tier.emoji} {tier.display_name}"
    # Sub-rarity decoration
    for label_key, emoji_key in [
        ("festival_label", "festival_emoji"),
        ("sport_label",    "sport_emoji"),
        ("archetype_label","archetype_emoji"),
    ]:
        if char.get(label_key):
            line += f"  {char.get(emoji_key, '')} {char[label_key]}"
            break
    return line


async def _get_page(page: int):
    """Fetch one page of characters sorted by numeric ID ascending."""
    skip  = (page - 1) * PER_PAGE
    total = await _col("characters").count_documents({"enabled": True})
    docs  = await (
        _col("characters")
        .find({"enabled": True}, {"id": 1, "name": 1, "anime": 1, "rarity": 1,
                                   "img_url": 1, "video_url": 1,
                                   "festival_label": 1, "festival_emoji": 1,
                                   "sport_label": 1, "sport_emoji": 1,
                                   "archetype_label": 1, "archetype_emoji": 1})
        .sort("id", 1)
        .skip(skip)
        .limit(PER_PAGE)
        .to_list(PER_PAGE)
    )
    return docs, total


def _build_list_text(docs: list, page: int, total: int) -> str:
    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    lines = [
        f"📋 **Character Database**  —  `{total}` total",
        f"Page `{page}` / `{total_pages}`  ·  `/check <id>` for details\n",
    ]
    for char in docs:
        tier = get_rarity(char.get("rarity", ""))
        emoji = tier.emoji if tier else "❓"
        media = "🎬" if char.get("video_url") else "🖼️"
        lines.append(
            f"{media} `{char['id']}` {emoji} **{char['name']}** — _{char.get('anime','?')}_"
        )
    return "\n".join(lines)


def _nav_buttons(page: int, total: int) -> InlineKeyboardMarkup:
    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    row = []
    if page > 1:
        row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"chk_pg:{page-1}"))
    row.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        row.append(InlineKeyboardButton("Next ▶️", callback_data=f"chk_pg:{page+1}"))
    return InlineKeyboardMarkup([row]) if row else None


# ─────────────────────────────────────────────────────────────────────────────
# /check  [id]
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("check"))
async def cmd_check(_, message: Message):
    args = message.command

    # ── /check <id>  →  single character detail view ──────────────────────────
    if len(args) >= 2:
        raw_id = args[1].strip()
        char_id = _pad(raw_id)

        char = await get_character(char_id)
        if not char:
            return await message.reply_text(
                f"❌ Character `{char_id}` not found.\n"
                "Use `/check` (no args) to browse all characters."
            )

        await _send_char_detail(message, char)
        return

    # ── /check  →  page 1 of list ─────────────────────────────────────────────
    docs, total = await _get_page(1)
    if not docs:
        return await message.reply_text("📭 No characters in the database yet.")

    await message.reply_text(
        _build_list_text(docs, 1, total),
        reply_markup=_nav_buttons(1, total),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Pagination callback  chk_pg:<page>
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^chk_pg:"))
async def check_page_cb(_, cb):
    page = int(cb.data.split(":")[1])
    docs, total = await _get_page(page)
    if not docs:
        return await cb.answer("No more pages.", show_alert=True)

    try:
        await cb.message.edit_text(
            _build_list_text(docs, page, total),
            reply_markup=_nav_buttons(page, total),
        )
    except Exception:
        pass
    await cb.answer()


# ─────────────────────────────────────────────────────────────────────────────
# Helper: send full character card
# ─────────────────────────────────────────────────────────────────────────────

async def _send_char_detail(message: Message, char: dict):
    tier      = get_rarity(char.get("rarity", ""))
    rarity_str = _rarity_line(char)
    media_type = "🎬 Video" if char.get("video_url") else "🖼️ Image"
    media_url  = char.get("video_url") or char.get("img_url") or ""

    restrictions = []
    if not char.get("trade_allowed", True): restrictions.append("🚫 No Trade")
    if not char.get("gift_allowed",  True): restrictions.append("🚫 No Gift")
    mpu = char.get("max_per_user", 0)
    if mpu: restrictions.append(f"👤 Max {mpu}/user")
    restr_line = ("⚠️ " + " | ".join(restrictions) + "\n") if restrictions else ""

    caption = (
        f"📄 **Character Info**\n\n"
        f"🆔 `{char['id']}`\n"
        f"👤 **{char.get('name','?')}**\n"
        f"📖 _{char.get('anime','?')}_\n"
        f"Rarity: {rarity_str}\n"
        f"💰 Sell: `{char.get('sell_price_min',0):,}–{char.get('sell_price_max',0):,}`\n"
        f"🌸 Kakera: `{char.get('kakera_reward','?')}`\n"
        f"{restr_line}"
        f"{media_type}: `{media_url or 'N/A'}`\n"
        f"📤 Added by: {char.get('mention','?')}\n"
        f"🕒 `{str(char.get('added_at','?'))[:19]}`"
    )

    try:
        if char.get("video_url"):
            await message.reply_video(char["video_url"], caption=caption)
        elif char.get("img_url"):
            await message.reply_photo(char["img_url"], caption=caption)
        else:
            await message.reply_text(caption)
    except Exception:
        await message.reply_text(caption)
