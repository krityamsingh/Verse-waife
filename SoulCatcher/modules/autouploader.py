"""
╔══════════════════════════════════════════════════════════════════════════════╗
║        SoulCatcher 🌸  autouploader.py                                      ║
║                                                                              ║
║  COMMANDS (Owner + Uploaders):                                               ║
║   /upload <anime> | <char> | <rarity_id>   — reply to photo/video           ║
║   /uchar media   <id>                      — update media                   ║
║   /uchar rarity  <id> <rarity_id>          — update rarity tier             ║
║   /uchar name    <id> <new_name>           — rename character               ║
║   /uchar anime   <id> <new_anime>          — update anime                   ║
║   /uchar season  <id> <season_key>         — set festival season (ID 51)    ║
║   /uchar sport   <id> <sport_key>          — set sport type (ID 62)         ║
║   /uchar fantasy <id> <archetype_key>      — set fantasy archetype (ID 63)  ║
║   /charinfo <id>                           — show character details         ║
║   /rarities                                — list all rarity IDs & tags     ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import os, tempfile
from typing import Optional, Dict, Any
from datetime import datetime

import aiohttp
from pyrogram import filters
from pyrogram.types import Message

from .. import app, uploader_filter
from ..rarity import (
    RARITY_LIST_TEXT, FESTIVAL_SEASONS, MYTHIC_SPORTS, MYTHIC_FANTASY,
    get_rarity_by_id, get_rarity, RARITIES, SUB_RARITIES,
)
from ..database import insert_character, get_character, update_character


# ─────────────────────────────────────────────────────────────────────────────
# HARDCODED UPLOAD CHANNEL  →  @SoulUploads
# ─────────────────────────────────────────────────────────────────────────────

UPLOAD_CHANNEL_ID: int = -1003888855632   # @SoulUploads

CATBOX_API = "https://catbox.moe/user/api.php"


# ─────────────────────────────────────────────────────────────────────────────
# CATBOX — anonymous upload
# ─────────────────────────────────────────────────────────────────────────────

async def _upload_to_catbox(file_path: str) -> Optional[str]:
    """Upload a local file to catbox anonymously. Returns public URL or None."""
    try:
        async with aiohttp.ClientSession() as session:
            with open(file_path, "rb") as f:
                form = aiohttp.FormData()
                form.add_field("reqtype", "fileupload")
                form.add_field(
                    "fileToUpload",
                    f,
                    filename=os.path.basename(file_path),
                    content_type="application/octet-stream",
                )
                async with session.post(CATBOX_API, data=form, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                    if resp.status == 200:
                        url = (await resp.text()).strip()
                        return url if url.startswith("https://") else None
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _get_parent_name(rarity_id: int) -> Optional[str]:
    return {51: "seasonal", 61: "mythic", 62: "mythic", 63: "mythic", 71: "eternal"}.get(rarity_id)


def _parse_upload_args(text: str):
    """Returns (anime, char_name, rarity_id) or raises ValueError."""
    for sep in ["|", "-"]:
        parts = [p.strip() for p in text.split(sep, 2)]
        if len(parts) == 3:
            anime, char_name, rid = parts
            return anime, char_name, int(rid)
    raise ValueError("Bad format — use: `anime | name | rarity_id`")


def _sub_line(char: dict) -> str:
    if not char.get("sub_tag"):
        return ""
    line = f"\n🏷 Sub-tag: `{char['sub_tag']}`"
    for label_key, emoji_key in [
        ("festival_label", "festival_emoji"),
        ("sport_label",    "sport_emoji"),
        ("archetype_label","archetype_emoji"),
    ]:
        if char.get(label_key):
            return line + f" {char.get(emoji_key,'')} {char[label_key]}"
    return line


def _format_tier(tier) -> str:
    parts = [f"{tier.emoji} **{tier.display_name}** (ID `{tier.id}`)"]
    if tier.video_only:
        parts.append("  ⚠️ VIDEO ONLY")
    parts.append(
        f"  Trade: `{tier.trade_allowed}` | Gift: `{tier.gift_allowed}` "
        f"| Max/user: `{tier.max_per_user or 'unlimited'}`"
    )
    parts.append(f"  Kakera: `{tier.kakera_reward}` | Claim: `{tier.claim_window_seconds}s`")
    return "\n".join(parts)


async def _download(message: Message, reply: Message):
    """Download reply media. Returns (file_path, is_video) or (None, bool)."""
    is_vid = bool(reply.video or reply.animation)
    tmp    = tempfile.mktemp(suffix=".mp4" if is_vid else ".jpg")
    try:
        path = await reply.download(tmp)
        return path, is_vid
    except Exception as e:
        await message.reply_text(f"❌ Download failed: `{e}`")
        return None, is_vid


def _cleanup(path: Optional[str]):
    try:
        if path: os.remove(path)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# CORE UPLOAD LOGIC  (shared by /upload and /uchar media)
# ─────────────────────────────────────────────────────────────────────────────

async def _do_upload_and_save(
    client,
    message:   Message,
    file_path: str,
    is_video:  bool,
    char_name: str,
    anime:     str,
    rarity_id: int,
    mention:   str,
    extra_meta: Dict[str, Any] = None,
) -> Optional[str]:
    """
    Upload file to catbox, save to DB, post to channel.
    Returns char_id on success, None on failure.
    """
    tier = get_rarity_by_id(rarity_id)
    if not tier:
        await message.reply_text(f"❌ Unknown rarity ID `{rarity_id}`.")
        return None

    # 1. Upload to catbox
    status = await message.reply_text(f"⏫ Uploading **{char_name}** to catbox...")
    url    = await _upload_to_catbox(file_path)
    if not url:
        await status.edit_text(f"❌ Catbox upload failed for **{char_name}**. Try again.")
        return None

    # 2. Build DB document
    doc: Dict[str, Any] = {
        "name":           char_name,
        "anime":          anime,
        "rarity":         tier.name,
        "rarity_id":      tier.id,
        "img_url":        "" if is_video else url,
        "video_url":      url if is_video else "",
        "mention":        mention,
        "added_at":       datetime.utcnow(),
        "added_by":       message.chat.id,
        "sub_tag":        "",
        # Economy snapshot
        "kakera_reward":  tier.kakera_reward,
        "sell_price_min": tier.sell_price_min,
        "sell_price_max": tier.sell_price_max,
        "trade_allowed":  tier.trade_allowed,
        "gift_allowed":   tier.gift_allowed,
        "max_per_user":   tier.max_per_user,
        "video_only":     tier.video_only,
        "wishlist_ping":  tier.wishlist_ping,
        "claim_window":   tier.claim_window_seconds,
        "drop_limit":     tier.drop_limit_per_day,
        "announce_spawn": tier.announce_spawn,
        **(extra_meta or {}),
    }
    char_id = await insert_character(doc)

    # 3. Build caption
    restrictions = []
    if not tier.trade_allowed: restrictions.append("🚫 No Trade")
    if not tier.gift_allowed:  restrictions.append("🚫 No Gift")
    if tier.max_per_user:      restrictions.append(f"👤 Max {tier.max_per_user}/user")

    caption = (
        f"✅ **Character Added!**\n\n"
        f"🆔 `{char_id}`\n"
        f"👤 **{char_name}**\n"
        f"📖 _{anime}_\n"
        f"{tier.emoji} **{tier.display_name}**\n"
        f"💰 Sell: `{tier.sell_price_min:,}–{tier.sell_price_max:,}` "
        f"| 🌸 Kakera: `{tier.kakera_reward}`\n"
        + (f"⚠️ {' | '.join(restrictions)}\n" if restrictions else "")
        + f"📤 Added by: {mention}"
        + (" _(VIDEO)_" if is_video else "")
    )

    # 4. Post to @SoulUploads
    try:
        if is_video:
            await client.send_video(UPLOAD_CHANNEL_ID, url, caption=caption)
        else:
            await client.send_photo(UPLOAD_CHANNEL_ID, url, caption=caption)
    except Exception:
        try:
            await client.send_message(UPLOAD_CHANNEL_ID, caption)
        except Exception:
            pass

    # 5. Reply to uploader
    await status.edit_text(caption)
    return char_id


# ─────────────────────────────────────────────────────────────────────────────
# /upload
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("upload") & uploader_filter)
async def cmd_upload(client, message: Message):
    if not message.reply_to_message:
        return await message.reply_text(
            "📸 Reply to a photo or video:\n"
            "`/upload <anime> | <char_name> | <rarity_id>`\n\n"
            f"{RARITY_LIST_TEXT}"
        )

    args_text = " ".join(message.command[1:]).strip()
    if not args_text:
        return await message.reply_text(
            f"Usage: `/upload anime | char | rarity_id`\n\n{RARITY_LIST_TEXT}"
        )

    try:
        anime, char_name, rid = _parse_upload_args(args_text)
    except (ValueError, IndexError) as e:
        return await message.reply_text(
            f"❌ {e}\n\n{RARITY_LIST_TEXT}"
        )

    tier = get_rarity_by_id(rid)
    if not tier:
        return await message.reply_text(f"❌ Unknown rarity ID `{rid}`.\n\n{RARITY_LIST_TEXT}")

    reply  = message.reply_to_message
    is_vid = bool(reply.video or reply.animation)

    if tier.video_only and not is_vid:
        return await message.reply_text(
            f"❌ **{tier.display_name}** is VIDEO ONLY. Reply to a video/animation!"
        )

    file_path, is_vid = await _download(message, reply)
    if not file_path:
        return

    mention = f"[{message.from_user.first_name}](tg://user?id={message.from_user.id})"

    await _do_upload_and_save(
        client, message,
        file_path=file_path,
        is_video=is_vid,
        char_name=char_name,
        anime=anime,
        rarity_id=rid,
        mention=mention,
    )
    _cleanup(file_path)


# ─────────────────────────────────────────────────────────────────────────────
# /uchar
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("uchar") & uploader_filter)
async def cmd_uchar(client, message: Message):
    args = message.command
    if len(args) < 3:
        return await message.reply_text(
            "**Update Character:**\n"
            "`/uchar media   <id>`              — reply to new photo/video\n"
            "`/uchar rarity  <id> <rarity_id>`  — change rarity tier\n"
            "`/uchar name    <id> <new_name>`   — rename\n"
            "`/uchar anime   <id> <new_anime>`  — change anime\n"
            "`/uchar season  <id> <season_key>` — set festival season (ID 51)\n"
            "`/uchar sport   <id> <sport_key>`  — set sport type (ID 62)\n"
            "`/uchar fantasy <id> <archetype>`  — set fantasy archetype (ID 63)"
        )

    sub_cmd = args[1].lower()
    char_id = args[2]

    char = await get_character(char_id)
    if not char:
        return await message.reply_text(f"❌ Character `{char_id}` not found.")

    # ── media ─────────────────────────────────────────────────────────────────
    if sub_cmd == "media":
        if not message.reply_to_message:
            return await message.reply_text("Reply to a photo/video with `/uchar media <id>`")

        reply  = message.reply_to_message
        is_vid = bool(reply.video or reply.animation)
        tier   = get_rarity(char.get("rarity", ""))

        if tier and tier.video_only and not is_vid:
            return await message.reply_text(
                f"❌ **{char['name']}** is {tier.emoji} {tier.display_name} — VIDEO ONLY!"
            )

        file_path, is_vid = await _download(message, reply)
        if not file_path:
            return

        status = await message.reply_text("⏫ Uploading new media to catbox...")
        url    = await _upload_to_catbox(file_path)
        _cleanup(file_path)

        if not url:
            return await status.edit_text("❌ Catbox upload failed. Try again.")

        update_fields = (
            {"video_url": url, "img_url": ""} if is_vid
            else {"img_url": url, "video_url": ""}
        )
        await update_character(char_id, {"$set": update_fields})
        await status.edit_text(
            f"✅ Media updated — **{char['name']}** (`{char_id}`)\n"
            f"{'🎬 Video' if is_vid else '🖼️ Image'}: `{url}`"
        )

    # ── rarity ────────────────────────────────────────────────────────────────
    elif sub_cmd == "rarity":
        if len(args) < 4:
            return await message.reply_text("Usage: `/uchar rarity <id> <rarity_id>`")
        try:
            rid = int(args[3])
        except ValueError:
            return await message.reply_text("❌ Rarity ID must be a number.")

        tier = get_rarity_by_id(rid)
        if not tier:
            return await message.reply_text(f"❌ Unknown rarity ID `{rid}`.\n\n{RARITY_LIST_TEXT}")

        if tier.video_only and not char.get("video_url"):
            return await message.reply_text(
                f"❌ **{tier.display_name}** is VIDEO ONLY but this character has no video.\n"
                "Add a video first with `/uchar media <id>`."
            )

        await update_character(char_id, {"$set": {
            "rarity":          tier.name,
            "rarity_id":       tier.id,
            "kakera_reward":   tier.kakera_reward,
            "sell_price_min":  tier.sell_price_min,
            "sell_price_max":  tier.sell_price_max,
            "trade_allowed":   tier.trade_allowed,
            "gift_allowed":    tier.gift_allowed,
            "max_per_user":    tier.max_per_user,
            "video_only":      tier.video_only,
            "wishlist_ping":   tier.wishlist_ping,
            "claim_window":    tier.claim_window_seconds,
            "drop_limit":      tier.drop_limit_per_day,
            "announce_spawn":  tier.announce_spawn,
        }})
        await message.reply_text(
            f"✅ **{char['name']}** rarity updated!\n{_format_tier(tier)}"
        )

    # ── name ──────────────────────────────────────────────────────────────────
    elif sub_cmd == "name":
        if len(args) < 4:
            return await message.reply_text("Usage: `/uchar name <id> <new_name>`")
        new_name = " ".join(args[3:])
        await update_character(char_id, {"$set": {"name": new_name}})
        await message.reply_text(f"✅ Renamed: **{char['name']}** → **{new_name}**")

    # ── anime ─────────────────────────────────────────────────────────────────
    elif sub_cmd == "anime":
        if len(args) < 4:
            return await message.reply_text("Usage: `/uchar anime <id> <new_anime>`")
        new_anime = " ".join(args[3:])
        await update_character(char_id, {"$set": {"anime": new_anime}})
        await message.reply_text(
            f"✅ Anime updated — **{char['name']}**: "
            f"_{char.get('anime','?')}_ → _{new_anime}_"
        )

    # ── season (Festival / ID 51) ─────────────────────────────────────────────
    elif sub_cmd == "season":
        if len(args) < 4:
            valid = ", ".join(FESTIVAL_SEASONS.keys())
            return await message.reply_text(
                f"Usage: `/uchar season <id> <season_key>`\nValid: `{valid}`"
            )
        key = args[3].lower()
        if key not in FESTIVAL_SEASONS:
            valid = ", ".join(FESTIVAL_SEASONS.keys())
            return await message.reply_text(f"❌ Unknown season `{key}`.\nValid: `{valid}`")
        s = FESTIVAL_SEASONS[key]
        await update_character(char_id, {"$set": {
            "sub_tag":         "festival",
            "festival_season": key,
            "festival_label":  s["label"],
            "festival_emoji":  s["emoji"],
            "active_months":   s["active_months"],
        }})
        await message.reply_text(
            f"✅ **{char['name']}** → {s['emoji']} **{s['label']}** (Festival)"
        )

    # ── sport (Sports / ID 62) ────────────────────────────────────────────────
    elif sub_cmd == "sport":
        if len(args) < 4:
            valid = ", ".join(MYTHIC_SPORTS.keys())
            return await message.reply_text(
                f"Usage: `/uchar sport <id> <sport_key>`\nValid: `{valid}`"
            )
        key = args[3].lower()
        if key not in MYTHIC_SPORTS:
            valid = ", ".join(MYTHIC_SPORTS.keys())
            return await message.reply_text(f"❌ Unknown sport `{key}`.\nValid: `{valid}`")
        s = MYTHIC_SPORTS[key]
        await update_character(char_id, {"$set": {
            "sub_tag":     "sports",
            "sport_type":  key,
            "sport_label": s["label"],
            "sport_emoji": s["emoji"],
        }})
        await message.reply_text(
            f"✅ **{char['name']}** → {s['emoji']} **{s['label']}** (Sports)"
        )

    # ── fantasy (Fantasy / ID 63) ─────────────────────────────────────────────
    elif sub_cmd == "fantasy":
        if len(args) < 4:
            valid = ", ".join(MYTHIC_FANTASY.keys())
            return await message.reply_text(
                f"Usage: `/uchar fantasy <id> <archetype_key>`\nValid: `{valid}`"
            )
        key = args[3].lower()
        if key not in MYTHIC_FANTASY:
            valid = ", ".join(MYTHIC_FANTASY.keys())
            return await message.reply_text(f"❌ Unknown archetype `{key}`.\nValid: `{valid}`")
        f_info = MYTHIC_FANTASY[key]
        await update_character(char_id, {"$set": {
            "sub_tag":         "fantasy",
            "archetype":       key,
            "archetype_label": f_info["label"],
            "archetype_emoji": f_info["emoji"],
        }})
        await message.reply_text(
            f"✅ **{char['name']}** → {f_info['emoji']} **{f_info['label']}** (Fantasy)"
        )

    else:
        await message.reply_text(
            "❌ Unknown sub-command.\n"
            "Valid: `media` | `rarity` | `name` | `anime` | `season` | `sport` | `fantasy`"
        )


# ─────────────────────────────────────────────────────────────────────────────
# /charinfo
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("charinfo") & uploader_filter)
async def cmd_charinfo(client, message: Message):
    args = message.command
    if len(args) < 2:
        return await message.reply_text("Usage: `/charinfo <id>`")

    char = await get_character(args[1])
    if not char:
        return await message.reply_text(f"❌ Character `{args[1]}` not found.")

    tier     = get_rarity(char.get("rarity", ""))
    tier_str = f"{tier.emoji} {tier.display_name}" if tier else f"`{char.get('rarity','?')}`"
    media_type = "🎬 Video" if char.get("video_url") else "🖼️ Image"
    media_url  = char.get("video_url") or char.get("img_url") or "N/A"

    await message.reply_text(
        f"📄 **Character Info**\n\n"
        f"🆔 `{args[1]}`\n"
        f"👤 **{char.get('name','?')}**\n"
        f"📖 _{char.get('anime','?')}_\n"
        f"Rarity: {tier_str}"
        f"{_sub_line(char)}\n"
        f"{media_type}: `{media_url}`\n"
        f"💰 Sell: `{char.get('sell_price_min',0):,}–{char.get('sell_price_max',0):,}`\n"
        f"🌸 Kakera: `{char.get('kakera_reward','?')}`\n"
        f"Trade: `{char.get('trade_allowed','?')}` | "
        f"Gift: `{char.get('gift_allowed','?')}` | "
        f"Max/user: `{char.get('max_per_user',0) or 'unlimited'}`\n"
        f"📤 Added by: {char.get('mention','?')}\n"
        f"🕒 `{char.get('added_at','?')}`"
    )


# ─────────────────────────────────────────────────────────────────────────────
# /rarities
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("rarities") & uploader_filter)
async def cmd_rarities(client, message: Message):
    lines = ["📋 **All Rarity IDs**\n\n**— Main Tiers —**"]
    for r in sorted(RARITIES.values(), key=lambda x: x.id):
        lines.append(
            f"`{r.id:>2}` {r.emoji} **{r.display_name}**"
            f"  W:`{r.weight}` | 🌸`{r.kakera_reward}` | "
            f"Limit:`{r.drop_limit_per_day or 'unlimited'}/day`"
        )

    lines.append("\n**— Sub-Rarities —**")
    for r in sorted(SUB_RARITIES.values(), key=lambda x: x.id):
        parent = _get_parent_name(r.id)
        lines.append(
            f"`{r.id:>2}` {r.emoji} **{r.display_name}**"
            f"  _(sub of {parent})_"
            + (" | ⚠️ VIDEO ONLY" if r.video_only else "")
        )

    lines.append("\n**— Festival Seasons** _(use with `/uchar season`)_")
    for k, v in FESTIVAL_SEASONS.items():
        lines.append(f"  `{k}` {v['emoji']} {v['label']}")

    lines.append("\n**— Mythic Sports** _(use with `/uchar sport`)_")
    for k, v in MYTHIC_SPORTS.items():
        lines.append(f"  `{k}` {v['emoji']} {v['label']}")

    lines.append("\n**— Mythic Fantasy** _(use with `/uchar fantasy`)_")
    for k, v in MYTHIC_FANTASY.items():
        lines.append(f"  `{k}` {v['emoji']} {v['label']}")

    await message.reply_text("\n".join(lines))
