"""
SoulCatcher/modules/autouploader.py
═══════════════════════════════════════════════════════════════════════
Full upload pipeline adapted from your autouploader__1_.py.
Updated rarity map: 7 tiers + 3 sub-rarities (Seasonal, Limited Ed., Cartoon)

COMMANDS (Owner + Uploaders):
  /upload               — Reply to photo/video:
                          /upload <anime> | <char> | <rarity_id>
  /il <rarity_id>       — Auto-detect name & anime from caption
  /uchar media  <id>    — Update character media
  /uchar rarity <id> <rarity_id>
  /uchar name   <id> <new_name>
  /uchar anime  <id> <new_anime>

RARITY IDs (from rarity.py):
   1  ⚫ Common
   2  🔵 Rare
   3  🌌 Cosmos
   4  🔥 Infernal
   5  💎 Crystal
   6  🔴 Mythic
   7  ✨ Eternal
  51  🌸 Seasonal       (Crystal sub)
  61  🔮 Limited Edition (Mythic sub)
  71  🎠 Cartoon        (Eternal sub — VIDEO ONLY)
═══════════════════════════════════════════════════════════════════════
"""

import asyncio, os, re, shutil, tempfile, time
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime

from pyrogram import filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait, MessageNotModified

from .. import app, uploader_filter
from ..config import UPLOAD_CHANNEL_ID, UPLOAD_GC_ID
from ..rarity import RARITY_ID_MAP, RARITY_LIST_TEXT, is_video_only, get_rarity_by_id
from ..database import (
    insert_character, get_character, update_character, is_uploader,
)

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


# ─────────────────────────────────────────────────────────────────────────────
# UPLOAD QUEUE
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class UploadJob:
    file_path:  str
    chat_id:    int
    char_name:  str
    anime:      str
    rarity:     str
    rarity_id:  int
    mention:    str
    is_video:   bool
    notify_msg: Message
    position:   int   = 0
    queued_at:  float = field(default_factory=time.time)


_queue:         asyncio.Queue     = asyncio.Queue()
_queue_list:    List[UploadJob]   = []
_worker_on:     bool              = False
_current_job:   Optional[UploadJob] = None


async def _worker(client):
    global _worker_on, _current_job, _queue_list
    _worker_on = True
    while True:
        try:
            job: UploadJob = await asyncio.wait_for(_queue.get(), timeout=30)
        except asyncio.TimeoutError:
            if _queue.empty():
                _worker_on = False; _current_job = None; return
            continue
        _current_job = job
        if job in _queue_list: _queue_list.remove(job)
        try:    await _process(client, job)
        except Exception as e:
            try: await job.notify_msg.reply_text(f"❌ Upload failed for `{job.char_name}`\n`{e}`")
            except Exception: pass
        finally: _current_job = None; _queue.task_done()


def _ensure_worker(client):
    global _worker_on
    if not _worker_on:
        asyncio.get_event_loop().create_task(_worker(client))
        _worker_on = True


async def _enqueue(client, job: UploadJob) -> int:
    _queue_list.append(job)
    job.position = len(_queue_list)
    await _queue.put(job)
    _ensure_worker(client)
    return job.position


def _bar(pct, w=20):
    filled = round(w*pct/100)
    return "▓"*filled + "░"*(w-filled)


# ─────────────────────────────────────────────────────────────────────────────
# CATBOX UPLOAD
# ─────────────────────────────────────────────────────────────────────────────

async def _upload_to_catbox(file_path: str) -> Optional[str]:
    if not HAS_REQUESTS: return None
    try:
        with open(file_path, "rb") as f:
            resp = requests.post(
                "https://catbox.moe/user/api.php",
                data={"reqtype":"fileupload"},
                files={"fileToUpload": f},
                timeout=120,
            )
        return resp.text.strip() if resp.status_code == 200 else None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# PROCESS JOB
# ─────────────────────────────────────────────────────────────────────────────

async def _process(client, job: UploadJob):
    tier = get_rarity_by_id(job.rarity_id)
    if not tier:
        await job.notify_msg.reply_text(f"❌ Unknown rarity ID `{job.rarity_id}`")
        return

    # Upload to catbox
    await job.notify_msg.reply_text(f"⏫ Uploading **{job.char_name}** to catbox...")
    url = await _upload_to_catbox(job.file_path)
    if not url:
        url = job.file_path  # fallback: use raw file_id

    # Save to DB
    doc = {
        "name":     job.char_name,
        "anime":    job.anime,
        "rarity":   tier.name,
        "img_url":  "" if job.is_video else url,
        "video_url": url if job.is_video else "",
        "mention":  job.mention,
        "added_at": datetime.utcnow(),
        "added_by": job.chat_id,
    }
    char_id = await insert_character(doc)

    # Post to upload channel
    caption = (
        f"✅ **Character Added!**\n\n"
        f"🆔 `{char_id}`\n"
        f"👤 **{job.char_name}**\n"
        f"📖 _{job.anime}_\n"
        f"{tier.emoji} **{tier.display_name}**\n"
        f"📤 Added by: {job.mention}"
        + (" _(VIDEO)_" if job.is_video else "")
    )

    if UPLOAD_CHANNEL_ID:
        try:
            if job.is_video:
                await client.send_video(UPLOAD_CHANNEL_ID, url, caption=caption)
            else:
                await client.send_photo(UPLOAD_CHANNEL_ID, url, caption=caption)
        except Exception:
            try: await client.send_message(UPLOAD_CHANNEL_ID, caption)
            except Exception: pass

    await job.notify_msg.reply_text(caption)

    # Cleanup
    try: os.remove(job.file_path)
    except Exception: pass


# ─────────────────────────────────────────────────────────────────────────────
# /upload COMMAND
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args(text: str):
    """Returns (anime, char_name, rarity_id) or raises ValueError."""
    for sep in ["|", "-"]:
        parts = [p.strip() for p in text.split(sep, 2)]
        if len(parts) == 3:
            anime, char_name, rid = parts
            return anime, char_name, int(rid)
    raise ValueError("Bad format")


@app.on_message(filters.command("upload") & uploader_filter)
async def cmd_upload(client, message: Message):
    if not message.reply_to_message:
        return await message.reply_text(
            "📸 **Reply to a photo or video and use:**\n"
            "`/upload <anime> | <char_name> | <rarity_id>`\n\n"
            f"**Rarity IDs:**\n{RARITY_LIST_TEXT}"
        )

    args_text = " ".join(message.command[1:]).strip()
    if not args_text:
        return await message.reply_text(
            f"Usage: `/upload anime | char | rarity_id`\n\n{RARITY_LIST_TEXT}")

    try:
        anime, char_name, rid = _parse_args(args_text)
    except (ValueError, IndexError):
        return await message.reply_text(f"❌ Parse error.\nFormat: `anime | name | rarity_id`\n\n{RARITY_LIST_TEXT}")

    tier = get_rarity_by_id(rid)
    if not tier:
        return await message.reply_text(f"❌ Unknown rarity ID `{rid}`.\n\n{RARITY_LIST_TEXT}")

    reply = message.reply_to_message
    is_vid = bool(reply.video or reply.animation)

    if tier.video_only and not is_vid:
        return await message.reply_text(
            f"❌ **{tier.display_name}** is VIDEO ONLY.\nPlease reply to a video/animation!")

    # Download media
    tmp = tempfile.mktemp(suffix=".mp4" if is_vid else ".jpg")
    await message.reply_text(f"⬇️ Downloading `{char_name}`...")
    try:
        file_path = await reply.download(tmp)
    except Exception as e:
        return await message.reply_text(f"❌ Download failed: `{e}`")

    mention  = f"[{message.from_user.first_name}](tg://user?id={message.from_user.id})"
    job = UploadJob(
        file_path=file_path,
        chat_id=message.chat.id,
        char_name=char_name,
        anime=anime,
        rarity=tier.name,
        rarity_id=rid,
        mention=mention,
        is_video=is_vid,
        notify_msg=message,
    )
    pos = await _enqueue(client, job)
    queue_len = _queue.qsize()
    await message.reply_text(
        f"📥 **Queued!** Position: `{pos}`\n"
        f"📦 Queue depth: `{queue_len}`\n"
        f"• **{char_name}** from _{anime}_\n"
        f"• Rarity: {tier.emoji} {tier.display_name}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# /il COMMAND  — auto-detect from spawn caption
# ─────────────────────────────────────────────────────────────────────────────

_IL_PATTERNS = [
    r"Name[:\s]*([^\n|]+)\s*[|\n]\s*Anime[:\s]*([^\n|]+)",
    r"Character[:\s]*([^\n|]+)\s*[|\n]\s*Anime[:\s]*([^\n|]+)",
    r"\*\*([^\*]+)\*\*\s*\n.*?_([^_]+)_",
    r"👤\s*\*\*([^\*]+)\*\*\s*\n.*?📖.*?_([^_\n]+)_",
    r"([A-Za-z][^\n|]{2,40})\s*[|\-]\s*([A-Za-z][^\n|]{2,40})",
]

def _detect_from_caption(caption: str):
    for pat in _IL_PATTERNS:
        m = re.search(pat, caption, re.IGNORECASE)
        if m:
            return m.group(1).strip(), m.group(2).strip()
    return None, None


@app.on_message(filters.command("il") & uploader_filter)
async def cmd_il(client, message: Message):
    if not message.reply_to_message:
        return await message.reply_text("Reply to a spawn message with `/il <rarity_id>`")
    args = message.command
    if len(args) < 2:
        return await message.reply_text(f"Usage: `/il <rarity_id>`\n\n{RARITY_LIST_TEXT}")
    try:
        rid = int(args[1])
    except ValueError:
        return await message.reply_text("❌ Rarity ID must be a number.")
    tier = get_rarity_by_id(rid)
    if not tier:
        return await message.reply_text(f"❌ Unknown rarity ID `{rid}`.")

    reply   = message.reply_to_message
    caption = reply.caption or reply.text or ""
    char_name, anime = _detect_from_caption(caption)
    if not char_name or not anime:
        return await message.reply_text(
            "❌ Could not auto-detect name & anime.\nUse `/upload anime | name | rarity_id` instead.")

    is_vid    = bool(reply.video or reply.animation)
    if tier.video_only and not is_vid:
        return await message.reply_text(f"❌ **{tier.display_name}** is VIDEO ONLY!")

    tmp = tempfile.mktemp(suffix=".mp4" if is_vid else ".jpg")
    try:
        file_path = await reply.download(tmp)
    except Exception as e:
        return await message.reply_text(f"❌ Download failed: `{e}`")

    mention = f"[{message.from_user.first_name}](tg://user?id={message.from_user.id})"
    job = UploadJob(
        file_path=file_path, chat_id=message.chat.id,
        char_name=char_name, anime=anime,
        rarity=tier.name, rarity_id=rid,
        mention=mention, is_video=is_vid, notify_msg=message,
    )
    pos = await _enqueue(client, job)
    await message.reply_text(
        f"🔍 **Auto-detected!**\n"
        f"• Name: **{char_name}**\n"
        f"• Anime: _{anime}_\n"
        f"• Rarity: {tier.emoji} {tier.display_name}\n"
        f"📥 Queue position: `{pos}`"
    )


# ─────────────────────────────────────────────────────────────────────────────
# /uchar COMMAND  — update existing character
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("uchar") & uploader_filter)
async def cmd_uchar(client, message: Message):
    args = message.command
    if len(args) < 3:
        return await message.reply_text(
            "**Update Character Usage:**\n"
            "`/uchar media <id>` — reply to new photo/video\n"
            "`/uchar rarity <id> <rarity_id>`\n"
            "`/uchar name <id> <new_name>`\n"
            "`/uchar anime <id> <new_anime>`"
        )

    sub_cmd = args[1].lower()
    char_id = args[2]

    char = await get_character(char_id)
    if not char:
        return await message.reply_text(f"❌ Character `{char_id}` not found.")

    if sub_cmd == "media":
        if not message.reply_to_message:
            return await message.reply_text("Reply to a photo/video with `/uchar media <id>`")
        reply = message.reply_to_message
        is_vid = bool(reply.video or reply.animation)
        tmp    = tempfile.mktemp(suffix=".mp4" if is_vid else ".jpg")
        file_path = await reply.download(tmp)
        url   = await _upload_to_catbox(file_path) or file_path
        try: os.remove(file_path)
        except Exception: pass
        if is_vid:  await update_character(char_id, {"$set": {"video_url": url, "img_url": ""}})
        else:       await update_character(char_id, {"$set": {"img_url": url, "video_url": ""}})
        await message.reply_text(f"✅ Media updated for `{char_id}` — **{char['name']}**")

    elif sub_cmd == "rarity":
        if len(args)<4: return await message.reply_text("Usage: `/uchar rarity <id> <rarity_id>`")
        try: rid = int(args[3])
        except ValueError: return await message.reply_text("❌ Invalid rarity ID.")
        tier = get_rarity_by_id(rid)
        if not tier: return await message.reply_text(f"❌ Unknown rarity ID `{rid}`.")
        await update_character(char_id, {"$set": {"rarity": tier.name}})
        await message.reply_text(f"✅ **{char['name']}** rarity → {tier.emoji} {tier.display_name}")

    elif sub_cmd == "name":
        if len(args)<4: return await message.reply_text("Usage: `/uchar name <id> <new_name>`")
        new_name = " ".join(args[3:])
        await update_character(char_id, {"$set": {"name": new_name}})
        await message.reply_text(f"✅ Name updated: **{char['name']}** → **{new_name}**")

    elif sub_cmd == "anime":
        if len(args)<4: return await message.reply_text("Usage: `/uchar anime <id> <new_anime>`")
        new_anime = " ".join(args[3:])
        await update_character(char_id, {"$set": {"anime": new_anime}})
        await message.reply_text(f"✅ Anime updated for **{char['name']}** → _{new_anime}_")

    else:
        await message.reply_text("❌ Unknown sub-command. Use: media / rarity / name / anime")
