"""SoulCatcher/modules/upload.py — Character upload, edit, delete (sudo/uploader only)."""
from __future__ import annotations

import logging
from datetime import datetime

import SoulCatcher as _soul
from pyrogram import filters
from pyrogram.types import Message

from SoulCatcher.database import (
    insert_character,
    get_character,
    update_character,
    delete_character,
    search_characters,
    count_characters,
)
from SoulCatcher.rarity import RARITIES, SUB_RARITIES, rarity_display, get_rarity

log = logging.getLogger("SoulCatcher.upload")

VALID_RARITIES = set(RARITIES) | set(SUB_RARITIES)


def _parse_rarity(name: str) -> str | None:
    name = name.lower().strip()
    aliases = {
        "c": "common", "r": "rare", "l": "cosmos", "e": "infernal",
        "s": "seasonal", "m": "mythic", "et": "eternal",
    }
    return aliases.get(name, name if name in VALID_RARITIES else None)


# ── /upload ───────────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command("upload") & (_soul.uploader_filter | _soul.sudo_filter))
async def upload_cmd(_, m: Message):
    """
    Usage (reply to photo/video):
      /upload <name> | <anime> | <rarity>
    OR positional:
      /upload <name> <anime> <rarity>
    """
    if not m.reply_to_message:
        await m.reply(
            "↩️ Reply to a **photo** or **video** with:\n"
            "`/upload <name> | <anime> | <rarity>`\n\n"
            "Example:\n`/upload Naruto Uzumaki | Naruto | rare`"
        )
        return

    raw  = m.text.split(maxsplit=1)
    args = raw[1].strip() if len(raw) > 1 else ""

    # Support pipe-separated or space-separated
    if "|" in args:
        parts = [p.strip() for p in args.split("|")]
    else:
        parts = args.split()

    if len(parts) < 3:
        await m.reply(
            "❌ Need: `name | anime | rarity`\n"
            f"Valid rarities: `{'`, `'.join(VALID_RARITIES)}`"
        )
        return

    name    = " ".join(parts[0].split()).title()
    anime   = " ".join(parts[1].split()).title()
    rarity  = _parse_rarity(parts[2])

    if not rarity:
        await m.reply(f"❌ Unknown rarity `{parts[2]}`.\nValid: `{'`, `'.join(VALID_RARITIES)}`")
        return

    reply    = m.reply_to_message
    img_url  = ""
    vid_url  = ""

    if reply.photo:
        img_url = reply.photo.file_id
    elif reply.video or reply.animation:
        media   = reply.video or reply.animation
        vid_url = media.file_id
    elif reply.document and reply.document.mime_type and "image" in reply.document.mime_type:
        img_url = reply.document.file_id
    else:
        await m.reply("❌ Reply must contain a photo, video, or GIF.")
        return

    char_doc = {
        "name":      name,
        "anime":     anime,
        "rarity":    rarity,
        "img_url":   img_url,
        "video_url": vid_url,
        "added_by":  m.from_user.id,
        "enabled":   True,
        "views":     0,
        "claims":    0,
    }

    char_id = await insert_character(char_doc)
    r_str   = rarity_display(rarity)

    await m.reply(
        f"✅ **Character uploaded!**\n\n"
        f"🆔 ID: `{char_id}`\n"
        f"👤 Name: **{name}**\n"
        f"📺 Anime: *{anime}*\n"
        f"✨ Rarity: {r_str}\n"
        f"🖼 Media: {'Video' if vid_url else 'Image'}"
    )

    # Log to upload channel
    from SoulCatcher.config import UPLOAD_CHANNEL_ID
    if UPLOAD_CHANNEL_ID:
        try:
            await _.send_message(
                UPLOAD_CHANNEL_ID,
                f"📤 **New Character** | By `{m.from_user.id}`\n"
                f"🆔 `{char_id}` | **{name}** | *{anime}* | {r_str}"
            )
        except Exception:
            pass


# ── /edit ─────────────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command("edit") & (_soul.uploader_filter | _soul.sudo_filter))
async def edit_cmd(_, m: Message):
    """
    /edit <charID> <field> <value>
    Fields: name, anime, rarity, img_url, video_url, enabled
    """
    parts = m.text.split(maxsplit=3)
    if len(parts) < 4:
        await m.reply(
            "Usage: `/edit <charID> <field> <value>`\n"
            "Fields: `name` `anime` `rarity` `img_url` `video_url` `enabled`"
        )
        return

    char_id = parts[1].zfill(4)
    field   = parts[2].lower()
    value   = parts[3].strip()

    ALLOWED = {"name", "anime", "rarity", "img_url", "video_url", "enabled"}
    if field not in ALLOWED:
        await m.reply(f"❌ Field must be one of: `{'`, `'.join(ALLOWED)}`")
        return

    char = await get_character(char_id)
    if not char:
        await m.reply(f"❌ No character with ID `{char_id}`.")
        return

    if field == "rarity":
        value = _parse_rarity(value)
        if not value:
            await m.reply(f"❌ Unknown rarity. Valid: `{'`, `'.join(VALID_RARITIES)}`")
            return
    elif field == "enabled":
        value = value.lower() in ("1", "true", "yes", "on")
    elif field in ("name", "anime"):
        value = value.title()

    await update_character(char_id, {"$set": {field: value}})
    await m.reply(f"✅ Character `{char_id}` — `{field}` updated to: **{value}**")


# ── /delete ───────────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command(["delete", "delchar"]) & _soul.sudo_filter)
async def delete_cmd(_, m: Message):
    parts = m.text.split()
    if len(parts) < 2:
        await m.reply("Usage: `/delete <charID>`")
        return

    char_id = parts[1].zfill(4)
    char    = await get_character(char_id)

    if not char:
        await m.reply(f"❌ No character with ID `{char_id}`.")
        return

    removed = await delete_character(char_id)
    if removed:
        await m.reply(f"🗑 Character `{char_id}` (**{char['name']}**) deleted.")
    else:
        await m.reply("❌ Deletion failed.")


# ── /charinfo ─────────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command(["charinfo", "char", "character"]))
async def charinfo_cmd(_, m: Message):
    parts = m.text.split()
    if len(parts) < 2:
        await m.reply("Usage: `/charinfo <charID>`")
        return

    char_id = parts[1].zfill(4)
    char    = await get_character(char_id)

    if not char:
        await m.reply(f"❌ No character with ID `{char_id}`.")
        return

    r_str    = rarity_display(char["rarity"])
    enabled  = "✅ Active" if char.get("enabled") else "❌ Disabled"
    added    = char.get("added_at", "?")
    if hasattr(added, "strftime"):
        added = added.strftime("%Y-%m-%d")

    text = (
        f"🎴 **Character Info**\n\n"
        f"🆔 ID: `{char['id']}`\n"
        f"👤 Name: **{char['name']}**\n"
        f"📺 Anime: *{char.get('anime','Unknown')}*\n"
        f"✨ Rarity: {r_str}\n"
        f"📊 Status: {enabled}\n"
        f"👁 Views: `{char.get('views', 0):,}`\n"
        f"🎯 Claims: `{char.get('claims', 0):,}`\n"
        f"📅 Added: `{added}`\n"
        f"🖼 Has image: `{'Yes' if char.get('img_url') else 'No'}`\n"
        f"🎬 Has video: `{'Yes' if char.get('video_url') else 'No'}`"
    )

    try:
        if char.get("video_url"):
            await m.reply_video(char["video_url"], caption=text)
        elif char.get("img_url"):
            await m.reply_photo(char["img_url"], caption=text)
        else:
            await m.reply(text)
    except Exception:
        await m.reply(text)


# ── /dbstats ──────────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command("dbstats") & _soul.sudo_filter)
async def dbstats_cmd(_, m: Message):
    active = await count_characters(enabled=True)
    total  = await count_characters(enabled=False)
    await m.reply(
        f"📦 **Character DB Stats**\n\n"
        f"✅ Active: `{active:,}`\n"
        f"📋 Total:  `{total:,}`\n"
        f"❌ Disabled: `{total - active:,}`"
    )


# ── /rarities ─────────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command("rarities"))
async def rarities_cmd(_, m: Message):
    from SoulCatcher.rarity import RARITY_LIST_TEXT
    await m.reply(f"✨ **Rarity Tiers**\n\n{RARITY_LIST_TEXT}")
