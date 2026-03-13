import os
import logging
import tempfile
from typing import Optional, Dict, Any, Tuple
from datetime import datetime

import aiohttp
from pyrogram import enums, filters
from pyrogram.types import Message

from .. import app, uploader_filter
from ..config import UPLOAD_CHANNEL_ID as _CFG_UPLOAD_CHANNEL_ID
from ..rarity import (
    RARITY_LIST_TEXT, FESTIVAL_SEASONS, MYTHIC_SPORTS, MYTHIC_FANTASY,
    get_rarity_by_id, get_rarity, RARITIES, SUB_RARITIES,
)
from ..database import insert_character, get_character, update_character

log = logging.getLogger("SoulCatcher.autouploader")

# ─────────────────────────────────────────────────────────────────────────────
# [FIX-1] Use UPLOAD_CHANNEL_ID from config (env var) instead of hardcoding.
#         Fallback to hardcoded value only if env var is 0/unset.
# ─────────────────────────────────────────────────────────────────────────────
UPLOAD_CHANNEL_ID: int = _CFG_UPLOAD_CHANNEL_ID if _CFG_UPLOAD_CHANNEL_ID else -1003888855632

CATBOX_API = "https://catbox.moe/user/api.php"

# [BUG-8] Max file size to download (bytes). 50 MB is safe for most servers.
MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB


# ─────────────────────────────────────────────────────────────────────────────
# UPLOAD HELP TEXT
# ─────────────────────────────────────────────────────────────────────────────

def _build_upload_help() -> str:
    lines = [
        "📸 **Reply to a photo or video, then:**",
        "`/upload <name> | <anime> | <rarity_id>` — main rarities",
        "`/upload <name> | <anime> | <rarity_id> | <sub_tag>` — sub-rarities",
        "",
        "━━━━ **MAIN RARITIES** ━━━━",
    ]
    for r in sorted(RARITIES.values(), key=lambda x: x.id):
        lines.append(f"`{r.id:>2}` {r.emoji} **{r.display_name}**")

    lines += ["", "━━━━ **SUB-RARITIES** ━━━━"]
    _parent_map = {51: "Seasonal", 61: "Mythic", 62: "Mythic", 63: "Mythic", 71: "Eternal"}
    for r in sorted(SUB_RARITIES.values(), key=lambda x: x.id):
        parent = _parent_map.get(r.id, "?")
        lines.append(
            f"`{r.id:>2}` {r.emoji} **{r.display_name}**"
            f" _(sub of {parent})_"
            + (" ⚠️ VIDEO ONLY" if r.video_only else "")
        )

    lines += ["", "━━━━ **🌸 FESTIVAL SEASONS** _(ID 51 sub-tag)_ ━━━━"]
    for k, v in FESTIVAL_SEASONS.items():
        months = ", ".join(str(m) for m in v["active_months"])
        lines.append(f"  `{k}` {v['emoji']} {v['label']}  — months: {months}")

    lines += ["", "━━━━ **🏆 MYTHIC SPORTS** _(ID 62 sub-tag)_ ━━━━"]
    for k, v in MYTHIC_SPORTS.items():
        lines.append(f"  `{k}` {v['emoji']} {v['label']}")

    lines += ["", "━━━━ **🧝 MYTHIC FANTASY** _(ID 63 sub-tag)_ ━━━━"]
    for k, v in MYTHIC_FANTASY.items():
        lines.append(f"  `{k}` {v['emoji']} {v['label']}")

    lines += [
        "",
        "━━━━ **EXAMPLES** ━━━━",
        "`/upload Sasuke | Naruto | 2`              → 🔵 Rare",
        "`/upload Luffy | One Piece | 6`            → 💀 Mythic",
        "`/upload Rukia | Bleach | 51 | diwali`     → 🌸 Festival (Diwali)",
        "`/upload Gojo | JJK | 51 | christmas`      → 🌸 Festival (Christmas)",
        "`/upload Oliver | Tsubasa | 62 | football` → 🏆 Sports (Football)",
        "`/upload Miku | Vocaloid | 63 | fairy`     → 🧝 Fantasy (Fairy)",
        "`/upload Asuna | SAO | 71`                 → 🎠 Verse  _(VIDEO ONLY)_",
        "`/upload Rem | Re:Zero | 61`               → 🔮 Limited Edition",
    ]
    return "\n".join(lines)


UPLOAD_HELP_TEXT: str = _build_upload_help()


# ─────────────────────────────────────────────────────────────────────────────
# [FIX-4 + FIX-5] CATBOX UPLOAD — proper error reporting + URL validation
# ─────────────────────────────────────────────────────────────────────────────

async def _upload_to_catbox(file_path: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Upload a local file to catbox anonymously.
    Returns (url, None) on success, (None, error_message) on failure.

    [FIX-4] Accept catbox URLs that start with http:// as well as https://
            and bare filenames like 'abc123.jpg' — catbox returns all of these.
    [FIX-5] No longer silently swallows exceptions. Returns the real error so
            the caller can show the uploader exactly what went wrong.
    """
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
                async with session.post(
                    CATBOX_API,
                    data=form,
                    timeout=aiohttp.ClientTimeout(total=180),
                ) as resp:
                    text = (await resp.text()).strip()
                    if resp.status != 200:
                        return None, f"Catbox HTTP {resp.status}: {text[:200]}"

                    # [FIX-4] Accept any catbox URL format
                    if text.startswith("https://") or text.startswith("http://"):
                        # Normalise to https
                        url = text.replace("http://", "https://", 1)
                        return url, None
                    # Bare filename returned (e.g. "abc123.mp4") — build full URL
                    if text and "/" not in text and len(text) < 80:
                        return f"https://files.catbox.moe/{text}", None

                    return None, f"Unexpected catbox response: {text[:200]}"

    except aiohttp.ClientError as e:
        return None, f"Network error: {e}"
    except OSError as e:
        return None, f"File read error: {e}"
    except Exception as e:
        return None, f"Unexpected error: {type(e).__name__}: {e}"


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _get_parent_name(rarity_id: int) -> Optional[str]:
    return {51: "seasonal", 61: "mythic", 62: "mythic", 63: "mythic", 71: "eternal"}.get(rarity_id)


# Sub-rarity IDs that require a tag — maps id → (field_prefix, lookup_dict)
_NEEDS_TAG: Dict[int, tuple] = {
    51: ("festival", FESTIVAL_SEASONS),
    62: ("sport",    MYTHIC_SPORTS),
    63: ("fantasy",  MYTHIC_FANTASY),
}


def _parse_upload_args(text: str):
    """
    /upload name | anime | rarity_id
    /upload name | anime | rarity_id | sub_tag
    Returns (char_name, anime, rarity_id, sub_tag_or_None).
    """
    parts = [p.strip() for p in text.split("|")]
    if len(parts) not in (3, 4):
        raise ValueError(
            "Use: `name | anime | rarity_id`\n"
            "or:  `name | anime | rarity_id | sub_tag`"
        )
    char_name, anime, rid = parts[0], parts[1], parts[2]
    if not char_name:
        raise ValueError("Character name cannot be empty.")
    if not anime:
        raise ValueError("Anime name cannot be empty.")
    try:
        rarity_id = int(rid)
    except ValueError:
        raise ValueError(f"Rarity ID must be a number, got: `{rid}`")
    sub_tag = parts[3].lower().strip() if len(parts) == 4 else None
    return char_name, anime, rarity_id, sub_tag or None


def _resolve_sub_meta(rarity_id: int, sub_tag: Optional[str]) -> dict:
    """Build extra_meta for sub-rarity tiers."""
    tier = get_rarity_by_id(rarity_id)
    if not tier or rarity_id not in {51, 61, 62, 63, 71}:
        return {}

    base = {"sub_tag": tier.name, "sub_label": tier.display_name, "sub_emoji": tier.emoji}

    if rarity_id not in _NEEDS_TAG:
        return base  # 61 / 71 — no extra tag needed

    _, lookup = _NEEDS_TAG[rarity_id]
    if not sub_tag or sub_tag not in lookup:
        return base

    info = lookup[sub_tag]

    if rarity_id == 51:  # 🌸 Festival
        return {**base,
            "festival_season": sub_tag,
            "festival_label":  info["label"],
            "festival_emoji":  info["emoji"],
            "active_months":   info["active_months"],
        }
    if rarity_id == 62:  # 🏆 Sports
        return {**base,
            "sport_type":  sub_tag,
            "sport_label": info["label"],
            "sport_emoji": info["emoji"],
        }
    if rarity_id == 63:  # 🧝 Fantasy
        return {**base,
            "archetype":       sub_tag,
            "archetype_label": info["label"],
            "archetype_emoji": info["emoji"],
        }
    return base


def _sub_line(char: dict) -> str:
    """One-line sub-rarity summary for /charinfo."""
    if not char.get("sub_tag"):
        return ""
    line = f"\n🏷 Sub-tag: `{char['sub_tag']}`"
    for label_key, emoji_key in [
        ("festival_label", "festival_emoji"),
        ("sport_label",    "sport_emoji"),
        ("archetype_label","archetype_emoji"),
    ]:
        if char.get(label_key):
            return line + f" {char.get(emoji_key, '')} {char[label_key]}"
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


def _safe_mention(user) -> str:
    """
    [FIX-7] first_name can be None for some Telegram accounts — never crash.
    """
    name = user.first_name or user.username or str(user.id)
    return f"[{name}](tg://user?id={user.id})"


async def _download(message: Message, reply: Message) -> Tuple[Optional[str], bool]:
    """
    Download reply media to a secure temp file.
    Returns (file_path, is_video) or (None, False) on failure.

    [FIX-2] tempfile.mktemp() replaced with tempfile.mkstemp() — no race condition.
    [FIX-3] reply.document now detected as potential video (uploaders often send
            videos as files/documents in Telegram).
    [BUG-8] File size checked before download — rejects files over MAX_FILE_BYTES.
    """
    # Detect media type
    is_vid = False
    file_size = 0

    if reply.video:
        is_vid = True
        file_size = reply.video.file_size or 0
    elif reply.animation:
        is_vid = True
        file_size = reply.animation.file_size or 0
    elif reply.document:
        # [FIX-3] Documents that are video mime types should be treated as video
        mime = (reply.document.mime_type or "").lower()
        is_vid = mime.startswith("video/")
        file_size = reply.document.file_size or 0
    elif reply.photo:
        is_vid = False
        # photo file_size is on the largest PhotoSize
        if reply.photo:
            file_size = reply.photo.file_size or 0
    else:
        await message.reply_text(
            "❌ No supported media found in the replied message.\n"
            "Reply to a **photo**, **video**, **animation**, or **video document**."
        )
        return None, False

    # [FIX-8] Size gate
    if file_size > MAX_FILE_BYTES:
        mb = file_size / (1024 * 1024)
        await message.reply_text(
            f"❌ File too large: `{mb:.1f} MB` (max {MAX_FILE_BYTES // (1024*1024)} MB).\n"
            "Compress the video or use a smaller image."
        )
        return None, is_vid

    # [FIX-2] mkstemp creates the file securely — no race condition
    suffix = ".mp4" if is_vid else ".jpg"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)  # close the fd; Pyrogram will write via its own file handle

    try:
        path = await reply.download(tmp_path)
        return path, is_vid
    except Exception as e:
        # Clean up the temp file on download failure
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        await message.reply_text(f"❌ Download failed: `{e}`", parse_mode=enums.ParseMode.MARKDOWN)
        return None, is_vid


def _cleanup(path: Optional[str]):
    """Safely delete a temp file. Never raises."""
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# CORE UPLOAD LOGIC  (shared by /upload and /uchar media)
# ─────────────────────────────────────────────────────────────────────────────

async def _do_upload_and_save(
    client,
    message:    Message,
    file_path:  str,
    is_video:   bool,
    char_name:  str,
    anime:      str,
    rarity_id:  int,
    mention:    str,
    extra_meta: Dict[str, Any] = None,
) -> Optional[str]:
    """
    Upload file to catbox, save to DB, post to channel.
    Returns char_id on success, None on failure.
    Always cleans up file_path when done.

    [FIX-6] _cleanup() now always called via finally — no temp file leaks.
    [FIX-5] Real catbox errors are shown to the uploader.
    [FIX-9] Channel post sends bytes directly, not a re-download from URL.
    """
    tier = get_rarity_by_id(rarity_id)
    if not tier:
        _cleanup(file_path)
        await message.reply_text(f"❌ Unknown rarity ID `{rarity_id}`.", parse_mode=enums.ParseMode.MARKDOWN)
        return None

    status = await message.reply_text(f"⏫ Uploading **{char_name}** to catbox...")

    try:
        # 1. Upload to catbox
        url, err = await _upload_to_catbox(file_path)
        if not url:
            await status.edit_text(
                f"❌ Catbox upload failed for **{char_name}**.\n"
                f"Reason: `{err}`\n"
                "Try again or check catbox.moe status."
            )
            log.error(f"Catbox upload failed: {err}")
            return None

        log.info(f"Catbox upload OK: {url}")

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
            "added_by":       message.from_user.id,
            "sub_tag":        "",
            # Economy snapshot — stored so DB records don't go stale if rarity.py changes
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

        meta = extra_meta or {}
        sub_rarity_line = ""
        if meta.get("sub_tag"):
            sub_label = (
                meta.get("festival_label") or meta.get("sport_label")
                or meta.get("archetype_label") or meta.get("sub_label")
                or meta["sub_tag"]
            )
            sub_emoji = (
                meta.get("festival_emoji") or meta.get("sport_emoji")
                or meta.get("archetype_emoji") or meta.get("sub_emoji")
                or "🏷"
            )
            sub_rarity_line = f"\n{sub_emoji} **{sub_label}** _(sub-rarity)_"

        caption = (
            f"✅ **Character Added!**\n\n"
            f"🆔 `{char_id}`\n"
            f"👤 **{char_name}**\n"
            f"📖 _{anime}_\n"
            f"{tier.emoji} **{tier.display_name}**"
            f"{sub_rarity_line}\n"
            f"💰 Sell: `{tier.sell_price_min:,}–{tier.sell_price_max:,}` "
            f"| 🌸 Kakera: `{tier.kakera_reward}`\n"
            + (f"⚠️ {' | '.join(restrictions)}\n" if restrictions else "")
            + f"📤 Added by: {mention}"
            + (" _(VIDEO)_" if is_video else "")
        )

        # 4. [FIX-9] Post to upload channel using local file (not re-downloading from URL)
        #    This is faster, more reliable, and doesn't depend on catbox being reachable again.
        if UPLOAD_CHANNEL_ID:
            try:
                if is_video:
                    await client.send_video(UPLOAD_CHANNEL_ID, file_path, caption=caption)
                else:
                    await client.send_photo(UPLOAD_CHANNEL_ID, file_path, caption=caption)
            except Exception as e:
                # Channel post failure is non-fatal — char is already in DB
                log.warning(f"Channel post failed (char still saved): {e}")
                try:
                    await client.send_message(UPLOAD_CHANNEL_ID, caption)
                except Exception:
                    pass

        # 5. Reply to the uploader
        await status.edit_text(caption)
        return char_id

    finally:
        # [FIX-6] Always clean up — runs on success, catbox failure, DB error, anything
        _cleanup(file_path)


# ─────────────────────────────────────────────────────────────────────────────
# /upload
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("upload") & uploader_filter)
async def cmd_upload(client, message: Message):
    if not message.reply_to_message:
        return await message.reply_text(UPLOAD_HELP_TEXT)

    args_text = " ".join(message.command[1:]).strip()
    if not args_text:
        return await message.reply_text(UPLOAD_HELP_TEXT)

    # Parse args
    try:
        char_name, anime, rid, sub_tag = _parse_upload_args(args_text)
    except (ValueError, IndexError) as e:
        return await message.reply_text(f"❌ {e}\n\n" + UPLOAD_HELP_TEXT)

    tier = get_rarity_by_id(rid)
    if not tier:
        return await message.reply_text(f"❌ Unknown rarity ID `{rid}`\n\n" + UPLOAD_HELP_TEXT, parse_mode=enums.ParseMode.MARKDOWN)

    # Validate sub_tag if provided
    if sub_tag and rid in _NEEDS_TAG:
        _, lookup = _NEEDS_TAG[rid]
        if sub_tag not in lookup:
            valid_list = "`, `".join(sorted(lookup.keys()))
            return await message.reply_text(
                f"❌ Invalid sub-tag `{sub_tag}` for {tier.emoji} **{tier.display_name}**\n"
                f"Valid tags: `{valid_list}`"
            )

    # Media type check
    reply  = message.reply_to_message
    is_vid = bool(
        reply.video or reply.animation
        or (reply.document and (reply.document.mime_type or "").startswith("video/"))
    )

    if tier.video_only and not is_vid:
        return await message.reply_text(
            f"❌ **{tier.display_name}** is VIDEO ONLY — reply to a video/animation!"
        )

    # Download
    file_path, is_vid = await _download(message, reply)
    if not file_path:
        return  # _download already sent the error message

    # [FIX-7] Safe mention — never crashes on None first_name
    mention = _safe_mention(message.from_user)

    # Upload + save (cleanup handled inside _do_upload_and_save via finally)
    await _do_upload_and_save(
        client, message,
        file_path  = file_path,
        is_video   = is_vid,
        char_name  = char_name,
        anime      = anime,
        rarity_id  = rid,
        mention    = mention,
        extra_meta = _resolve_sub_meta(rid, sub_tag),
    )


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
        return await message.reply_text(f"❌ Character `{char_id}` not found.", parse_mode=enums.ParseMode.MARKDOWN)

    # ── media ─────────────────────────────────────────────────────────────────
    if sub_cmd == "media":
        if not message.reply_to_message:
            return await message.reply_text("Reply to a photo/video with `/uchar media <id>`", parse_mode=enums.ParseMode.MARKDOWN)

        reply  = message.reply_to_message
        tier   = get_rarity(char.get("rarity", ""))
        is_vid = bool(
            reply.video or reply.animation
            or (reply.document and (reply.document.mime_type or "").startswith("video/"))
        )

        if tier and tier.video_only and not is_vid:
            return await message.reply_text(
                f"❌ **{char['name']}** is {tier.emoji} {tier.display_name} — VIDEO ONLY!"
            )

        file_path, is_vid = await _download(message, reply)
        if not file_path:
            return

        status = await message.reply_text("⏫ Uploading new media to catbox...")
        try:
            url, err = await _upload_to_catbox(file_path)
        finally:
            # [FIX-6] Always clean up
            _cleanup(file_path)

        if not url:
            return await status.edit_text(
                f"❌ Catbox upload failed.\nReason: `{err}`"
            )

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
            return await message.reply_text("Usage: `/uchar rarity <id> <rarity_id>`", parse_mode=enums.ParseMode.MARKDOWN)
        try:
            rid = int(args[3])
        except ValueError:
            return await message.reply_text("❌ Rarity ID must be a number.")

        tier = get_rarity_by_id(rid)
        if not tier:
            return await message.reply_text(f"❌ Unknown rarity ID `{rid}`.\n\n{RARITY_LIST_TEXT}", parse_mode=enums.ParseMode.MARKDOWN)

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
            return await message.reply_text("Usage: `/uchar name <id> <new_name>`", parse_mode=enums.ParseMode.MARKDOWN)
        new_name = " ".join(args[3:]).strip()
        if not new_name:
            return await message.reply_text("❌ Name cannot be empty.")
        await update_character(char_id, {"$set": {"name": new_name}})
        await message.reply_text(f"✅ Renamed: **{char['name']}** → **{new_name}**")

    # ── anime ─────────────────────────────────────────────────────────────────
    elif sub_cmd == "anime":
        if len(args) < 4:
            return await message.reply_text("Usage: `/uchar anime <id> <new_anime>`", parse_mode=enums.ParseMode.MARKDOWN)
        new_anime = " ".join(args[3:]).strip()
        if not new_anime:
            return await message.reply_text("❌ Anime name cannot be empty.")
        await update_character(char_id, {"$set": {"anime": new_anime}})
        await message.reply_text(
            f"✅ Anime updated — **{char['name']}**: "
            f"_{char.get('anime', '?')}_ → _{new_anime}_"
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
            return await message.reply_text(f"❌ Unknown season `{key}`.\nValid: `{valid}`", parse_mode=enums.ParseMode.MARKDOWN)
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
            return await message.reply_text(f"❌ Unknown sport `{key}`.\nValid: `{valid}`", parse_mode=enums.ParseMode.MARKDOWN)
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
            return await message.reply_text(f"❌ Unknown archetype `{key}`.\nValid: `{valid}`", parse_mode=enums.ParseMode.MARKDOWN)
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
async def cmd_charinfo(_, message: Message):
    args = message.command
    if len(args) < 2:
        return await message.reply_text("Usage: `/charinfo <id>`", parse_mode=enums.ParseMode.MARKDOWN)

    char = await get_character(args[1])
    if not char:
        return await message.reply_text(f"❌ Character `{args[1]}` not found.", parse_mode=enums.ParseMode.MARKDOWN)

    tier      = get_rarity(char.get("rarity", ""))
    tier_str  = f"{tier.emoji} {tier.display_name}" if tier else f"`{char.get('rarity', '?')}`"
    media_type = "🎬 Video" if char.get("video_url") else "🖼️ Image"
    media_url  = char.get("video_url") or char.get("img_url") or "N/A"

    await message.reply_text(
        f"📄 **Character Info**\n\n"
        f"🆔 `{args[1]}`\n"
        f"👤 **{char.get('name', '?')}**\n"
        f"📖 _{char.get('anime', '?')}_\n"
        f"Rarity: {tier_str}"
        f"{_sub_line(char)}\n"
        f"{media_type}: `{media_url}`\n"
        f"💰 Sell: `{char.get('sell_price_min', 0):,}–{char.get('sell_price_max', 0):,}`\n"
        f"🌸 Kakera: `{char.get('kakera_reward', '?')}`\n"
        f"Trade: `{char.get('trade_allowed', '?')}` | "
        f"Gift: `{char.get('gift_allowed', '?')}` | "
        f"Max/user: `{char.get('max_per_user', 0) or 'unlimited'}`\n"
        f"📤 Added by: {char.get('mention', '?')}\n"
        f"🕒 `{char.get('added_at', '?')}`"
    )


# ─────────────────────────────────────────────────────────────────────────────
# /rarities
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("rarities") & uploader_filter)
async def cmd_rarities(_, message: Message):
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

    lines.append("\n**— Festival Seasons** _(ID 51 sub-tag)_")
    for k, v in FESTIVAL_SEASONS.items():
        lines.append(f"  `{k}` {v['emoji']} {v['label']}")

    lines.append("\n**— Mythic Sports** _(ID 62 sub-tag)_")
    for k, v in MYTHIC_SPORTS.items():
        lines.append(f"  `{k}` {v['emoji']} {v['label']}")

    lines.append("\n**— Mythic Fantasy** _(ID 63 sub-tag)_")
    for k, v in MYTHIC_FANTASY.items():
        lines.append(f"  `{k}` {v['emoji']} {v['label']}")

    await message.reply_text("\n".join(lines))
