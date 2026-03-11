"""
SoulCatcher/modules/admin.py
Adapted from your: gban.py, broadcast.py, trasnfer.py, eval.py, gitpull.py
Commands: /gban /ungban /gmute /ungmute /broadcast /transfer /eval /shell /gitpull
          /addchar /delchar /setmode /forcedrop /ban /unban
"""
import asyncio, subprocess, sys, time, io, traceback, random
from contextlib import redirect_stdout
from datetime import datetime, timedelta

from pyrogram import filters
from pyrogram.errors import PeerIdInvalid, FloodWait, ChatAdminRequired, UserPrivacyRestricted
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from .. import app, sudo_filter, dev_filter, owner_filter, capsify
from ..config import OWNER_IDS, GIT_REPO_URL, GIT_BRANCH
from ..database import (
    add_to_global_ban, remove_from_global_ban, is_user_globally_banned,
    fetch_globally_banned_users, get_all_chats,
    add_to_global_mute, remove_from_global_mute, is_user_globally_muted,
    fetch_globally_muted_users,
    get_user, get_all_user_ids, get_all_tracked_group_ids,
    get_all_harem, add_balance, get_balance,
    insert_character, get_character, update_character,
)
from ..rarity import RARITY_ID_MAP, RARITY_LIST_TEXT, GAME_MODES

# ─────────────────────────────────────────────────────────────────────────────
# GBAN
# ─────────────────────────────────────────────────────────────────────────────

_active_gbans  = {}
_active_gmutes = {}

async def _get_info(client, user_id):
    try: u = await client.get_users(user_id); return u.first_name, u.username
    except: return f"User-{user_id}", None

async def _resolve_target(client, message):
    if message.reply_to_message:
        u = message.reply_to_message.from_user
        return u.id, u.first_name, " ".join(message.command[1:]) or "No reason"
    try:
        uid    = int(message.command[1])
        name,_ = await _get_info(client, uid)
        reason = " ".join(message.command[2:]) or "No reason"
        return uid, name, reason
    except Exception:
        return None, None, None


@app.on_message(filters.command("gban") & sudo_filter)
async def cmd_gban(client, message: Message):
    uid, name, reason = await _resolve_target(client, message)
    if not uid: return await message.reply_text(capsify("Usage: `/gban <user_id/reply> <reason>`"))
    if uid in OWNER_IDS: return await message.reply_text("❌ Can't gban the owner!")
    if await is_user_globally_banned(uid): return await message.reply_text(f"⚠️ [{name}](tg://user?id={uid}) is already gbanned!")
    await add_to_global_ban(uid, reason, message.from_user.id)
    all_chats = await get_all_chats(); total = len(all_chats); banned = 0; failed = 0
    pm = await message.reply_text(f"🚀 **Starting Global Ban**\n• Target: [{name}](tg://user?id={uid})\n• Reason: `{reason}`\n• Chats: `{total}`\n⏳ Working...")
    _active_gbans[uid] = True
    for i in range(0, total, 10):
        if not _active_gbans.get(uid): break
        chunk = all_chats[i:i+10]
        tasks = [client.ban_chat_member(cid, uid, until_date=datetime.now()+timedelta(days=365)) for cid in chunk]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception): failed+=1
            else: banned+=1
        if i % 50 == 0:
            try: await pm.edit_text(f"⚡ Banning... `{banned+failed}/{total}` ({int((banned+failed)/total*100)}%)")
            except Exception: pass
        await asyncio.sleep(0.3)
    _active_gbans.pop(uid, None)
    await pm.edit_text(f"✅ **Global Ban Complete!**\n• Banned in: `{banned}` chats\n• Failed: `{failed}`\n• Reason: `{reason}`")


@app.on_message(filters.command("ungban") & sudo_filter)
async def cmd_ungban(client, message: Message):
    uid, name, _ = await _resolve_target(client, message)
    if not uid: return await message.reply_text("Usage: `/ungban <user_id/reply>`")
    if not await is_user_globally_banned(uid): return await message.reply_text(f"⚠️ Not gbanned.")
    await remove_from_global_ban(uid)
    all_chats = await get_all_chats(); unbanned = 0
    pm = await message.reply_text(f"🔓 Unbanning `{name}` from `{len(all_chats)}` chats...")
    for cid in all_chats:
        try: await client.unban_chat_member(cid, uid); unbanned+=1
        except Exception: pass
        await asyncio.sleep(0.1)
    await pm.edit_text(f"✅ `{name}` ungbanned from `{unbanned}` chats!")


@app.on_message(filters.command("gmute") & sudo_filter)
async def cmd_gmute(client, message: Message):
    uid, name, reason = await _resolve_target(client, message)
    if not uid: return await message.reply_text("Usage: `/gmute <user_id/reply> <reason>`")
    if await is_user_globally_muted(uid): return await message.reply_text("⚠️ Already gmuted.")
    await add_to_global_mute(uid, reason, message.from_user.id)
    await message.reply_text(f"🔇 **{name}** has been globally muted.\nReason: `{reason}`")


@app.on_message(filters.command("ungmute") & sudo_filter)
async def cmd_ungmute(client, message: Message):
    uid, name, _ = await _resolve_target(client, message)
    if not uid: return await message.reply_text("Usage: `/ungmute <user_id/reply>`")
    await remove_from_global_mute(uid)
    await message.reply_text(f"🔊 `{name}` is no longer muted.")


# Message watcher for gmute enforcement
@app.on_message(filters.group, group=2)
async def gmute_watcher(client, message: Message):
    if message.from_user and await is_user_globally_muted(message.from_user.id):
        try: await message.delete()
        except Exception: pass


# ─────────────────────────────────────────────────────────────────────────────
# BROADCAST
# ─────────────────────────────────────────────────────────────────────────────

_bc_active    = False
_bc_cancelled = False


@app.on_message(filters.command("broadcast") & dev_filter)
async def cmd_broadcast(_, message: Message):
    global _bc_active, _bc_cancelled
    if not message.reply_to_message:
        return await message.reply_text("❌ Reply to a message to broadcast it.")
    if _bc_active:
        return await message.reply_text("⚠️ Broadcast already running!")
    _bc_cancelled = False; _bc_active = True
    users  = await get_all_user_ids()
    groups = await get_all_tracked_group_ids()
    all_chats = list(set(users+groups))
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Stop", callback_data="bc:cancel")]])
    pm = await message.reply_text(f"📢 **Broadcast started!** ({len(all_chats)} chats)", reply_markup=kb)
    sent = failed = 0
    for cid in all_chats:
        if _bc_cancelled: break
        ok = await _bc_send(cid, message.reply_to_message)
        if ok: sent+=1
        else:  failed+=1
        await asyncio.sleep(0.5)
    _bc_active = False
    status = "🚫 Stopped" if _bc_cancelled else "✅ Done"
    await pm.edit_text(f"{status}\n📩 Sent: `{sent}` | ⚠️ Failed: `{failed}`")


@app.on_callback_query(filters.regex("^bc:cancel$"))
async def bc_cancel(_, cb):
    global _bc_cancelled
    _bc_cancelled = True
    await cb.answer("Stopping broadcast...")


async def _bc_send(cid, msg):
    try:
        fwd = await msg.forward(cid)
        if str(cid).startswith("-"):
            try: await app.pin_chat_message(cid, fwd.id, disable_notification=True)
            except Exception: pass
        return True
    except (PeerIdInvalid, ChatAdminRequired, UserPrivacyRestricted): return False
    except FloodWait as e: await asyncio.sleep(e.value); return await _bc_send(cid, msg)
    except Exception: return False


# ─────────────────────────────────────────────────────────────────────────────
# TRANSFER  (sudo admin forced transfer of all assets)
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("transfer") & sudo_filter)
async def cmd_transfer(_, message: Message):
    args = message.command
    if len(args) != 3:
        return await message.reply_text("**Usage**: `/transfer <sender_id> <receiver_id>`")
    try:
        sid, rid = int(args[1]), int(args[2])
    except ValueError:
        return await message.reply_text("❌ Invalid IDs.")
    if sid == rid: return await message.reply_text("❌ Same user!")

    s_doc = await get_user(sid); r_doc = await get_user(rid)
    if not s_doc: return await message.reply_text(f"❌ Sender `{sid}` not found.")
    if not r_doc: return await message.reply_text(f"❌ Receiver `{rid}` not found.")

    s_chars = await get_all_harem(sid)
    s_bal   = await get_balance(sid)
    s_name  = s_doc.get("first_name", f"User{sid}")
    r_name  = r_doc.get("first_name", f"User{rid}")

    pm = await message.reply_text(
        f"⚠️ **Transfer Confirmation**\n\n"
        f"From: **{s_name}** `{sid}`\nTo: **{r_name}** `{rid}`\n\n"
        f"• Characters: `{len(s_chars)}`\n"
        f"• Kakera: `{s_bal:,}`\n\nTransferring all assets..."
    )

    from ..database import _col
    transferred = 0
    for char in s_chars:
        await _col("user_characters").update_one(
            {"instance_id": char["instance_id"]}, {"$set": {"user_id": rid}})
        transferred += 1

    fee = max(0, int(s_bal * 0.02))  # 2% fee, just removed
    net = s_bal - fee
    await _col("users").update_one({"user_id": sid}, {"$set": {"balance": 0}})
    await add_balance(rid, net)

    await pm.edit_text(
        f"✅ **Transfer Complete!**\n\n"
        f"• Characters moved: `{transferred}`\n"
        f"• Kakera moved: `{net:,}` (fee: `{fee:,}`)\n"
        f"• From: **{s_name}** → **{r_name}**"
    )


# ─────────────────────────────────────────────────────────────────────────────
# EVAL / SHELL  (from eval.py)
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command(["eval","ev"]) & dev_filter)
async def cmd_eval(_, message: Message):
    code = message.text.split(None,1)
    if len(code)<2: return await message.reply_text("❌ No code provided.")
    code = code[1].strip().strip("`")
    if code.startswith("python"): code = code[4:].strip()
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            exec(compile(f"async def _e():\n " + "\n ".join(code.splitlines()), "<eval>", "exec"), {"app": app, "message": message})
            await locals()["_e"]()
        out = buf.getvalue() or "✅ Done (no output)"
    except Exception:
        out = traceback.format_exc()
    await message.reply_text(f"```\n{out[:4000]}\n```")


@app.on_message(filters.command(["shell","sh","bash"]) & dev_filter)
async def cmd_shell(_, message: Message):
    cmd = message.text.split(None,1)
    if len(cmd)<2: return await message.reply_text("❌ No command.")
    pm = await message.reply_text("⚡ Running...")
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd[1], stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), timeout=60)
        result   = (out or b"").decode() + (err or b"").decode()
        await pm.edit_text(f"```\n{result[:4000] or 'No output'}\n```")
    except asyncio.TimeoutError:
        await pm.edit_text("❌ Command timed out (60s).")
    except Exception as e:
        await pm.edit_text(f"❌ Error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# GIT PULL  (from gitpull.py)
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command(["gitpull","update"]) & dev_filter)
async def cmd_gitpull(_, message: Message):
    pm = await message.reply_text("🔄 Pulling latest code...")
    try:
        repo  = GIT_REPO_URL or "origin"
        result= subprocess.run(
            ["git","pull",repo,GIT_BRANCH],
            capture_output=True, text=True, timeout=60)
        out   = result.stdout + result.stderr
        if result.returncode == 0:
            await pm.edit_text(f"✅ **Pull successful!**\n```\n{out[:3000]}\n```\nRestarting...")
            asyncio.create_task(_delayed_restart())
        else:
            await pm.edit_text(f"❌ **Pull failed!**\n```\n{out[:3000]}\n```")
    except Exception as e:
        await pm.edit_text(f"❌ Git error: `{e}`")

async def _delayed_restart():
    await asyncio.sleep(2)
    subprocess.Popen([sys.executable, "-m", "SoulCatcher"])
    sys.exit(0)


# ─────────────────────────────────────────────────────────────────────────────
# CHARACTER ADMIN
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("addchar") & sudo_filter)
async def cmd_addchar(_, message: Message):
    """Add a character manually: /addchar name | anime | rarity_id"""
    text = " ".join(message.command[1:])
    if "|" not in text:
        return await message.reply_text(
            "Usage: `/addchar Name | Anime | RarityID`\n\n"
            f"**Rarity IDs:**\n{RARITY_LIST_TEXT}\n\n"
            "Reply to an image/video to attach media."
        )
    parts  = [p.strip() for p in text.split("|")]
    if len(parts)<3: return await message.reply_text("❌ Need: Name | Anime | RarityID")
    name, anime, rid = parts[0], parts[1], parts[2]
    try: rid = int(rid)
    except ValueError: return await message.reply_text("❌ RarityID must be a number.")
    from ..rarity import get_rarity_by_id
    tier = get_rarity_by_id(rid)
    if not tier: return await message.reply_text(f"❌ Unknown rarity ID `{rid}`.")
    if tier.video_only and not (message.reply_to_message and message.reply_to_message.video):
        return await message.reply_text(f"❌ Rarity **{tier.display_name}** is VIDEO ONLY. Reply to a video!")
    doc = {
        "name": name, "anime": anime, "rarity": tier.name,
        "img_url": "", "video_url": "", "added_by": message.from_user.id,
    }
    if message.reply_to_message:
        if message.reply_to_message.photo:
            doc["img_url"] = message.reply_to_message.photo.file_id
        elif message.reply_to_message.video:
            doc["video_url"] = message.reply_to_message.video.file_id
    char_id = await insert_character(doc)
    await message.reply_text(
        f"✅ **Character Added!**\n\n"
        f"🆔 `{char_id}`\n👤 **{name}**\n📖 _{anime}_\n{tier.emoji} **{tier.display_name}**"
    )


@app.on_message(filters.command("delchar") & sudo_filter)
async def cmd_delchar(_, message: Message):
    args = message.command
    if len(args)<2: return await message.reply_text("Usage: `/delchar <char_id>`")
    await update_character(args[1], {"$set": {"enabled": False}})
    await message.reply_text(f"🗑 Character `{args[1]}` disabled.")


@app.on_message(filters.command("setmode") & sudo_filter)
async def cmd_setmode(_, message: Message):
    args = message.command
    if len(args)<2:
        modes = "\n".join(f"• `{k}` — {v['label']}" for k,v in GAME_MODES.items())
        return await message.reply_text(f"🎮 **Available Modes:**\n{modes}\n\nUsage: `/setmode <name>`")
    import SoulCatcher.rarity as _mod
    mode = args[1].lower()
    if mode not in GAME_MODES: return await message.reply_text("❌ Unknown mode.")
    _mod.CURRENT_MODE = mode
    await message.reply_text(f"✅ Game mode set to **{GAME_MODES[mode]['label']}**!")


@app.on_message(filters.command("forcedrop") & sudo_filter)
async def cmd_forcedrop(client, message: Message):
    from .spawn import _do_spawn
    await _do_spawn(client, message, message.chat.id)


@app.on_message(filters.command("ban") & sudo_filter)
async def cmd_ban(_, message: Message):
    args = message.command
    if not message.reply_to_message and len(args)<2:
        return await message.reply_text("Reply or provide a user ID.")
    uid  = message.reply_to_message.from_user.id if message.reply_to_message else int(args[1])
    reason = " ".join(args[2:]) if len(args)>2 else "Admin action"
    from ..database import ban_user_db
    await ban_user_db(uid, reason)
    await message.reply_text(f"🚫 User `{uid}` banned. Reason: `{reason}`")


@app.on_message(filters.command("unban") & sudo_filter)
async def cmd_unban(_, message: Message):
    args = message.command
    if not message.reply_to_message and len(args)<2:
        return await message.reply_text("Reply or provide a user ID.")
    uid = message.reply_to_message.from_user.id if message.reply_to_message else int(args[1])
    from ..database import unban_user_db
    await unban_user_db(uid)
    await message.reply_text(f"✅ User `{uid}` unbanned.")
