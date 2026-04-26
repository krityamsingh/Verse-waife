"""SoulCatcher/modules/group_settings.py — Group-level configuration commands."""
from __future__ import annotations

import SoulCatcher as _soul
from pyrogram import filters
from pyrogram.types import Message

from SoulCatcher.database import get_group_settings, update_group_settings
from SoulCatcher.rarity import SPAWN_SETTINGS


def _is_admin():
    async def check(_, __, m: Message):
        if not m.from_user:
            return False
        if _soul.is_sudo(m.from_user.id):
            return True
        try:
            member = await _.get_chat_member(m.chat.id, m.from_user.id)
            return member.status in ("administrator", "creator")
        except Exception:
            return False
    return filters.create(check)


admin_filter = _is_admin()


@_soul.app.on_message(filters.command(["groupsettings", "gsettings"]) & filters.group & admin_filter)
async def group_settings_cmd(_, m: Message):
    gs      = await get_group_settings(m.chat.id)
    enabled = "✅ Enabled" if gs.get("spawn_enabled", True) else "❌ Disabled"
    freq    = gs.get("spawn_frequency", SPAWN_SETTINGS["messages_per_spawn"])
    annc    = "✅" if gs.get("announcement_mode", True) else "❌"

    await m.reply(
        f"⚙️ **Group Settings** — {m.chat.title}\n\n"
        f"🌸 Spawn: {enabled}\n"
        f"📊 Frequency: every `{freq}` messages\n"
        f"📢 Announcements: {annc}\n\n"
        "Commands:\n"
        "• `/spawnon` / `/spawnoff` — Toggle spawns\n"
        "• `/spawnfreq <n>` — Set spawn frequency (5–100)\n"
        "• `/announcement on|off` — Toggle spawn alerts"
    )


@_soul.app.on_message(filters.command("spawnon") & filters.group & admin_filter)
async def spawnon_cmd(_, m: Message):
    await update_group_settings(m.chat.id, {"spawn_enabled": True})
    await m.reply("✅ Character spawning **enabled** in this group.")


@_soul.app.on_message(filters.command("spawnoff") & filters.group & admin_filter)
async def spawnoff_cmd(_, m: Message):
    await update_group_settings(m.chat.id, {"spawn_enabled": False})
    await m.reply("❌ Character spawning **disabled** in this group.")


@_soul.app.on_message(filters.command("spawnfreq") & filters.group & admin_filter)
async def spawnfreq_cmd(_, m: Message):
    parts = m.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await m.reply("Usage: `/spawnfreq <5–100>`")
        return

    freq = max(5, min(100, int(parts[1])))
    await update_group_settings(m.chat.id, {"spawn_frequency": freq})
    await m.reply(f"✅ Spawn frequency set to every **{freq}** messages.")


@_soul.app.on_message(filters.command("announcement") & filters.group & admin_filter)
async def announcement_cmd(_, m: Message):
    parts = m.text.split()
    if len(parts) < 2 or parts[1].lower() not in ("on", "off"):
        await m.reply("Usage: `/announcement on|off`")
        return

    enabled = parts[1].lower() == "on"
    await update_group_settings(m.chat.id, {"announcement_mode": enabled})
    state = "enabled" if enabled else "disabled"
    await m.reply(f"📢 Announcement mode **{state}**.")
