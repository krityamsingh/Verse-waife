"""SoulCatcher/modules/dev.py — Owner/dev-only system commands."""
from __future__ import annotations

import asyncio
import importlib
import logging
import os
import sys
import time

import SoulCatcher as _soul
from pyrogram import filters
from pyrogram.types import Message

log = logging.getLogger("SoulCatcher.dev")

START_TIME = time.time()


def _fmt_uptime(seconds: float) -> str:
    d, r = divmod(int(seconds), 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


@_soul.app.on_message(filters.command(["ping", "alive"]))
async def ping_cmd(_, m: Message):
    start  = time.time()
    msg    = await m.reply("🏓 Pong!")
    latency = round((time.time() - start) * 1000, 2)
    uptime  = _fmt_uptime(time.time() - START_TIME)
    await msg.edit_text(
        f"🏓 **Pong!**\n"
        f"⚡ Latency: `{latency}ms`\n"
        f"⏱ Uptime: `{uptime}`"
    )


@_soul.app.on_message(filters.command("uptime") & _soul.sudo_filter)
async def uptime_cmd(_, m: Message):
    await m.reply(f"⏱ **Uptime:** `{_fmt_uptime(time.time() - START_TIME)}`")


@_soul.app.on_message(filters.command(["restart", "reboot"]) & _soul.owner_filter)
async def restart_cmd(_, m: Message):
    await m.reply("🔄 Restarting...")
    log.info("Restart requested by %d", m.from_user.id)
    os.execv(sys.executable, [sys.executable] + sys.argv)


@_soul.app.on_message(filters.command("reload") & _soul.dev_filter)
async def reload_cmd(_, m: Message):
    parts = m.text.split()
    if len(parts) < 2:
        await m.reply("Usage: `/reload <module_name>`")
        return

    mod_name  = parts[1]
    full_name = f"SoulCatcher.modules.{mod_name}"

    if full_name not in sys.modules:
        await m.reply(f"❌ Module `{mod_name}` is not loaded.")
        return

    try:
        importlib.reload(sys.modules[full_name])
        await m.reply(f"✅ Module `{mod_name}` reloaded.")
    except Exception as exc:
        await m.reply(f"❌ Reload failed: `{exc}`")


@_soul.app.on_message(filters.command("shell") & _soul.owner_filter)
async def shell_cmd(_, m: Message):
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2:
        await m.reply("Usage: `/shell <command>`")
        return

    cmd = parts[1]
    msg = await m.reply(f"⏳ Running: `{cmd}`")

    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        out = (stdout.decode() + stderr.decode()).strip()[:3000]
    except asyncio.TimeoutError:
        out = "⏰ Timed out (30s)"
    except Exception as exc:
        out = str(exc)

    await msg.edit_text(f"```\n{out or '(no output)'}\n```")


@_soul.app.on_message(filters.command(["sysinfo", "system"]) & _soul.sudo_filter)
async def sysinfo_cmd(_, m: Message):
    import platform
    py_ver = platform.python_version()
    os_ver = platform.system()
    arch   = platform.machine()

    try:
        import psutil
        ram      = psutil.virtual_memory()
        cpu_pct  = psutil.cpu_percent(interval=0.5)
        ram_text = f"{ram.used // 1024 // 1024}MB / {ram.total // 1024 // 1024}MB ({ram.percent}%)"
        cpu_text = f"{cpu_pct}%"
    except ImportError:
        ram_text = "N/A (psutil not installed)"
        cpu_text = "N/A"

    from SoulCatcher.config import BOT_VERSION
    await m.reply(
        f"🖥 **System Info**\n\n"
        f"🤖 Bot Version:  `{BOT_VERSION}`\n"
        f"🐍 Python:       `{py_ver}`\n"
        f"💻 OS:           `{os_ver} {arch}`\n"
        f"⏱ Uptime:       `{_fmt_uptime(time.time() - START_TIME)}`\n"
        f"🧠 RAM:          `{ram_text}`\n"
        f"⚙️  CPU:          `{cpu_text}`"
    )


@_soul.app.on_message(filters.command(["setmode", "gamemode"]) & _soul.sudo_filter)
async def setmode_cmd(_, m: Message):
    from SoulCatcher.rarity import GAME_MODES
    import SoulCatcher.rarity as rmod

    parts = m.text.split()
    if len(parts) < 2:
        modes = "\n".join(f"• `{k}` — {v['label']}" for k, v in GAME_MODES.items())
        await m.reply(f"Usage: `/setmode <mode>`\n\nAvailable modes:\n{modes}")
        return

    mode = parts[1].lower()
    if mode not in GAME_MODES:
        await m.reply(f"❌ Unknown mode `{mode}`.")
        return

    rmod.CURRENT_MODE = mode
    await m.reply(f"✅ Game mode set to **{GAME_MODES[mode]['label']}**")


@_soul.app.on_message(filters.command("cleanspawns") & _soul.sudo_filter)
async def clean_spawns_cmd(_, m: Message):
    from SoulCatcher.database import delete_expired_spawns
    count = await delete_expired_spawns()
    await m.reply(f"🧹 Cleaned `{count}` expired spawns.")
