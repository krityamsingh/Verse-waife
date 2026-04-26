import os
import asyncio
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

UPLOAD_CHANNEL_ID: int  = _CFG_UPLOAD_CHANNEL_ID if _CFG_UPLOAD_CHANNEL_ID else -1003869604435
CATBOX_USERHASH   = "de47eb51da1e8bc98c5ca9cf3"   # catbox.moe authenticated uploads
MAX_FILE_BYTES    = 50 * 1024 * 1024  # 50 MB

# Retry config for Catbox 412 "Uploads paused"
_CATBOX_MAX_RETRIES = 3
_CATBOX_RETRY_BASE  = 10  # seconds (doubles each attempt: 10 → 20 → 40)


# ──────────────────────────────────────────────────────────────────────────────
# NAME FORMATTING
# ──────────────────────────────────────────────────────────────────────────────

def _title(text: str) -> str:
    """Capitalise every word (handles multi-word names gracefully)."""
    return " ".join(w.capitalize() for w in text.strip().split())


def _bold(text: str) -> str:
    """Wrap in Telegram bold markdown."""
    return f"**{text}**"


# ──────────────────────────────────────────────────────────────────────────────
# UPLOAD PROVIDER CHAIN
# Priority: Catbox (auth) → 0x0.st → Oshi.at
# One file download, try each host once, fall back automatically.
# ──────────────────────────────────────────────────────────────────────────────

_PROVIDERS = [
    {
        "name":  "Catbox",
        "emoji": "📦",
        "permanent": True,
    },
    {
        "name":  "0x0.st",
        "emoji": "🗂️",
        "permanent": True,
    },
    {
        "name":  "Oshi.at",
        "emoji": "☁️",
        "permanent": True,
    },
]

# How long (seconds) to tell the user to wait before retrying when all fail
_ALL_FAILED_RETRY_SECONDS = 300


async def _try_catbox(session: aiohttp.ClientSession, file_path: str) -> Tuple[Optional[str], Optional[str]]:
    """Catbox authenticated upload. Handles 412 'paused' with one backoff retry."""
    filename = os.path.basename(file_path)
    delay    = _CATBOX_RETRY_BASE

    for attempt in range(1, _CATBOX_MAX_RETRIES + 1):
        try:
            with open(file_path, "rb") as f:
                form = aiohttp.FormData()
                form.add_field("reqtype",      "fileupload")
                form.add_field("userhash",     CATBOX_USERHASH)
                form.add_field("fileToUpload", f,
                               filename=filename,
                               content_type="application/octet-stream")
                async with session.post(
                    "https://catbox.moe/user/api.php", data=form,
                    timeout=aiohttp.ClientTimeout(total=300, connect=15, sock_read=270),
                ) as resp:
                    text = (await resp.text()).strip()
                    if resp.status == 412:
                        if attempt < _CATBOX_MAX_RETRIES:
                            log.warning("Catbox 412 — retry %d/%d in %ds", attempt, _CATBOX_MAX_RETRIES, delay)
                            await asyncio.sleep(delay)
                            delay *= 2
                            continue
                        return None, f"Catbox 412 (uploads paused) after {attempt} attempts"
                    if resp.status != 200:
                        return None, f"Catbox HTTP {resp.status}: {text[:200]}"
                    if text.startswith(("https://", "http://")):
                        return text.replace("http://", "https://", 1), None
                    if text and "/" not in text and len(text) < 80:
                        return f"https://files.catbox.moe/{text}", None
                    return None, f"Catbox unexpected response: {text[:200]}"
        except Exception as e:
            return None, f"Catbox {type(e).__name__}: {e}"

    return None, "Catbox: exhausted retries"


async def _try_0x0(session: aiohttp.ClientSession, file_path: str) -> Tuple[Optional[str], Optional[str]]:
    """0x0.st — anonymous, permanent hosting, no auth needed."""
    try:
        with open(file_path, "rb") as f:
            form = aiohttp.FormData()
            form.add_field("file", f,
                           filename=os.path.basename(file_path),
                           content_type="application/octet-stream")
            async with session.post(
                "https://0x0.st",
                data=form,
                timeout=aiohttp.ClientTimeout(total=300, connect=15, sock_read=270),
            ) as resp:
                text = (await resp.text()).strip()
                if resp.status == 200 and text.startswith(("https://", "http://")):
                    return text.replace("http://", "https://", 1), None
                return None, f"0x0.st HTTP {resp.status}: {text[:200]}"
    except Exception as e:
        return None, f"0x0.st {type(e).__name__}: {e}"


async def _try_oshi(session: aiohttp.ClientSession, file_path: str) -> Tuple[Optional[str], Optional[str]]:
    """Oshi.at — anonymous, permanent, up to 5 GB."""
    try:
        filename = os.path.basename(file_path)
        with open(file_path, "rb") as f:
            form = aiohttp.FormData()
            form.add_field("f", f,
                           filename=filename,
                           content_type="application/octet-stream")
            async with session.post(
                "https://oshi.at",
                data=form,
                headers={"User-Agent": "SoulCatcher-Bot/2.0"},
                timeout=aiohttp.ClientTimeout(total=300, connect=15, sock_read=270),
            ) as resp:
                text = (await resp.text()).strip()
                if resp.status == 200:
                    # oshi returns plain-text URL or JSON
                    for line in text.splitlines():
                        line = line.strip()
                        if line.startswith(("https://", "http://")):
                            return line.replace("http://", "https://", 1), None
                return None, f"Oshi.at HTTP {resp.status}: {text[:200]}"
    except Exception as e:
        return None, f"Oshi.at {type(e).__name__}: {e}"


_PROVIDER_FNS = [_try_catbox, _try_0x0, _try_oshi]


async def _upload_with_fallback(
    file_path: str,
    status_msg=None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Try each upload provider in order. Returns (url, provider_name, error).
    - On success: (url, provider_name, None)
    - On total failure: (None, None, combined_error)

    One file download — no re-downloading between providers.
    Updates status_msg live so the uploader can see which host is being tried.
    """
    errors: list[str] = []

    async with aiohttp.ClientSession() as session:
        for i, (provider, fn) in enumerate(zip(_PROVIDERS, _PROVIDER_FNS)):
            label = f"{provider['emoji']} Trying {provider['name']}…"
            if status_msg:
                try:
                    suffix = f"  _(fallback {i}/{len(_PROVIDERS)-1})_" if i > 0 else ""
                    await status_msg.edit_text(f"⏫ Uploading… {label}{suffix}")
                except Exception:
                    pass

            log.info("Upload attempt via %s (provider %d/%d)", provider["name"], i + 1, len(_PROVIDERS))
            url, err = await fn(session, file_path)

            if url:
                log.info("✅ Upload OK via %s — %s", provider["name"], url)
                return url, provider["name"], None

            log.warning("❌ %s failed: %s", provider["name"], err)
            errors.append(f"{provider['name']}: {err}")

    combined = " | ".join(errors)
    return None, None, combined


# ──────────────────────────────────────────────────────────────────────────────
# ARGUMENT PARSING
# ──────────────────────────────────────────────────────────────────────────────

# Sub-rarity IDs that require an extra tag
_NEEDS_TAG: Dict[int, tuple] = {
    51: ("festival", FESTIVAL_SEASONS),
    62: ("sport",    MYTHIC_SPORTS),
    63: ("fantasy",  MYTHIC_FANTASY),
}


def _parse_upload_args(text: str):
    """Parse `name | anime | rarity_id [| sub_tag]`."""
    parts = [p.strip() for p in text.split("|")]
    if len(parts) not in (3, 4):
        raise ValueError("Format: `name | anime | rarity_id` or `name | anime | rarity_id | sub_tag`")

    char_name, anime, rid = parts[0], parts[1], parts[2]
    if not char_name:
        raise ValueError("Character name cannot be empty.")
    if not anime:
        raise ValueError("Anime name cannot be empty.")
    try:
        rarity_id = int(rid)
    except ValueError:
        raise ValueError(f"Rarity ID must be a number — got: `{rid}`")

    sub_tag = parts[3].lower().strip() if len(parts) == 4 else None
    return _title(char_name), _title(anime), rarity_id, sub_tag or None


# ──────────────────────────────────────────────────────────────────────────────
# SUB-RARITY METADATA
# ──────────────────────────────────────────────────────────────────────────────

def _resolve_sub_meta(rarity_id: int, sub_tag: Optional[str]) -> dict:
    tier = get_rarity_by_id(rarity_id)
    if not tier or rarity_id not in {51, 61, 62, 63, 71}:
        return {}

    base = {"sub_tag": tier.name, "sub_label": tier.display_name, "sub_emoji": tier.emoji}

    if rarity_id not in _NEEDS_TAG or not sub_tag:
        return base

    _, lookup = _NEEDS_TAG[rarity_id]
    if sub_tag not in lookup:
        return base

    info = lookup[sub_tag]
    if rarity_id == 51:
        return {**base,
            "festival_season": sub_tag, "festival_label": info["label"],
            "festival_emoji": info["emoji"], "active_months": info["active_months"],
        }
    if rarity_id == 62:
        return {**base,
            "sport_type": sub_tag, "sport_label": info["label"], "sport_emoji": info["emoji"],
        }
    if rarity_id == 63:
        return {**base,
            "archetype": sub_tag, "archetype_label": info["label"], "archetype_emoji": info["emoji"],
        }
    return base


def _sub_line(char: dict) -> str:
    if not char.get("sub_tag"):
        return ""
    line = f"\n🏷 Sub: `{char['sub_tag']}`"
    for lk, ek in [("festival_label", "festival_emoji"), ("sport_label", "sport_emoji"), ("archetype_label", "archetype_emoji")]:
        if char.get(lk):
            return line + f" {char.get(ek, '')} {char[lk]}"
    return line


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _safe_mention(user) -> str:
    name = user.first_name or user.username or str(user.id)
    return f"[{name}](tg://user?id={user.id})"


def _cleanup(path: Optional[str]):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


async def _download(message: Message, reply: Message) -> Tuple[Optional[str], bool]:
    """Download reply media to a secure temp file. Returns (path, is_video)."""
    is_vid, file_size = False, 0

    if reply.video:
        is_vid, file_size = True, reply.video.file_size or 0
    elif reply.animation:
        is_vid, file_size = True, reply.animation.file_size or 0
    elif reply.document:
        mime = (reply.document.mime_type or "").lower()
        is_vid = mime.startswith("video/")
        file_size = reply.document.file_size or 0
    elif reply.photo:
        file_size = reply.photo.file_size or 0
    else:
        await message.reply_text(
            "❌ No supported media found.\n"
            "Please reply to a **photo**, **video**, or **animation**."
        )
        return None, False

    if file_size > MAX_FILE_BYTES:
        mb = file_size / (1024 * 1024)
        await message.reply_text(
            f"❌ File too large: `{mb:.1f} MB`\n"
            f"Maximum allowed: `{MAX_FILE_BYTES // (1024 * 1024)} MB`"
        )
        return None, is_vid

    # Use a temp directory so Pyrogram writes a fresh file (avoids mkstemp locking issues)
    tmp_dir  = tempfile.mkdtemp()
    tmp_path = os.path.join(tmp_dir, "media.mp4" if is_vid else "media.jpg")

    try:
        path = await reply.download(tmp_path)
        return path, is_vid
    except Exception as e:
        _cleanup(tmp_path)
        await message.reply_text(f"❌ Download failed: `{e}`")
        return None, is_vid


# ──────────────────────────────────────────────────────────────────────────────
# UPLOAD HELP TEXT
# ──────────────────────────────────────────────────────────────────────────────

def _build_help() -> str:
    lines = [
        "╔══════════════════════════════╗",
        "║     📸  UPLOAD  GUIDE        ║",
        "╚══════════════════════════════╝",
        "",
        "**Reply to a photo or video, then send:**",
        "`/upload name | anime | rarity_id`",
        "`/upload name | anime | rarity_id | sub_tag`",
        "",
        "━━━━━━  MAIN RARITIES  ━━━━━━",
    ]
    for r in sorted(RARITIES.values(), key=lambda x: x.id):
        lines.append(f"  `{r.id:>2}`  {r.emoji}  **{r.display_name}**")

    lines += ["", "━━━━━━  SUB-RARITIES  ━━━━━━"]
    _parent = {51: "Seasonal", 61: "Mythic", 62: "Mythic", 63: "Mythic", 71: "Eternal"}
    for r in sorted(SUB_RARITIES.values(), key=lambda x: x.id):
        tag = " ⚠️ _video only_" if r.video_only else ""
        lines.append(f"  `{r.id:>2}`  {r.emoji}  **{r.display_name}** _(sub of {_parent.get(r.id, '?')})_{tag}")

    lines += ["", "━━━━━━  FESTIVAL SEASONS  `(id 51)`  ━━━━━━"]
    for k, v in FESTIVAL_SEASONS.items():
        lines.append(f"  `{k}`  {v['emoji']}  {v['label']}")

    lines += ["", "━━━━━━  MYTHIC SPORTS  `(id 62)`  ━━━━━━"]
    for k, v in MYTHIC_SPORTS.items():
        lines.append(f"  `{k}`  {v['emoji']}  {v['label']}")

    lines += ["", "━━━━━━  MYTHIC FANTASY  `(id 63)`  ━━━━━━"]
    for k, v in MYTHIC_FANTASY.items():
        lines.append(f"  `{k}`  {v['emoji']}  {v['label']}")

    lines += [
        "",
        "━━━━━━  EXAMPLES  ━━━━━━",
        "`/upload Sasuke | Naruto | 2`              → 🔵 Rare",
        "`/upload Rukia | Bleach | 51 | diwali`     → 🌸 Festival",
        "`/upload Oliver | Tsubasa | 62 | football` → 🏆 Sports",
        "`/upload Miku | Vocaloid | 63 | fairy`     → 🧝 Fantasy",
        "`/upload Asuna | SAO | 71`                 → 🎠 Verse _(video only)_",
    ]
    return "\n".join(lines)


UPLOAD_HELP = _build_help()


# ──────────────────────────────────────────────────────────────────────────────
# CORE — upload file, save to DB, post to channel
# ──────────────────────────────────────────────────────────────────────────────

async def _do_upload(
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
    tier = get_rarity_by_id(rarity_id)
    if not tier:
        _cleanup(file_path)
        await message.reply_text(f"❌ Unknown rarity ID `{rarity_id}`.")
        return None

    status = await message.reply_text(f"⏫ Uploading {_bold(char_name)}…")

    try:
        # 1. Try each upload provider in order; one file download, no re-downloads
        url, provider, err = await _upload_with_fallback(file_path, status_msg=status)
        if not url:
            mins = _ALL_FAILED_RETRY_SECONDS // 60
            await status.edit_text(
                f"❌ **All upload providers failed** for {_bold(char_name)}\n\n"
                f"Tried: Catbox → 0x0.st → Oshi.at\n"
                f"Errors:\n`{err}`\n\n"
                f"⏳ Please retry in ~{mins} minutes."
            )
            return None

        log.info(f"Upload OK via {provider}: {url}")

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
        meta = extra_meta or {}
        sub_line = ""
        if meta.get("sub_tag"):
            sub_label = (meta.get("festival_label") or meta.get("sport_label")
                         or meta.get("archetype_label") or meta.get("sub_label") or meta["sub_tag"])
            sub_emoji = (meta.get("festival_emoji") or meta.get("sport_emoji")
                         or meta.get("archetype_emoji") or meta.get("sub_emoji") or "🏷")
            sub_line = f"\n{sub_emoji} **{sub_label}** _(sub-rarity)_"

        restrictions = " · ".join(filter(None, [
            "🚫 No Trade"          if not tier.trade_allowed else "",
            "🚫 No Gift"           if not tier.gift_allowed  else "",
            f"👤 Max {tier.max_per_user}/user" if tier.max_per_user else "",
        ]))

        caption = (
            f"✅ **Character Added**\n"
            f"{'─' * 28}\n"
            f"🆔  `{char_id}`\n"
            f"👤  **{char_name}**\n"
            f"📖  __{anime}__\n"
            f"{tier.emoji}  **{tier.display_name}**{sub_line}\n"
            f"{'─' * 28}\n"
            f"💰  `{tier.sell_price_min:,} – {tier.sell_price_max:,}`  "
            f"🌸  `{tier.kakera_reward}`\n"
            + (f"⚠️  {restrictions}\n" if restrictions else "")
            + f"{'─' * 28}\n"
            f"📤  {mention}"
            + ("  _(video)_" if is_video else "")
        )

        # 4. Post to channel (using local file — no re-download needed)
        channel_err: Optional[str] = None

        if UPLOAD_CHANNEL_ID:
            try:
                if is_video:
                    await client.send_video(UPLOAD_CHANNEL_ID, file_path, caption=caption)
                else:
                    await client.send_photo(UPLOAD_CHANNEL_ID, file_path, caption=caption)
                log.info(f"Channel post OK — char_id={char_id} channel={UPLOAD_CHANNEL_ID}")
            except Exception as media_exc:
                log.warning(f"Channel media post failed (char_id={char_id}): {media_exc}")
                try:
                    await client.send_message(UPLOAD_CHANNEL_ID, caption)
                    log.info(f"Channel text fallback OK — char_id={char_id}")
                except Exception as text_exc:
                    log.error(f"Channel text fallback also failed (char_id={char_id}): {text_exc}")
                    channel_err = f"media → `{media_exc}`\ntext  → `{text_exc}`"

        if channel_err:
            await status.edit_text(
                caption
                + f"\n\n{'─' * 28}\n"
                + f"⚠️ **Channel post failed**\n{channel_err}"
                + f"\nCharacter was saved — ID: `{char_id}`"
            )
            return char_id

        await status.edit_text(caption)
        return char_id

    finally:
        _cleanup(file_path)


# ──────────────────────────────────────────────────────────────────────────────
# /upload
# ──────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("upload") & uploader_filter)
async def cmd_upload(client, message: Message):
    if not message.reply_to_message or not " ".join(message.command[1:]).strip():
        return await message.reply_text(UPLOAD_HELP)

    try:
        char_name, anime, rid, sub_tag = _parse_upload_args(" ".join(message.command[1:]))
    except ValueError as e:
        return await message.reply_text(f"❌ {e}")

    tier = get_rarity_by_id(rid)
    if not tier:
        return await message.reply_text(f"❌ Unknown rarity ID `{rid}`\n\n{UPLOAD_HELP}")

    # Validate sub_tag
    if sub_tag and rid in _NEEDS_TAG:
        _, lookup = _NEEDS_TAG[rid]
        if sub_tag not in lookup:
            valid = "`, `".join(sorted(lookup.keys()))
            return await message.reply_text(
                f"❌ Invalid sub-tag `{sub_tag}` for {tier.emoji} **{tier.display_name}**\n"
                f"Valid: `{valid}`"
            )

    # Detect media type once and reuse — avoids mismatch between pre-check and download
    reply  = message.reply_to_message
    is_vid = bool(
        reply.video or reply.animation
        or (reply.document and (reply.document.mime_type or "").startswith("video/"))
    )

    if tier.video_only and not is_vid:
        return await message.reply_text(
            f"❌ **{tier.display_name}** requires a video — please reply to a video or animation."
        )

    file_path, is_vid = await _download(message, reply)
    if not file_path:
        return

    await _do_upload(
        client, message,
        file_path  = file_path,
        is_video   = is_vid,
        char_name  = char_name,
        anime      = anime,
        rarity_id  = rid,
        mention    = _safe_mention(message.from_user),
        extra_meta = _resolve_sub_meta(rid, sub_tag),
    )


# ──────────────────────────────────────────────────────────────────────────────
# /uchar  —  update character fields
# ──────────────────────────────────────────────────────────────────────────────

_UCHAR_HELP = (
    "**Update a Character**\n"
    "──────────────────────\n"
    "`/uchar media   <id>`              — replace photo / video\n"
    "`/uchar rarity  <id> <rarity_id>`  — change rarity tier\n"
    "`/uchar name    <id> <new name>`   — rename character\n"
    "`/uchar anime   <id> <new anime>`  — change anime title\n"
    "`/uchar season  <id> <season_key>` — set festival season _(id 51)_\n"
    "`/uchar sport   <id> <sport_key>`  — set sport type _(id 62)_\n"
    "`/uchar fantasy <id> <archetype>`  — set fantasy archetype _(id 63)_"
)


@app.on_message(filters.command("uchar") & uploader_filter)
async def cmd_uchar(client, message: Message):
    args = message.command
    if len(args) < 3:
        return await message.reply_text(_UCHAR_HELP)

    sub_cmd  = args[1].lower()
    char_id  = args[2]
    char     = await get_character(char_id)

    if not char:
        return await message.reply_text(f"❌ Character `{char_id}` not found.")

    # ── media ─────────────────────────────────────────────────────────────────
    if sub_cmd == "media":
        if not message.reply_to_message:
            return await message.reply_text("Reply to a photo/video with `/uchar media <id>`")

        reply  = message.reply_to_message
        tier   = get_rarity(char.get("rarity", ""))
        is_vid = bool(
            reply.video or reply.animation
            or (reply.document and (reply.document.mime_type or "").startswith("video/"))
        )

        if tier and tier.video_only and not is_vid:
            return await message.reply_text(
                f"❌ **{char['name']}** is {tier.emoji} **{tier.display_name}** — video only!"
            )

        file_path, is_vid = await _download(message, reply)
        if not file_path:
            return

        status = await message.reply_text("⏫ Uploading new media…")
        try:
            url, provider, err = await _upload_with_fallback(file_path, status_msg=status)
        finally:
            _cleanup(file_path)

        if not url:
            mins = _ALL_FAILED_RETRY_SECONDS // 60
            return await status.edit_text(
                f"❌ **All upload providers failed**\n\n"
                f"Tried: Catbox → 0x0.st → Oshi.at\n"
                f"Errors:\n`{err}`\n\n"
                f"⏳ Please retry in ~{mins} minutes."
            )

        await update_character(char_id, {"$set": (
            {"video_url": url, "img_url": ""} if is_vid else {"img_url": url, "video_url": ""}
        )})
        await status.edit_text(
            f"✅ Media updated\n"
            f"👤  **{char['name']}**  (`{char_id}`)\n"
            f"{'🎬' if is_vid else '🖼️'}  `{url}`"
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
            return await message.reply_text(f"❌ Unknown rarity ID `{rid}`.")

        if tier.video_only and not char.get("video_url"):
            return await message.reply_text(
                f"❌ **{tier.display_name}** is video only — add a video first with `/uchar media {char_id}`."
            )

        await update_character(char_id, {"$set": {
            "rarity": tier.name, "rarity_id": tier.id,
            "kakera_reward": tier.kakera_reward,
            "sell_price_min": tier.sell_price_min, "sell_price_max": tier.sell_price_max,
            "trade_allowed": tier.trade_allowed, "gift_allowed": tier.gift_allowed,
            "max_per_user": tier.max_per_user, "video_only": tier.video_only,
            "wishlist_ping": tier.wishlist_ping, "claim_window": tier.claim_window_seconds,
            "drop_limit": tier.drop_limit_per_day, "announce_spawn": tier.announce_spawn,
        }})
        await message.reply_text(
            f"✅ Rarity updated\n"
            f"👤  **{char['name']}**\n"
            f"{tier.emoji}  **{tier.display_name}** (ID `{tier.id}`)"
        )

    # ── name ──────────────────────────────────────────────────────────────────
    elif sub_cmd == "name":
        if len(args) < 4:
            return await message.reply_text("Usage: `/uchar name <id> <new name>`")
        new_name = _title(" ".join(args[3:]))
        if not new_name:
            return await message.reply_text("❌ Name cannot be empty.")
        await update_character(char_id, {"$set": {"name": new_name}})
        await message.reply_text(f"✅ Renamed\n**{char['name']}**  →  **{new_name}**")

    # ── anime ─────────────────────────────────────────────────────────────────
    elif sub_cmd == "anime":
        if len(args) < 4:
            return await message.reply_text("Usage: `/uchar anime <id> <new anime>`")
        new_anime = _title(" ".join(args[3:]))
        if not new_anime:
            return await message.reply_text("❌ Anime name cannot be empty.")
        await update_character(char_id, {"$set": {"anime": new_anime}})
        await message.reply_text(
            f"✅ Anime updated\n"
            f"👤  **{char['name']}**\n"
            f"_{char.get('anime', '?')}_  →  _{new_anime}_"
        )

    # ── season ────────────────────────────────────────────────────────────────
    elif sub_cmd == "season":
        if len(args) < 4:
            return await message.reply_text(
                f"Usage: `/uchar season <id> <key>`\nValid: `{'`, `'.join(FESTIVAL_SEASONS)}`"
            )
        key = args[3].lower()
        if key not in FESTIVAL_SEASONS:
            return await message.reply_text(
                f"❌ Unknown season `{key}`\nValid: `{'`, `'.join(FESTIVAL_SEASONS)}`"
            )
        s = FESTIVAL_SEASONS[key]
        await update_character(char_id, {"$set": {
            "sub_tag": "festival", "festival_season": key,
            "festival_label": s["label"], "festival_emoji": s["emoji"],
            "active_months": s["active_months"],
        }})
        await message.reply_text(
            f"✅ Season set\n👤  **{char['name']}**\n{s['emoji']}  **{s['label']}** _(Festival)_"
        )

    # ── sport ─────────────────────────────────────────────────────────────────
    elif sub_cmd == "sport":
        if len(args) < 4:
            return await message.reply_text(
                f"Usage: `/uchar sport <id> <key>`\nValid: `{'`, `'.join(MYTHIC_SPORTS)}`"
            )
        key = args[3].lower()
        if key not in MYTHIC_SPORTS:
            return await message.reply_text(
                f"❌ Unknown sport `{key}`\nValid: `{'`, `'.join(MYTHIC_SPORTS)}`"
            )
        s = MYTHIC_SPORTS[key]
        await update_character(char_id, {"$set": {
            "sub_tag": "sports", "sport_type": key,
            "sport_label": s["label"], "sport_emoji": s["emoji"],
        }})
        await message.reply_text(
            f"✅ Sport set\n👤  **{char['name']}**\n{s['emoji']}  **{s['label']}** _(Sports)_"
        )

    # ── fantasy ───────────────────────────────────────────────────────────────
    elif sub_cmd == "fantasy":
        if len(args) < 4:
            return await message.reply_text(
                f"Usage: `/uchar fantasy <id> <archetype>`\nValid: `{'`, `'.join(MYTHIC_FANTASY)}`"
            )
        key = args[3].lower()
        if key not in MYTHIC_FANTASY:
            return await message.reply_text(
                f"❌ Unknown archetype `{key}`\nValid: `{'`, `'.join(MYTHIC_FANTASY)}`"
            )
        f_info = MYTHIC_FANTASY[key]
        await update_character(char_id, {"$set": {
            "sub_tag": "fantasy", "archetype": key,
            "archetype_label": f_info["label"], "archetype_emoji": f_info["emoji"],
        }})
        await message.reply_text(
            f"✅ Fantasy set\n👤  **{char['name']}**\n{f_info['emoji']}  **{f_info['label']}** _(Fantasy)_"
        )

    else:
        await message.reply_text(
            "❌ Unknown sub-command.\n"
            "Valid: `media` · `rarity` · `name` · `anime` · `season` · `sport` · `fantasy`"
        )


# ──────────────────────────────────────────────────────────────────────────────
# /charinfo
# ──────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("charinfo") & uploader_filter)
async def cmd_charinfo(_, message: Message):
    args = message.command
    if len(args) < 2:
        return await message.reply_text("Usage: `/charinfo <id>`")

    char = await get_character(args[1])
    if not char:
        return await message.reply_text(f"❌ Character `{args[1]}` not found.")

    tier       = get_rarity(char.get("rarity", ""))
    tier_str   = f"{tier.emoji}  **{tier.display_name}**" if tier else f"`{char.get('rarity', '?')}`"
    media_type = "🎬 Video" if char.get("video_url") else "🖼️ Image"
    media_url  = char.get("video_url") or char.get("img_url") or "N/A"

    await message.reply_text(
        f"📄 **Character Info**\n"
        f"{'─' * 28}\n"
        f"🆔  `{args[1]}`\n"
        f"👤  **{char.get('name', '?')}**\n"
        f"📖  __{char.get('anime', '?')}__\n"
        f"Rarity:  {tier_str}"
        f"{_sub_line(char)}\n"
        f"{'─' * 28}\n"
        f"{media_type}:  `{media_url}`\n"
        f"{'─' * 28}\n"
        f"📤  {char.get('mention', '?')}\n"
        f"🕒  `{char.get('added_at', '?')}`"
    )


# ──────────────────────────────────────────────────────────────────────────────
# /rarities
# ──────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("rarities") & uploader_filter)
async def cmd_rarities(_, message: Message):
    lines = ["📋 **Rarity Reference**\n\n**— Main Tiers —**"]
    for r in sorted(RARITIES.values(), key=lambda x: x.id):
        lines.append(
            f"  `{r.id:>2}`  {r.emoji}  **{r.display_name}**"
            f"  🌸 `{r.kakera_reward}`  Limit: `{r.drop_limit_per_day or '∞'}/day`"
        )

    lines.append("\n**— Sub-Rarities —**")
    _parent = {51: "Seasonal", 61: "Mythic", 62: "Mythic", 63: "Mythic", 71: "Eternal"}
    for r in sorted(SUB_RARITIES.values(), key=lambda x: x.id):
        tag = "  ⚠️ video only" if r.video_only else ""
        lines.append(
            f"  `{r.id:>2}`  {r.emoji}  **{r.display_name}**"
            f"  _(sub of {_parent.get(r.id, '?')})_{tag}"
        )

    for header, data in [
        ("Festival Seasons  `(id 51)`", FESTIVAL_SEASONS),
        ("Mythic Sports  `(id 62)`",    MYTHIC_SPORTS),
        ("Mythic Fantasy  `(id 63)`",   MYTHIC_FANTASY),
    ]:
        lines.append(f"\n**— {header} —**")
        for k, v in data.items():
            lines.append(f"  `{k}`  {v['emoji']}  {v['label']}")

    await message.reply_text("\n".join(lines))
