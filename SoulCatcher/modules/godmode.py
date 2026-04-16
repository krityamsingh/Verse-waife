"""SoulCatcher/modules/reloader.py

/godmode reload
    — Scans every enabled character in the DB, finds those with broken or
      non-Catbox img_url / video_url, downloads the media from Telegram
      (using the file_id stored in a dump channel), uploads it to Catbox
      anonymously, and patches the DB with the new stable Catbox URL.

/godmode status
    — Shows how many characters have working Catbox URLs vs broken/raw ones.

/godmode fix <id>
    — Re-processes a single character (force mode).

/godmode reload --force
    — Re-uploads ALL characters, even those already on Catbox.

Only SUDO_USERS / OWNER_ID can invoke these commands.
"""

from __future__ import annotations

import asyncio
import logging
import time
from io import BytesIO

import aiohttp
from pyrogram import filters
from pyrogram.errors import FloodWait, MessageNotModified, MessageIdInvalid
from pyrogram.types import Message

from .. import app
from ..database import _col, get_character

log = logging.getLogger("SoulCatcher.reloader")

# ─── hardcoded authorised users ──────────────────────────────────────────────
OWNER_ID   = 6118760915
SUDO_USERS = {6118760915}
# ─────────────────────────────────────────────────────────────────────────────

# ─── tunables ────────────────────────────────────────────────────────────────
DUMP_CHANNEL     = -1003869604435
CATBOX_URL       = "https://catbox.moe/user/api.php"
DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=60, connect=15)
UPLOAD_TIMEOUT   = aiohttp.ClientTimeout(total=120, connect=15)
MAX_RETRIES      = 3
CONCURRENCY      = 4
PROGRESS_EVERY   = 10
# ─────────────────────────────────────────────────────────────────────────────


# ═════════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════════

def _is_sudo(uid: int) -> bool:
    return uid == OWNER_ID or uid in SUDO_USERS


def _is_working_catbox(url: str) -> bool:
    return url.startswith("https://files.catbox.moe/")


def _is_telegram_file_id(value: str) -> bool:
    return not value.startswith("http") and len(value) > 20


class ReloadError(Exception):
    pass


# ═════════════════════════════════════════════════════════════════════════════
# safe_edit — rate-limit-aware message editor
# ═════════════════════════════════════════════════════════════════════════════

# Tracks (last_text, last_edit_timestamp) per message_id
_edit_state: dict[int, tuple[str, float]] = {}

MIN_EDIT_INTERVAL = 3.5   # seconds between edits on the same message (Telegram allows ~20/min)
MAX_MSG_LEN       = 4096  # Telegram hard limit

async def safe_edit(msg: Message, text: str) -> None:
    """
    Edit *msg* with *text*, but:
    • Truncate to Telegram's 4096-char limit automatically.
    • Skip the edit if the text hasn't changed.
    • Throttle: never edit the same message faster than MIN_EDIT_INTERVAL seconds.
    • Absorb MessageNotModified / MessageIdInvalid silently.
    • On FloodWait: sleep the required time, then retry once.
    """
    # Truncate safely (never crash on long failure lists)
    if len(text) > MAX_MSG_LEN:
        cutoff = text.rfind("\n", 0, MAX_MSG_LEN - 60)
        if cutoff == -1:
            cutoff = MAX_MSG_LEN - 60
        text = text[:cutoff] + "\n\n…_(truncated — check logs for full list)_"

    mid = msg.id
    now = time.monotonic()
    last_text, last_ts = _edit_state.get(mid, ("", 0.0))

    # Skip if nothing changed
    if text == last_text:
        return

    # Throttle: wait out the remaining cooldown
    wait_for = MIN_EDIT_INTERVAL - (now - last_ts)
    if wait_for > 0:
        await asyncio.sleep(wait_for)

    for attempt in range(2):   # try twice (once after FloodWait)
        try:
            await msg.edit_text(text)
            _edit_state[mid] = (text, time.monotonic())
            return
        except FloodWait as fw:
            if attempt == 0:
                log.warning(f"[safe_edit] FloodWait {fw.value}s on msg {mid}")
                await asyncio.sleep(fw.value + 1)
            else:
                log.error(f"[safe_edit] FloodWait again on msg {mid}, giving up")
                return
        except (MessageNotModified, MessageIdInvalid):
            # Not modified = text was already that value; Invalid = message deleted
            _edit_state[mid] = (text, time.monotonic())
            return
        except Exception as e:
            log.warning(f"[safe_edit] msg {mid}: {e}")
            return


# ═════════════════════════════════════════════════════════════════════════════
# Step 1 — Download from Telegram via file_id
# ═════════════════════════════════════════════════════════════════════════════

async def _download_from_telegram(file_id: str) -> tuple[bytes, str]:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            buf = BytesIO()
            await app.download_media(file_id, in_memory=True, file_name=buf)
            buf.seek(0)
            data = buf.read()
            if not data:
                raise ReloadError(f"Empty download for file_id {file_id[:20]}…")
            ext = "mp4" if file_id.startswith(("BAA", "BQA")) else "jpg"
            return data, ext
        except FloodWait as fw:
            log.warning(f"[tg-dl] FloodWait {fw.value}s")
            await asyncio.sleep(fw.value + 1)
        except ReloadError:
            raise
        except Exception as e:
            log.warning(f"[tg-dl] attempt {attempt}/{MAX_RETRIES}: {e}")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(2 ** attempt)
    raise ReloadError(f"Telegram download failed after {MAX_RETRIES} attempts")


# ═════════════════════════════════════════════════════════════════════════════
# Step 2 — HTTP fallback download
# ═════════════════════════════════════════════════════════════════════════════

async def _http_download(session: aiohttp.ClientSession, url: str) -> bytes:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with session.get(url, timeout=DOWNLOAD_TIMEOUT) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    if data:
                        return data
                    raise ReloadError(f"Empty body from {url}")
                raise ReloadError(f"HTTP {resp.status} from {url}")
        except ReloadError:
            raise
        except asyncio.TimeoutError:
            log.warning(f"[http-dl] timeout attempt {attempt}/{MAX_RETRIES}: {url}")
        except Exception as e:
            log.warning(f"[http-dl] attempt {attempt}/{MAX_RETRIES}: {e}")
        if attempt < MAX_RETRIES:
            await asyncio.sleep(2 ** attempt)
    raise ReloadError(f"HTTP download failed after {MAX_RETRIES} attempts: {url}")


# ═════════════════════════════════════════════════════════════════════════════
# Step 3 — Upload to Catbox (anonymous)
# ═════════════════════════════════════════════════════════════════════════════

def _build_catbox_form(data: bytes, filename: str) -> aiohttp.FormData:
    form = aiohttp.FormData()
    form.add_field("reqtype", "fileupload")
    form.add_field("userhash", "")
    form.add_field(
        "fileToUpload",
        BytesIO(data),
        filename=filename,
        content_type="application/octet-stream",
    )
    return form


async def _upload_to_catbox(
    session: aiohttp.ClientSession,
    data: bytes,
    filename: str,
) -> str:
    for attempt in range(1, MAX_RETRIES + 1):
        form = _build_catbox_form(data, filename)
        try:
            async with session.post(CATBOX_URL, data=form, timeout=UPLOAD_TIMEOUT) as resp:
                text = (await resp.text()).strip()
                if resp.status == 200 and text.startswith("https://"):
                    log.info(f"[catbox] uploaded → {text}")
                    return text
                raise ReloadError(f"Catbox returned HTTP {resp.status}: {text[:120]}")
        except ReloadError:
            raise
        except asyncio.TimeoutError:
            log.warning(f"[catbox] timeout attempt {attempt}/{MAX_RETRIES}")
        except Exception as e:
            log.warning(f"[catbox] attempt {attempt}/{MAX_RETRIES}: {e}")
        if attempt < MAX_RETRIES:
            await asyncio.sleep(2 ** attempt)
    raise ReloadError(f"Catbox upload failed after {MAX_RETRIES} attempts for {filename}")


# ═════════════════════════════════════════════════════════════════════════════
# Per-character processor
# ═════════════════════════════════════════════════════════════════════════════

class ReloadResult:
    __slots__ = ("char_id", "success", "skipped", "error")

    def __init__(self, char_id: str, *, success: bool = False, skipped: bool = False, error: str = ""):
        self.char_id = char_id
        self.success = success
        self.skipped = skipped
        self.error   = error


async def _process_one(
    session: aiohttp.ClientSession,
    char: dict,
    force: bool = False,
) -> ReloadResult:
    char_id   = str(char["id"])
    safe_name = f"{char_id}_{char.get('name', 'char').replace(' ', '_')}"

    img_url = (char.get("img_url")   or "").strip()
    vid_url = (char.get("video_url") or "").strip()

    update: dict = {}

    if img_url and (force or not _is_working_catbox(img_url)):
        try:
            if _is_telegram_file_id(img_url):
                data, ext = await _download_from_telegram(img_url)
            else:
                data = await _http_download(session, img_url)
                ext  = "jpg"
            filename   = f"{safe_name}.{ext}"
            catbox_url = await _upload_to_catbox(session, data, filename)
            update["img_url"] = catbox_url
            log.info(f"[{char_id}] photo → {catbox_url}")
        except ReloadError as e:
            log.error(f"[{char_id}] photo FAILED: {e}")
            return ReloadResult(char_id, error=f"photo: {e}")

    if vid_url and (force or not _is_working_catbox(vid_url)):
        try:
            if _is_telegram_file_id(vid_url):
                data, ext = await _download_from_telegram(vid_url)
            else:
                data = await _http_download(session, vid_url)
                ext  = "mp4"
            filename   = f"{safe_name}.{ext}"
            catbox_url = await _upload_to_catbox(session, data, filename)
            update["video_url"] = catbox_url
            log.info(f"[{char_id}] video → {catbox_url}")
        except ReloadError as e:
            log.error(f"[{char_id}] video FAILED: {e}")
            return ReloadResult(char_id, error=f"video: {e}")

    if not update:
        return ReloadResult(char_id, skipped=True)

    await _col("characters").update_one({"id": char_id}, {"$set": update})
    return ReloadResult(char_id, success=True)


# ═════════════════════════════════════════════════════════════════════════════
# Bulk reload engine
# ═════════════════════════════════════════════════════════════════════════════

async def _bulk_reload(
    status_msg: Message,
    force: bool = False,
) -> tuple[int, int, int, list[str]]:
    query: dict = {"enabled": True}
    if not force:
        query["$or"] = [
            {"img_url": {"$exists": True, "$not": {"$regex": r"^https://files\.catbox\.moe/"}}},
            {"video_url": {"$exists": True, "$not": {"$regex": r"^https://files\.catbox\.moe/"}}},
        ]

    chars = await _col("characters").find(query).to_list(None)
    total = len(chars)

    if total == 0:
        await safe_edit(
            status_msg,
            "✅ **Nothing to do!**\n\n"
            "All enabled characters already have working Catbox URLs.",
        )
        return 0, 0, 0, []

    sem        = asyncio.Semaphore(CONCURRENCY)
    success    = 0
    skipped    = 0
    failed     = 0
    done       = 0
    failed_ids: list[str] = []
    start_ts   = time.time()

    connector = aiohttp.TCPConnector(limit=CONCURRENCY * 2, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:

        async def _worker(char: dict):
            nonlocal success, skipped, failed, done
            async with sem:
                result = await _process_one(session, char, force=force)
                done += 1

                if result.success:
                    success += 1
                elif result.skipped:
                    skipped += 1
                else:
                    failed += 1
                    failed_ids.append(f"`{result.char_id}` — {result.error}")

                if done % PROGRESS_EVERY == 0 or done == total:
                    elapsed = time.time() - start_ts
                    eta     = (elapsed / done) * (total - done) if done else 0
                    pct     = int(done / total * 100)
                    bar     = "█" * (pct // 5) + "░" * (20 - pct // 5)
                    await safe_edit(
                        status_msg,
                        f"⚙️ **GODMODE RELOAD** in progress…\n\n"
                        f"`[{bar}]` {pct}%\n\n"
                        f"📊 **{done}** / **{total}** need fixing\n"
                        f"✅ Fixed    : **{success}**\n"
                        f"⏭ Skipped  : **{skipped}**\n"
                        f"❌ Failed   : **{failed}**\n\n"
                        f"⏱ Elapsed  : `{elapsed:.0f}s`  ETA: `{eta:.0f}s`",
                    )

        await asyncio.gather(*[_worker(c) for c in chars])

    return success, skipped, failed, failed_ids


# ═════════════════════════════════════════════════════════════════════════════
# /godmode commands  ← NOW works in BOTH private AND group chats
# ═════════════════════════════════════════════════════════════════════════════

# ── FIX 1: removed `& filters.private` so GC also works ──────────────────────
@app.on_message(filters.command("godmode"))
async def cmd_godmode(client, message: Message):
    # ── FIX 2: guard against messages with no sender (channel posts, etc.) ───
    if not message.from_user:
        return

    if not _is_sudo(message.from_user.id):
        return await message.reply_text(
            "⛔ You are not authorised to use `/godmode`."
        )

    args = message.command[1:]  # message.command[0] is "godmode"

    # ── FIX 3: explicit routing with safe fallback ───────────────────────────
    sub = args[0].lower() if args else "status"

    if sub == "status":
        await _godmode_status(message)

    elif sub == "reload":
        force = "--force" in args
        await _godmode_reload(message, force=force)

    elif sub == "fix":
        if len(args) < 2:
            return await message.reply_text("❌ Usage: `/godmode fix <character_id>`")
        await _godmode_fix(message, args[1])

    else:
        await message.reply_text(
            "**GODMODE — available commands**\n\n"
            "`/godmode status`          — DB media health check\n"
            "`/godmode reload`          — fix all broken / non-Catbox URLs\n"
            "`/godmode reload --force`  — re-upload everything (even working Catbox URLs)\n"
            "`/godmode fix <id>`        — re-process a single character\n"
        )


# ── status ────────────────────────────────────────────────────────────────────

async def _godmode_status(message: Message):
    msg = await message.reply_text("🔍 Scanning database…")

    total        = await _col("characters").count_documents({"enabled": True})
    catbox_photo = 0
    catbox_video = 0
    fileid_photo = 0
    fileid_video = 0
    broken_photo = 0
    broken_video = 0

    async for char in _col("characters").find({"enabled": True}):
        img = (char.get("img_url")   or "").strip()
        vid = (char.get("video_url") or "").strip()

        for val, is_vid in ((img, False), (vid, True)):
            if not val:
                continue
            if _is_working_catbox(val):
                if is_vid:
                    catbox_video += 1
                else:
                    catbox_photo += 1
            elif _is_telegram_file_id(val):
                if is_vid:
                    fileid_video += 1
                else:
                    fileid_photo += 1
            else:
                if is_vid:
                    broken_video += 1
                else:
                    broken_photo += 1

    needs_work = fileid_photo + fileid_video + broken_photo + broken_video

    await safe_edit(
        msg,
        "╔══════════════════════════════╗\n"
        "║    🛡  GODMODE  STATUS       ║\n"
        "╚══════════════════════════════╝\n\n"
        f"📦 Total characters     : **{total:,}**\n\n"
        "**Photo (img_url)**\n"
        f"  ✅ Catbox URL         : **{catbox_photo:,}**\n"
        f"  🔁 Telegram file_id  : **{fileid_photo:,}**\n"
        f"  💔 Broken / raw URL  : **{broken_photo:,}**\n\n"
        "**Video (video_url)**\n"
        f"  ✅ Catbox URL         : **{catbox_video:,}**\n"
        f"  🔁 Telegram file_id  : **{fileid_video:,}**\n"
        f"  💔 Broken / raw URL  : **{broken_video:,}**\n\n"
        + (
            f"⚠️ **{needs_work:,}** characters need fixing.\n"
            "Run `/godmode reload` to fix them."
            if needs_work else
            "🎉 **All media already on Catbox — no errors expected!**"
        ),
    )


# ── bulk reload ───────────────────────────────────────────────────────────────

async def _godmode_reload(message: Message, force: bool):
    note = " _(force — re-uploading everything)_" if force else ""
    status_msg = await message.reply_text(
        f"🚀 **GODMODE RELOAD** starting…{note}\n\n"
        "Scanning for characters with broken / non-Catbox media…"
    )

    try:
        success, skipped, failed, failed_ids = await _bulk_reload(
            status_msg, force=force
        )
    except Exception as e:
        log.exception("_bulk_reload crashed")
        return await safe_edit(status_msg, f"💥 **Fatal error during reload:**\n`{e}`")

    fail_block = ""
    if failed_ids:
        sample = failed_ids[:15]
        more   = failed - len(sample)
        fail_block = "\n\n**Failed characters:**\n" + "\n".join(sample)
        if more > 0:
            fail_block += f"\n… and **{more}** more (check logs)"

    footer = (
        "\n\n💡 Re-run `/godmode reload` to retry failures."
        if failed else
        "\n\n🎉 All broken media now points to stable Catbox URLs!"
    )

    await safe_edit(
        status_msg,
        "╔══════════════════════════════╗\n"
        "║   ✅  RELOAD  COMPLETE       ║\n"
        "╚══════════════════════════════╝\n\n"
        f"✅ Fixed     : **{success:,}**\n"
        f"⏭ Skipped   : **{skipped:,}** _(already Catbox)_\n"
        f"❌ Failed    : **{failed:,}**\n"
        + fail_block
        + footer,
    )


# ── single-char fix ───────────────────────────────────────────────────────────

async def _godmode_fix(message: Message, raw_id: str):
    try:
        char_id = str(int(raw_id)).zfill(4)
    except ValueError:
        char_id = raw_id.strip()

    char = await get_character(char_id)
    if not char:
        return await message.reply_text(f"❌ Character `{char_id}` not found.")

    msg = await message.reply_text(
        f"🔧 Fixing `{char_id}` — **{char.get('name', '?')}**…\n\n"
        f"📸 img_url   : `{(char.get('img_url') or 'none')[:60]}`\n"
        f"🎬 video_url : `{(char.get('video_url') or 'none')[:60]}`"
    )

    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        result = await _process_one(session, char, force=True)

    updated = await get_character(char_id)

    if result.success:
        await safe_edit(
            msg,
            f"✅ `{char_id}` — **{char.get('name', '?')}**\n\n"
            f"📸 img_url   : `{(updated.get('img_url') or 'none')[:80]}`\n"
            f"🎬 video_url : `{(updated.get('video_url') or 'none')[:80]}`\n\n"
            "DB patched with new Catbox URLs.",
        )
    elif result.skipped:
        await safe_edit(
            msg,
            f"⏭ `{char_id}` — **{char.get('name', '?')}**\n"
            "Already on Catbox — nothing to do.\n"
            "_(use `/godmode reload --force` to re-upload anyway)_",
        )
    else:
        await safe_edit(
            msg,
            f"❌ `{char_id}` — **{char.get('name', '?')}**\n"
            f"**Error:** `{result.error}`\n\n"
            "Possible causes:\n"
            "• The file_id is from a different bot/session\n"
            "• The dump channel is inaccessible\n"
            "• The original URL is dead or geo-blocked",
        )
