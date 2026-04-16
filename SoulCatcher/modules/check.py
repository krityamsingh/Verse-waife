"""SoulCatcher/modules/check.py

Commands:
  /check              — paginated browsable character database
  /check <id>         — full character card with ownership stats
"""

from __future__ import annotations
import logging

from pyrogram import filters
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup as IKM,
    InlineKeyboardButton as IKB,
    InputMediaVideo,
    InputMediaPhoto,
)

from .. import app
from ..rarity import get_rarity
from ..database import _col, get_character

log = logging.getLogger("SoulCatcher.check")

PER_PAGE = 12


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _pad(raw: str) -> str:
    try:
        return str(int(raw)).zfill(4)
    except (ValueError, TypeError):
        return raw.strip()


def _rarity_badge(char: dict) -> str:
    tier = get_rarity(char.get("rarity", ""))
    if not tier:
        return f"❔ `{char.get('rarity', '?')}`"
    line = f"{tier.emoji} **{tier.display_name}**"
    for label_key, emoji_key in [
        ("festival_label",  "festival_emoji"),
        ("sport_label",     "sport_emoji"),
        ("archetype_label", "archetype_emoji"),
    ]:
        if char.get(label_key):
            line += f"  {char.get(emoji_key, '')} {char[label_key]}"
            break
    return line


def _restrictions(char: dict) -> str:
    tier = get_rarity(char.get("rarity", ""))
    parts = []
    if tier:
        if not tier.trade_allowed:
            parts.append("🚫 No Trade")
        if not tier.gift_allowed:
            parts.append("🚫 No Gift")
        if tier.max_per_user:
            parts.append(f"👤 Max {tier.max_per_user}/user")
    return "  ·  ".join(parts) if parts else "✅ Tradeable & Giftable"


async def _ownership_stats(char_id: str) -> tuple[int, int]:
    col    = _col("user_characters")
    total  = await col.count_documents({"char_id": char_id})
    owners = await col.distinct("user_id", {"char_id": char_id})
    return len(owners), total


async def _top_owners(char_id: str, limit: int = 8) -> list[dict]:
    pipeline = [
        {"$match": {"char_id": char_id}},
        {"$group": {"_id": "$user_id", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": limit},
    ]
    rows = await _col("user_characters").aggregate(pipeline).to_list(limit)
    return [{"user_id": r["_id"], "count": r["count"]} for r in rows]


async def _get_page(page: int):
    skip  = (page - 1) * PER_PAGE
    total = await _col("characters").count_documents({"enabled": True})
    docs  = await (
        _col("characters")
        .find(
            {"enabled": True},
            {"id": 1, "name": 1, "anime": 1, "rarity": 1, "video_url": 1, "img_url": 1},
        )
        .sort("id", 1)
        .skip(skip)
        .limit(PER_PAGE)
        .to_list(PER_PAGE)
    )
    return docs, total


# ─────────────────────────────────────────────────────────────────────────────
# List page
# ─────────────────────────────────────────────────────────────────────────────

def _list_text(docs: list, page: int, total: int) -> str:
    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)

    header = (
        "╔══════════════════════════════╗\n"
        "║   📋  CHARACTER DATABASE     ║\n"
        "╚══════════════════════════════╝\n\n"
        f"📦 **{total:,}** characters  ·  Page **{page}/{total_pages}**\n"
        "──────────────────────────────\n"
    )

    lines = []
    for char in docs:
        tier  = get_rarity(char.get("rarity", ""))
        emoji = tier.emoji if tier else "❔"
        media = "🎬" if char.get("video_url") else "🖼"
        lines.append(
            f"{media} `{char['id']}` {emoji} **{char['name']}**\n"
            f"         ╰ _{char.get('anime', '?')}_"
        )

    footer = (
        "\n──────────────────────────────\n"
        "💡 `/check <id>` — full character card"
    )

    return header + "\n\n".join(lines) + footer


def _list_nav(page: int, total: int) -> IKM:
    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    nav = []
    if page > 1:
        nav.append(IKB("⬅️", callback_data=f"chk_pg:{page-1}"))
    nav.append(IKB(f"📖 {page} / {total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(IKB("➡️", callback_data=f"chk_pg:{page+1}"))

    jump = []
    if page > 2:
        jump.append(IKB("⏮ First", callback_data="chk_pg:1"))
    if page < total_pages - 1:
        jump.append(IKB("Last ⏭", callback_data=f"chk_pg:{total_pages}"))

    rows = [nav]
    if jump:
        rows.append(jump)
    return IKM(rows)


# ─────────────────────────────────────────────────────────────────────────────
# Character card
# ─────────────────────────────────────────────────────────────────────────────

async def _char_card_text(char: dict) -> str:
    tier = get_rarity(char.get("rarity", ""))
    char_id = char["id"]

    unique_owners, total_copies = await _ownership_stats(char_id)

    kakera    = tier.kakera_reward  if tier else char.get("kakera_reward", "?")
    price_min = tier.sell_price_min if tier else char.get("sell_price_min", 0)
    price_max = tier.sell_price_max if tier else char.get("sell_price_max", 0)
    mpu       = tier.max_per_user   if tier else 0
    mpu_str   = str(mpu) if mpu else "∞"
    media_flag = "🎬" if char.get("video_url") else "🖼"
    return (
        f"〔 {media_flag} `{char_id}` 〕\n"
        f"❝ **{char.get('name', '?')}** ❞\n"
        f"╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌\n"
        f"📖  _{char.get('anime', '?')}_\n"
        f"✨  {_rarity_badge(char)}\n\n"
        f"👥  **{unique_owners:,}** owners  ·  **{total_copies:,}** copies  ·  max **{mpu_str}**/user\n\n"
        f"🌸  `{kakera}` kakera  ·  💵 `{price_min:,} – {price_max:,}`\n"
    )


def _card_buttons(char_id: str) -> IKM:
    return IKM([[
        IKB("👥 Top Owners", callback_data=f"chk_own:{char_id}"),
        IKB("◀️ List", callback_data="chk_pg:1"),
    ]])


# ─────────────────────────────────────────────────────────────────────────────
# Send / edit helpers  (fixed — individual try/except per media type with
#                       proper logging and a clickable fallback URL)
# ─────────────────────────────────────────────────────────────────────────────

async def _reply_card(source: Message, char: dict):
    text   = await _char_card_text(char)
    markup = _card_buttons(char["id"])
    vid    = char.get("video_url", "").strip()
    img    = char.get("img_url",   "").strip()

    if vid:
        try:
            await source.reply_video(vid, caption=text, reply_markup=markup)
            return
        except Exception as e:
            log.warning(f"reply_video failed for char {char['id']}: {e}")

    if img:
        try:
            await source.reply_photo(img, caption=text, reply_markup=markup)
            return
        except Exception as e:
            log.warning(f"reply_photo failed for char {char['id']}: {e}")

    # Fallback: text only + clickable media link so user can still open the image
    media_url = vid or img
    fallback  = text + (f"\n\n🔗 [Open Media]({media_url})" if media_url else "")
    await source.reply_text(fallback, reply_markup=markup, disable_web_page_preview=False)


async def _edit_card(source, char: dict):
    text   = await _char_card_text(char)
    markup = _card_buttons(char["id"])
    vid    = char.get("video_url", "").strip()
    img    = char.get("img_url",   "").strip()

    if vid:
        try:
            await source.edit_media(InputMediaVideo(vid, caption=text), reply_markup=markup)
            return
        except Exception as e:
            log.warning(f"edit_media(video) failed for char {char['id']}: {e}")

    if img:
        try:
            await source.edit_media(InputMediaPhoto(img, caption=text), reply_markup=markup)
            return
        except Exception as e:
            log.warning(f"edit_media(photo) failed for char {char['id']}: {e}")

    # Fallback: text only + clickable media link
    media_url = vid or img
    fallback  = text + (f"\n\n🔗 [Open Media]({media_url})" if media_url else "")
    try:
        await source.edit_text(fallback, reply_markup=markup, disable_web_page_preview=False)
    except Exception as e:
        log.warning(f"edit_text fallback also failed for char {char['id']}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Commands
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("check"))
async def cmd_check(client, message: Message):
    args = message.command

    if len(args) >= 2:
        char_id = _pad(args[1].strip())
        char    = await get_character(char_id)
        if not char:
            return await message.reply_text(
                f"❌ Character `{char_id}` not found.\n"
                "Use `/check` to browse all characters."
            )
        await _reply_card(message, char)
        return

    return await message.reply_text(
        "💡 Usage: `/check <id>`\n"
        "Example: `/check 0042`\n\n"
        "Use `/check <id>` to view full details for any character."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Callbacks
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^chk_pg:"))
async def check_page_cb(_, cb):
    page = int(cb.data.split(":")[1])
    docs, total = await _get_page(page)
    if not docs:
        return await cb.answer("No more pages.", show_alert=True)
    try:
        await cb.message.edit_text(_list_text(docs, page, total), reply_markup=_list_nav(page, total))
    except Exception:
        pass
    await cb.answer()


@app.on_callback_query(filters.regex(r"^chk_own:"))
async def check_owners_cb(client, cb):
    char_id = cb.data.split(":")[1]
    char    = await get_character(char_id)
    if not char:
        return await cb.answer("Character not found.", show_alert=True)

    unique_owners, total_copies = await _ownership_stats(char_id)
    top   = await _top_owners(char_id, limit=8)
    tier  = get_rarity(char.get("rarity", ""))
    emoji = tier.emoji if tier else "❔"

    lines = [
        "╔══════════════════════════════╗\n"
        "║     👥  OWNERSHIP STATS      ║\n"
        "╚══════════════════════════════╝\n",
        f"{emoji} **{char['name']}** `{char_id}`\n"
        f"📖 _{char.get('anime', '?')}_\n",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥  Unique Owners:  **{unique_owners:,}**\n"
        f"📦  Total Copies:   **{total_copies:,}**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🏆  **TOP HOLDERS**\n",
    ]

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"]
    if not top:
        lines.append("_No one owns this character yet._")
    else:
        for i, row in enumerate(top):
            medal = medals[i] if i < len(medals) else f"{i+1}."
            try:
                user    = await client.get_users(row["user_id"])
                mention = f"[{user.first_name}](tg://user?id={row['user_id']})"
            except Exception:
                mention = f"`{row['user_id']}`"
            copies = row["count"]
            label  = "copy" if copies == 1 else "copies"
            lines.append(f"{medal}  {mention}  —  **{copies}** {label}")

    text = "\n".join(lines)
    kb   = IKM([[IKB("◀️ Back to Card", callback_data=f"chk_back:{char_id}")]])

    try:
        await cb.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
    except Exception:
        pass
    await cb.answer()


@app.on_callback_query(filters.regex(r"^chk_back:"))
async def check_back_cb(client, cb):
    char_id = cb.data.split(":")[1]
    char    = await get_character(char_id)
    if not char:
        return await cb.answer("Character not found.", show_alert=True)
    await cb.answer()
    await _edit_card(cb.message, char)
