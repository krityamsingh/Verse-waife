"""SoulCatcher/modules/moderation.py — gban, gunban, gmute, gunmute, sudo, dev, uploader."""
from __future__ import annotations

import logging

import SoulCatcher as _soul
from pyrogram import filters
from pyrogram.types import Message

from SoulCatcher.database import (
    add_global_ban,
    remove_global_ban,
    is_globally_banned,
    get_all_gbanned,
    add_global_mute,
    remove_global_mute,
    is_globally_muted,
    add_sudo,
    remove_sudo,
    get_sudo_ids,
    add_dev,
    remove_dev,
    get_dev_ids,
    add_uploader,
    remove_uploader,
    get_uploader_ids,
    ban_user_db,
    unban_user_db,
)
from SoulCatcher import (
    refresh_sudo, refresh_dev, refresh_uploader,
    GBAN_HANDLER_GROUP, GMUTE_HANDLER_GROUP,
)

log = logging.getLogger("SoulCatcher.moderation")


def _get_target(m: Message) -> tuple[int | None, str]:
    if m.reply_to_message and m.reply_to_message.from_user:
        u = m.reply_to_message.from_user
        return u.id, u.first_name or str(u.id)
    parts = m.text.split()
    if len(parts) >= 2:
        arg = parts[1].lstrip("@")
        if arg.lstrip("-").isdigit():
            return int(arg), str(arg)
    return None, ""


def _get_reason(m: Message) -> str:
    parts = m.text.split(maxsplit=2)
    return parts[2] if len(parts) > 2 else "No reason given."


# ── Global Ban / Unban ────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command("gban") & _soul.sudo_filter)
async def gban_cmd(_, m: Message):
    uid, name = _get_target(m)
    if not uid:
        await m.reply("Reply to a user or provide their ID to gban.")
        return
    if _soul.is_sudo(uid):
        await m.reply("❌ Cannot gban a sudo/owner user.")
        return

    reason = _get_reason(m)
    await add_global_ban(uid, reason)
    await ban_user_db(uid, reason)
    await m.reply(f"🔨 **{name}** (`{uid}`) has been **globally banned**.\n📋 Reason: {reason}")


@_soul.app.on_message(filters.command("gunban") & _soul.sudo_filter)
async def gunban_cmd(_, m: Message):
    uid, name = _get_target(m)
    if not uid:
        await m.reply("Reply to a user or provide their ID to gunban.")
        return

    removed = await remove_global_ban(uid)
    await unban_user_db(uid)
    if removed:
        await m.reply(f"✅ **{name}** (`{uid}`) has been **globally unbanned**.")
    else:
        await m.reply(f"❌ `{uid}` was not globally banned.")


@_soul.app.on_message(filters.command("gbanned") & _soul.sudo_filter)
async def gbanned_list(_, m: Message):
    banned = await get_all_gbanned()
    if not banned:
        await m.reply("✅ No globally banned users.")
        return

    lines = [f"`{b['user_id']}` — {b.get('reason','?')}" for b in banned[:20]]
    await m.reply(f"🔨 **Globally Banned** ({len(banned)} total)\n\n" + "\n".join(lines))


# ── Global Mute / Unmute ──────────────────────────────────────────────────────

@_soul.app.on_message(filters.command("gmute") & _soul.sudo_filter)
async def gmute_cmd(_, m: Message):
    uid, name = _get_target(m)
    if not uid:
        await m.reply("Reply to a user or provide their ID to gmute.")
        return
    if _soul.is_sudo(uid):
        await m.reply("❌ Cannot gmute a sudo/owner user.")
        return

    reason = _get_reason(m)
    await add_global_mute(uid, reason)
    await m.reply(f"🔇 **{name}** (`{uid}`) has been **globally muted**.\n📋 Reason: {reason}")


@_soul.app.on_message(filters.command("gunmute") & _soul.sudo_filter)
async def gunmute_cmd(_, m: Message):
    uid, name = _get_target(m)
    if not uid:
        await m.reply("Reply to a user or provide their ID to gunmute.")
        return

    removed = await remove_global_mute(uid)
    if removed:
        await m.reply(f"✅ **{name}** (`{uid}`) has been **globally unmuted**.")
    else:
        await m.reply(f"❌ `{uid}` was not globally muted.")


# ── Gban / Gmute Watchers ─────────────────────────────────────────────────────

@_soul.app.on_message(filters.group, group=GBAN_HANDLER_GROUP)
async def gban_watcher(_, m: Message):
    if not m.from_user:
        return
    uid = m.from_user.id
    if await is_globally_banned(uid):
        try:
            await m.delete()
            await _.ban_chat_member(m.chat.id, uid)
        except Exception:
            pass


@_soul.app.on_message(filters.group, group=GMUTE_HANDLER_GROUP)
async def gmute_watcher(_, m: Message):
    if not m.from_user:
        return
    uid = m.from_user.id
    if await is_globally_muted(uid):
        try:
            await m.delete()
        except Exception:
            pass


# ── Sudo Management ───────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command("addsudo") & _soul.owner_filter)
async def addsudo_cmd(_, m: Message):
    uid, name = _get_target(m)
    if not uid:
        await m.reply("Reply to a user or provide their ID.")
        return
    await add_sudo(uid)
    refresh_sudo([uid])
    await m.reply(f"✅ **{name}** (`{uid}`) added to sudo list.")


@_soul.app.on_message(filters.command("removesudo") & _soul.owner_filter)
async def removesudo_cmd(_, m: Message):
    uid, name = _get_target(m)
    if not uid:
        await m.reply("Reply to a user or provide their ID.")
        return
    removed = await remove_sudo(uid)
    if removed:
        await m.reply(f"✅ **{name}** (`{uid}`) removed from sudo list.")
    else:
        await m.reply(f"❌ `{uid}` is not in the sudo list.")


@_soul.app.on_message(filters.command("sudolist") & _soul.sudo_filter)
async def sudolist_cmd(_, m: Message):
    ids = await get_sudo_ids()
    if not ids:
        await m.reply("No sudo users.")
        return
    await m.reply("👑 **Sudo Users:**\n\n" + "\n".join(f"• `{i}`" for i in ids))


# ── Dev Management ────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command("adddev") & _soul.owner_filter)
async def adddev_cmd(_, m: Message):
    uid, name = _get_target(m)
    if not uid:
        await m.reply("Reply to a user or provide their ID.")
        return
    await add_dev(uid)
    refresh_dev([uid])
    await m.reply(f"✅ **{name}** (`{uid}`) added to dev list.")


@_soul.app.on_message(filters.command("removedev") & _soul.owner_filter)
async def removedev_cmd(_, m: Message):
    uid, name = _get_target(m)
    if not uid:
        await m.reply("Reply to a user or provide their ID.")
        return
    removed = await remove_dev(uid)
    if removed:
        await m.reply(f"✅ **{name}** (`{uid}`) removed from dev list.")
    else:
        await m.reply(f"❌ `{uid}` is not in the dev list.")


# ── Uploader Management ───────────────────────────────────────────────────────

@_soul.app.on_message(filters.command("adduploader") & _soul.sudo_filter)
async def adduploader_cmd(_, m: Message):
    uid, name = _get_target(m)
    if not uid:
        await m.reply("Reply to a user or provide their ID.")
        return
    await add_uploader(uid)
    refresh_uploader([uid])
    await m.reply(f"✅ **{name}** (`{uid}`) can now upload characters.")


@_soul.app.on_message(filters.command("removeuploader") & _soul.sudo_filter)
async def removeuploader_cmd(_, m: Message):
    uid, name = _get_target(m)
    if not uid:
        await m.reply("Reply to a user or provide their ID.")
        return
    removed = await remove_uploader(uid)
    if removed:
        await m.reply(f"✅ **{name}** (`{uid}`) removed from uploader list.")
    else:
        await m.reply(f"❌ `{uid}` is not an uploader.")


@_soul.app.on_message(filters.command("uploaderlist") & _soul.sudo_filter)
async def uploaderlist_cmd(_, m: Message):
    ids = await get_uploader_ids()
    if not ids:
        await m.reply("No uploaders.")
        return
    await m.reply("📤 **Uploaders:**\n\n" + "\n".join(f"• `{i}`" for i in ids))
