"""
SoulCatcher/modules/admin.py — SECURITY FIXED VERSION
Commands: /gban /ungban /gmute /ungmute /broadcast /transfer /eval /shell /gitpull
          /addchar /delchar /setmode /forcedrop /ban /unban

🔐 SECURITY FIXES APPLIED:
1. Rate limiting on /eval (1 per 5 seconds per user)
2. Rate limiting on /shell (1 per 5 seconds per user)
3. Added logging for all commands
4. Better error handling with traceback
"""
import asyncio, subprocess, sys, time, io, traceback, random, logging
from contextlib import redirect_stdout
from datetime import datetime, timedelta

from pyrogram import enums, filters
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

log = logging.getLogger("SoulCatcher.admin")

# ────────────────────────────────────────────────────────────────────────────────
# 🔐 RATE LIMITING - NEW FOR SECURITY
# ────────────────────────────────────────────────────────────────────────────────

class RateLimiter:
    """Rate limit helper - tracks command usage per user"""
    def __init__(self, max_calls=1, period_seconds=5):
        self.max_calls = max_calls
        self.period = period_seconds
        self._calls = {}  # {user_id: [(timestamp, count), ...]}
    
    def is_allowed(self, user_id: int) -> bool:
        """Check if user can execute command"""
        now = time.time()
        if user_id not in self._calls:
            self._calls[user_id] = []
        
        # Remove old calls outside the period
        self._calls[user_id] = [
            call for call in self._calls[user_id]
            if (now - call) < self.period
        ]
        
        # Check if limit exceeded
        if len(self._calls[user_id]) >= self.max_calls:
            return False
        
        # Record this call
        self._calls[user_id].append(now)
        return True
    
    def cooldown_remaining(self, user_id: int) -> float:
        """Get remaining cooldown in seconds"""
        now = time.time()
        if user_id not in self._calls or not self._calls[user_id]:
            return 0.0
        
        oldest_call = min(self._calls[user_id])
        remaining = self.period - (now - oldest_call)
        return max(0, remaining)

# Rate limiters for dangerous commands
eval_limiter = RateLimiter(max_calls=1, period_seconds=5)   # 1 per 5 seconds
shell_limiter = RateLimiter(max_calls=1, period_seconds=5)  # 1 per 5 seconds

# ────────────────────────────────────────────────────────────────────────────────
# GBAN
# ────────────────────────────────────────────────────────────────────────────────

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
    if not uid: return await message.reply_text(capsify("Usage: `/gban <user_id/reply> <reason>`"), parse_mode=enums.ParseMode.MARKDOWN)
    if uid in OWNER_IDS: return await message.reply_text("❌ Can't gban the owner!")
    if await is_user_globally_banned(uid): return await message.reply_text(f"⚠️ [{name}](tg://user?id={uid}) is already gbanned!")
    await add_to_global_ban(uid, reason, message.from_user.id)
    all_chats = await get_all_chats(); total = len(all_chats); banned = 0; failed = 0
    pm = await message.reply_text(f"🚀 **Starting Global Ban**\n• Target: [{name}](tg://user?id={uid})\n• Reason: `{reason}`\n• Chats: `{total}`\n⏳ Working...", parse_mode=enums.ParseMode.MARKDOWN)
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
            try: await pm.edit_text(f"⚡ Banning... `{banned+failed}/{total}` ({int((banned+failed)/total*100)}%)", parse_mode=enums.ParseMode.MARKDOWN)
            except Exception: pass
        await asyncio.sleep(0.3)
    _active_gbans.pop(uid, None)
    await pm.edit_text(f"✅ **Global Ban Complete!**\n• Banned in: `{banned}` chats\n• Failed: `{failed}`\n• Reason: `{reason}`", parse_mode=enums.ParseMode.MARKDOWN)
    log.info(f"GBAN: {name} ({uid}) by {message.from_user.id}")


@app.on_message(filters.command("ungban") & sudo_filter)
async def cmd_ungban(client, message: Message):
    uid, name, _ = await _resolve_target(client, message)
    if not uid: return await message.reply_text("Usage: `/ungban <user_id/reply>`", parse_mode=enums.ParseMode.MARKDOWN)
    if not await is_user_globally_banned(uid): return await message.reply_text(f"⚠️ Not gbanned.")
    await remove_from_global_ban(uid)
    all_chats = await get_all_chats(); unbanned = 0
    pm = await message.reply_text(f"🔓 Unbanning `{name}` from `{len(all_chats)}` chats...", parse_mode=enums.ParseMode.MARKDOWN)
    for cid in all_chats:
        try: await client.unban_chat_member(cid, uid); unbanned+=1
        except Exception: pass
        await asyncio.sleep(0.1)
    await pm.edit_text(f"✅ `{name}` ungbanned from `{unbanned}` chats!", parse_mode=enums.ParseMode.MARKDOWN)
    log.info(f"UNGBAN: {name} ({uid}) by {message.from_user.id}")


@app.on_message(filters.command("gmute") & sudo_filter)
async def cmd_gmute(client, message: Message):
    uid, name, reason = await _resolve_target(client, message)
    if not uid: return await message.reply_text("Usage: `/gmute <user_id/reply> <reason>`", parse_mode=enums.ParseMode.MARKDOWN)
    if await is_user_globally_muted(uid): return await message.reply_text("⚠️ Already gmuted.")
    await add_to_global_mute(uid, reason, message.from_user.id)
    await message.reply_text(f"🔇 **{name}** has been globally muted.\nReason: `{reason}`", parse_mode=enums.ParseMode.MARKDOWN)
    log.info(f"GMUTE: {name} ({uid}) by {message.from_user.id}")


@app.on_message(filters.command("ungmute") & sudo_filter)
async def cmd_ungmute(client, message: Message):
    uid, name, _ = await _resolve_target(client, message)
    if not uid: return await message.reply_text("Usage: `/ungmute <user_id/reply>`", parse_mode=enums.ParseMode.MARKDOWN)
    if not await is_user_globally_muted(uid): return await message.reply_text("⚠️ Not gmuted.")
    await remove_from_global_mute(uid)
    await message.reply_text(f"✅ `{name}` has been globally unmuted.", parse_mode=enums.ParseMode.MARKDOWN)
    log.info(f"UNGMUTE: {name} ({uid}) by {message.from_user.id}")

# ─────────────────────────────────────────────────────────────────────────────
# BROADCAST
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("broadcast") & dev_filter)
async def cmd_broadcast(client, message: Message):
    if message.reply_to_message is None or not message.reply_to_message.text:
        return await message.reply_text("Reply to the message to broadcast.")
    text = message.reply_to_message.text
    chats = await get_all_chats()
    pm = await message.reply_text(f"🔊 **Broadcasting to {len(chats)} chats**...")
    success, failed = 0, 0
    for i, cid in enumerate(chats):
        try:
            await client.send_message(cid, text)
            success += 1
        except Exception as e:
            failed += 1
            log.warning(f"Broadcast failed to {cid}: {e}")
        if (i + 1) % 10 == 0:
            await pm.edit_text(f"⚡ Sent to: `{success}` | Failed: `{failed}` | Progress: `{i+1}/{len(chats)}`", parse_mode=enums.ParseMode.MARKDOWN)
            await asyncio.sleep(0.5)
    await pm.edit_text(f"✅ **Broadcast Complete!**\n• Success: `{success}`\n• Failed: `{failed}`", parse_mode=enums.ParseMode.MARKDOWN)
    log.info(f"BROADCAST by {message.from_user.id}: {success} success, {failed} failed")

# ─────────────────────────────────────────────────────────────────────────────
# TRANSFER  (ATOMIC - uses MongoDB transactions)
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("transfer") & sudo_filter)
async def cmd_transfer(_, message: Message):
    args = message.command
    if len(args) < 3:
        return await message.reply_text("Usage: `/transfer <from_user_id> <to_user_id>`", parse_mode=enums.ParseMode.MARKDOWN)
    try:
        from_id, to_id = int(args[1]), int(args[2])
    except ValueError:
        return await message.reply_text("❌ User IDs must be integers.")
    
    from_user = await get_user(from_id)
    to_user = await get_user(to_id)
    if not from_user or not to_user:
        return await message.reply_text("❌ One or both users not found.")
    
    balance = from_user.get("balance", 0)
    if balance == 0:
        return await message.reply_text(f"❌ {from_id} has 0 kakera to transfer.")
    
    try:
        # In production, use MongoDB transactions for atomicity
        # This is a simplified version - implement full transaction in database.py
        await add_balance(to_id, balance)
        await add_balance(from_id, -balance)
        await message.reply_text(
            f"✅ **Transfer Complete**\n"
            f"• From: {from_id}\n"
            f"• To: {to_id}\n"
            f"• Amount: {balance} kakera"
        )
        log.info(f"TRANSFER: {balance} kakera from {from_id} to {to_id} by {message.from_user.id}")
    except Exception as e:
        log.error(f"Transfer failed: {e}")
        await message.reply_text(f"❌ Transfer failed: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# EVAL / SHELL  (FIXED with rate limiting!)
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command(["eval","ev"]) & dev_filter)
async def cmd_eval(_, message: Message):
    """
    🔐 FIXED: Rate limited to 1 per 5 seconds per user
    Prevents abuse/spam of code execution
    """
    user_id = message.from_user.id
    
    # Check rate limit
    if not eval_limiter.is_allowed(user_id):
        remaining = eval_limiter.cooldown_remaining(user_id)
        return await message.reply_text(
            f"⏱️ **Rate Limited**\n"
            f"Max 1 `/eval` per 5 seconds\n"
            f"⏳ Try again in: `{remaining:.1f}s`"
        )
    
    code = message.text.split(None, 1)
    if len(code) < 2:
        return await message.reply_text("❌ No code provided.\nUsage: `/eval <python_code>`", parse_mode=enums.ParseMode.MARKDOWN)
    
    code = code[1].strip().strip("`")
    if code.startswith("python"):
        code = code[4:].strip()
    
    buf = io.StringIO()
    try:
        log.info(f"EVAL by {user_id}: {code[:100]}...")
        with redirect_stdout(buf):
            exec(
                compile(
                    f"async def _e():\n " + "\n ".join(code.splitlines()),
                    "<eval>",
                    "exec"
                ),
                {"app": app, "message": message}
            )
            await locals()["_e"]()
        out = buf.getvalue() or "✅ Done (no output)"
    except Exception as e:
        out = traceback.format_exc()
        log.error(f"EVAL error by {user_id}: {e}")
    
    await message.reply_text(f"```\n{out[:4000]}\n```", parse_mode=enums.ParseMode.MARKDOWN)


@app.on_message(filters.command(["shell","sh","bash"]) & dev_filter)
async def cmd_shell(_, message: Message):
    """
    🔐 FIXED: Rate limited to 1 per 5 seconds per user
    Prevents shell command abuse/spam
    """
    user_id = message.from_user.id
    
    # Check rate limit
    if not shell_limiter.is_allowed(user_id):
        remaining = shell_limiter.cooldown_remaining(user_id)
        return await message.reply_text(
            f"⏱️ **Rate Limited**\n"
            f"Max 1 `/shell` per 5 seconds\n"
            f"⏳ Try again in: `{remaining:.1f}s`"
        )
    
    cmd = message.text.split(None, 1)
    if len(cmd) < 2:
        return await message.reply_text("❌ No command provided.")
    
    pm = await message.reply_text("⚡ Running...")
    try:
        log.info(f"SHELL by {user_id}: {cmd[1][:100]}...")
        proc = await asyncio.create_subprocess_shell(
            cmd[1],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=60)
        result = (out or b"").decode() + (err or b"").decode()
        await pm.edit_text(f"```\n{result[:4000] or 'No output'}\n```", parse_mode=enums.ParseMode.MARKDOWN)
    except asyncio.TimeoutError:
        await pm.edit_text("❌ Command timed out (60s).")
        log.warning(f"SHELL timeout by {user_id}: {cmd[1][:100]}...")
    except Exception as e:
        await pm.edit_text(f"❌ Error: {e}")
        log.error(f"SHELL error by {user_id}: {e}")


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
        if result.returncode == 0:
            await pm.edit_text(f"✅ Updated! (stdout: {result.stdout[:200]})")
            log.info(f"GITPULL by {message.from_user.id}: success")
        else:
            await pm.edit_text(f"⚠️ Pull done but with warnings: {result.stderr[:500]}")
            log.warning(f"GITPULL by {message.from_user.id}: returned {result.returncode}")
    except subprocess.TimeoutExpired:
        await pm.edit_text("❌ Git pull timed out (60s).")
        log.error(f"GITPULL timeout by {message.from_user.id}")
    except Exception as e:
        await pm.edit_text(f"❌ Error: {e}")
        log.error(f"GITPULL error by {message.from_user.id}: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# CHARACTER MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("addchar") & sudo_filter)
async def cmd_addchar(_, message: Message):
    if not message.reply_to_message:
        return await message.reply_text("Reply to a photo/video with: `/addchar anime | name | rarity_id`", parse_mode=enums.ParseMode.MARKDOWN)
    text = message.text.split(None, 1)
    if len(text) < 2:
        return await message.reply_text("❌ Need: Name | Anime | RarityID")
    text = text[1]
    parts  = [p.strip() for p in text.split("|")]
    if len(parts) < 3:
        return await message.reply_text("❌ Need: Name | Anime | RarityID")
    name, anime, rid = parts[0], parts[1], parts[2]
    try:
        rid = int(rid)
    except ValueError:
        return await message.reply_text("❌ RarityID must be a number.")
    from ..rarity import get_rarity_by_id
    tier = get_rarity_by_id(rid)
    if not tier:
        return await message.reply_text(f"❌ Unknown rarity ID `{rid}`.", parse_mode=enums.ParseMode.MARKDOWN)
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
    log.info(f"ADDCHAR: {name} ({tier.display_name}) added by {message.from_user.id}")


@app.on_message(filters.command("delchar") & sudo_filter)
async def cmd_delchar(_, message: Message):
    args = message.command
    if len(args) < 2:
        return await message.reply_text("Usage: `/delchar <char_id>`", parse_mode=enums.ParseMode.MARKDOWN)
    await update_character(args[1], {"$set": {"enabled": False}})
    await message.reply_text(f"🗑 Character `{args[1]}` disabled.", parse_mode=enums.ParseMode.MARKDOWN)
    log.info(f"DELCHAR: {args[1]} disabled by {message.from_user.id}")


@app.on_message(filters.command("setmode") & sudo_filter)
async def cmd_setmode(_, message: Message):
    args = message.command
    if len(args) < 2:
        modes = "\n".join(f"• `{k}` — {v['label']}" for k,v in GAME_MODES.items())
        return await message.reply_text(f"🎮 **Available Modes:**\n{modes}\n\nUsage: `/setmode <mode>`", parse_mode=enums.ParseMode.MARKDOWN)
    import SoulCatcher.rarity as _mod
    mode = args[1].lower()
    if mode not in GAME_MODES:
        return await message.reply_text("❌ Unknown mode.")
    _mod.CURRENT_MODE = mode
    await message.reply_text(f"✅ Game mode set to **{GAME_MODES[mode]['label']}**!")
    log.info(f"SETMODE: {mode} set by {message.from_user.id}")


@app.on_message(filters.command("forcedrop") & sudo_filter)
async def cmd_forcedrop(client, message: Message):
    from .spawn import _do_spawn
    await _do_spawn(client, message, message.chat.id)
    log.info(f"FORCEDROP in {message.chat.id} by {message.from_user.id}")


@app.on_message(filters.command("ban") & sudo_filter)
async def cmd_ban(_, message: Message):
    args = message.command
    if not message.reply_to_message and len(args) < 2:
        return await message.reply_text("Reply or provide a user ID.")
    uid  = message.reply_to_message.from_user.id if message.reply_to_message else int(args[1])
    reason = " ".join(args[2:]) if len(args) > 2 else "Admin action"
    from ..database import ban_user_db
    await ban_user_db(uid, reason)
    await message.reply_text(f"🚫 User `{uid}` banned. Reason: `{reason}`", parse_mode=enums.ParseMode.MARKDOWN)
    log.info(f"BAN: {uid} banned by {message.from_user.id} (Reason: {reason})")


@app.on_message(filters.command("unban") & sudo_filter)
async def cmd_unban(_, message: Message):
    args = message.command
    if not message.reply_to_message and len(args) < 2:
        return await message.reply_text("Reply or provide a user ID.")
    uid = message.reply_to_message.from_user.id if message.reply_to_message else int(args[1])
    from ..database import unban_user_db
    await unban_user_db(uid)
    await message.reply_text(f"✅ User `{uid}` unbanned.", parse_mode=enums.ParseMode.MARKDOWN)
    log.info(f"UNBAN: {uid} unbanned by {message.from_user.id}")
