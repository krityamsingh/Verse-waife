"""SoulCatcher/modules/reloader.py  — PROFESSIONAL EDITION
═══════════════════════════════════════════════════════════════════════════════
GODMODE media-reload system
  • Scans DB for every enabled character
  • Resolves broken / raw / Telegram file_id media URLs
  • Downloads from Telegram dump channel (message-level scan, not just file_id)
  • Falls back to direct HTTP download with HEAD-check + 3× retry
  • Uploads to Catbox (anonymous) with 3× retry + integrity verification
  • Patches MongoDB atomically
  • Live progress bar in Telegram (rate-limit-safe edits)

Commands
────────
  /godmode status            — DB media health check (counts + sample broken IDs)
  /godmode reload            — fix all broken / non-Catbox media
  /godmode reload --force    — re-upload everything (even working Catbox URLs)
  /godmode fix <id>          — re-process a single character (force mode)
  /godmode scan              — deep scan: verify each Catbox URL is still alive
  /godmode retry             — retry only previously-failed characters (uses DB flag)
  /godmode help              — show this menu

Only OWNER_ID / SUDO_USERS may invoke these commands.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
import re
import time
from io import BytesIO
from typing import Optional

import aiohttp
from pyrogram import filters
from pyrogram.errors import (
    ChannelInvalid,
    ChannelPrivate,
    FileIdInvalid,
    FloodWait,
    MessageIdInvalid,
    MessageNotModified,
    RPCError,
)
from pyrogram.types import Message

from .. import app
from ..database import _col, get_character

log = logging.getLogger("SoulCatcher.reloader")

# ─── authorised users ─────────────────────────────────────────────────────────
OWNER_ID   = 6118760915
SUDO_USERS: set[int] = {6118760915}

# ─── tunables ─────────────────────────────────────────────────────────────────
DUMP_CHANNEL      = -1003869604435   # channel where media messages live
CATBOX_URL        = "https://catbox.moe/user/api.php"
CATBOX_CDN_PREFIX = "https://files.catbox.moe/"

DOWNLOAD_TIMEOUT  = aiohttp.ClientTimeout(total=90,  connect=15)
UPLOAD_TIMEOUT    = aiohttp.ClientTimeout(total=180, connect=15)
HEAD_TIMEOUT      = aiohttp.ClientTimeout(total=10,  connect=5)

MAX_RETRIES       = 4        # per-step retry budget
CONCURRENCY       = 6        # parallel workers
PROGRESS_EVERY    = 5        # update progress every N completions
DUMP_SCAN_LIMIT   = 5_000    # how many messages to scan when building file_id index
MIN_EDIT_INTERVAL = 3.5      # seconds between Telegram message edits
MAX_MSG_LEN       = 4_096    # Telegram hard limit

# Patterns that indicate a value is almost certainly a Telegram file_id
_TG_FILEID_RE = re.compile(r"^[A-Za-z0-9_\-]{20,}$")
# Photo file_ids normally start with these prefixes
_TG_PHOTO_PREFIXES = ("AgAC", "AAKC", "AQA")
_TG_VIDEO_PREFIXES = ("BAA",  "BQA",  "BAAC")

# ──────────────────────────────────────────────────────────────────────────────
# In-memory index:  file_id → catbox_url  (populated during dump-channel scan)
# ──────────────────────────────────────────────────────────────────────────────
_dump_index: dict[str, str] = {}   # {file_id: catbox_url_if_already_uploaded}
_dump_messages: dict[str, int]  = {}   # {file_id: message_id_in_dump_channel}
_index_built = False


# ═══════════════════════════════════════════════════════════════════════════════
# Misc helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _is_sudo(uid: int) -> bool:
    return uid == OWNER_ID or uid in SUDO_USERS


def _is_working_catbox(url: str) -> bool:
    return isinstance(url, str) and url.startswith(CATBOX_CDN_PREFIX)


def _looks_like_file_id(value: str) -> bool:
    """Heuristic: no http prefix, long enough, matches base64-url charset."""
    if not value or value.startswith("http"):
        return False
    return bool(_TG_FILEID_RE.match(value))


def _mime_to_ext(content_type: str) -> str:
    ct = content_type.split(";")[0].strip()
    ext = mimetypes.guess_extension(ct) or ".bin"
    # normalise some common aliases
    return {".jpe": ".jpg", ".jpeg": ".jpg", ".jfif": ".jpg"}.get(ext, ext).lstrip(".")


def _guess_ext_from_file_id(file_id: str) -> str:
    for p in _TG_VIDEO_PREFIXES:
        if file_id.startswith(p):
            return "mp4"
    for p in _TG_PHOTO_PREFIXES:
        if file_id.startswith(p):
            return "jpg"
    return "bin"


class ReloadError(Exception):
    """Raised when a media item cannot be recovered."""


# ═══════════════════════════════════════════════════════════════════════════════
# safe_edit — rate-limit-aware Telegram message editor
# ═══════════════════════════════════════════════════════════════════════════════

_edit_state: dict[int, tuple[str, float]] = {}
_edit_lock:  dict[int, asyncio.Lock]      = {}


def _get_edit_lock(mid: int) -> asyncio.Lock:
    if mid not in _edit_lock:
        _edit_lock[mid] = asyncio.Lock()
    return _edit_lock[mid]


async def safe_edit(msg: Message, text: str) -> None:
    if len(text) > MAX_MSG_LEN:
        cutoff = text.rfind("\n", 0, MAX_MSG_LEN - 80)
        cutoff = cutoff if cutoff > 0 else MAX_MSG_LEN - 80
        text   = text[:cutoff] + "\n\n…_(truncated — see logs for full list)_"

    mid = msg.id
    async with _get_edit_lock(mid):
        now = time.monotonic()
        last_text, last_ts = _edit_state.get(mid, ("", 0.0))

        if text == last_text:
            return

        wait_for = MIN_EDIT_INTERVAL - (now - last_ts)
        if wait_for > 0:
            await asyncio.sleep(wait_for)

        for attempt in range(3):
            try:
                await msg.edit_text(text)
                _edit_state[mid] = (text, time.monotonic())
                return
            except FloodWait as fw:
                log.warning(f"[safe_edit] FloodWait {fw.value}s on msg {mid}")
                await asyncio.sleep(fw.value + 1)
            except (MessageNotModified, MessageIdInvalid):
                _edit_state[mid] = (text, time.monotonic())
                return
            except Exception as exc:
                log.warning(f"[safe_edit] msg {mid} attempt {attempt}: {exc}")
                if attempt < 2:
                    await asyncio.sleep(2)
                return


# ═══════════════════════════════════════════════════════════════════════════════
# Dump-channel index builder
# ═══════════════════════════════════════════════════════════════════════════════

async def _build_dump_index(status_msg: Optional[Message] = None) -> None:
    """
    Iterates recent messages in DUMP_CHANNEL and builds a mapping of
    file_id → message_id so we can re-download by message ID (more reliable
    than using raw file_ids which can expire across sessions).
    """
    global _index_built
    if _index_built:
        return

    if status_msg:
        await safe_edit(status_msg, "📡 **Scanning dump channel** — building media index…")

    count = 0
    try:
        async for msg in app.get_chat_history(DUMP_CHANNEL, limit=DUMP_SCAN_LIMIT):
            media = msg.photo or msg.video or msg.document or msg.animation
            if not media:
                continue
            fid = media.file_id
            _dump_messages[fid] = msg.id
            count += 1
    except (ChannelInvalid, ChannelPrivate) as exc:
        log.error(f"[dump-index] Cannot access dump channel {DUMP_CHANNEL}: {exc}")
        raise ReloadError(
            f"Dump channel {DUMP_CHANNEL} is inaccessible.\n"
            "Make sure the bot is a member/admin of that channel."
        ) from exc
    except Exception as exc:
        log.error(f"[dump-index] Unexpected error: {exc}")
        raise ReloadError(f"Dump channel scan failed: {exc}") from exc

    log.info(f"[dump-index] indexed {count} media items from dump channel")
    _index_built = True


# ═══════════════════════════════════════════════════════════════════════════════
# Step 1A — Download via Telegram (file_id, with dump-channel fallback)
# ═══════════════════════════════════════════════════════════════════════════════

async def _tg_download(file_id: str) -> tuple[bytes, str]:
    """
    Attempts download strategies in order:
      1. Direct download via file_id (current session)
      2. Re-download via message_id from dump channel index
      3. Forward the message to get a fresh file_id and download
    """
    ext = _guess_ext_from_file_id(file_id)

    # ── Strategy 1: direct file_id download ──────────────────────────────────
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            buf = BytesIO()
            await app.download_media(file_id, in_memory=True, file_name=buf)
            buf.seek(0)
            data = buf.read()
            if data:
                log.debug(f"[tg-dl] direct download OK ({len(data)} bytes)")
                return data, ext
            raise ReloadError("Empty file from Telegram direct download")
        except FileIdInvalid:
            log.warning(f"[tg-dl] file_id invalid/expired, trying dump-channel…")
            break   # skip remaining retries, move to strategy 2
        except FloodWait as fw:
            log.warning(f"[tg-dl] FloodWait {fw.value}s")
            await asyncio.sleep(fw.value + 1)
        except ReloadError:
            raise
        except RPCError as exc:
            log.warning(f"[tg-dl] RPC {exc} attempt {attempt}/{MAX_RETRIES}")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(2 ** attempt)

    # ── Strategy 2: re-download via message_id from dump channel ─────────────
    if not _index_built:
        await _build_dump_index()

    msg_id = _dump_messages.get(file_id)
    if msg_id:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                dump_msg = await app.get_messages(DUMP_CHANNEL, msg_id)
                media    = dump_msg.photo or dump_msg.video or dump_msg.document or dump_msg.animation
                if not media:
                    raise ReloadError(f"Dump message {msg_id} has no media")
                fresh_fid = media.file_id
                buf = BytesIO()
                await app.download_media(fresh_fid, in_memory=True, file_name=buf)
                buf.seek(0)
                data = buf.read()
                if data:
                    log.info(f"[tg-dl] dump-channel re-download OK via msg {msg_id}")
                    return data, ext
            except FloodWait as fw:
                await asyncio.sleep(fw.value + 1)
            except Exception as exc:
                log.warning(f"[tg-dl] dump-channel attempt {attempt}: {exc}")
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(2 ** attempt)

    raise ReloadError(
        f"All Telegram download strategies exhausted for file_id {file_id[:24]}…\n"
        "The file may have been deleted from the dump channel."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Step 1B — HTTP download (for non-file_id URLs that aren't Catbox)
# ═══════════════════════════════════════════════════════════════════════════════

async def _http_download(session: aiohttp.ClientSession, url: str) -> tuple[bytes, str]:
    # Quick HEAD check first to catch dead URLs fast
    try:
        async with session.head(url, timeout=HEAD_TIMEOUT, allow_redirects=True) as r:
            if r.status == 404:
                raise ReloadError(f"404 Not Found: {url}")
            content_type = r.headers.get("Content-Type", "image/jpeg")
            ext = _mime_to_ext(content_type)
    except ReloadError:
        raise
    except Exception:
        ext = "jpg"   # can't HEAD, try anyway

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with session.get(url, timeout=DOWNLOAD_TIMEOUT, allow_redirects=True) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    if data:
                        # Refine ext from actual response headers
                        ct  = resp.headers.get("Content-Type", "")
                        if ct:
                            ext = _mime_to_ext(ct)
                        return data, ext
                    raise ReloadError(f"Empty body from {url}")
                if resp.status in (403, 404, 410):
                    raise ReloadError(f"HTTP {resp.status} — resource gone: {url}")
                raise ValueError(f"HTTP {resp.status}")
        except ReloadError:
            raise
        except asyncio.TimeoutError:
            log.warning(f"[http-dl] timeout attempt {attempt}/{MAX_RETRIES}: {url}")
        except Exception as exc:
            log.warning(f"[http-dl] attempt {attempt}/{MAX_RETRIES}: {exc}")
        if attempt < MAX_RETRIES:
            await asyncio.sleep(2 ** attempt)

    raise ReloadError(f"HTTP download failed after {MAX_RETRIES} attempts: {url}")


# ═══════════════════════════════════════════════════════════════════════════════
# Step 2 — Upload to Catbox (anonymous) with integrity check
# ═══════════════════════════════════════════════════════════════════════════════

async def _upload_to_catbox(
    session: aiohttp.ClientSession,
    data:    bytes,
    filename: str,
) -> str:
    sha = hashlib.md5(data).hexdigest()[:8]
    log.debug(f"[catbox] uploading {filename} ({len(data)} bytes, md5={sha})")

    for attempt in range(1, MAX_RETRIES + 1):
        form = aiohttp.FormData()
        form.add_field("reqtype",      "fileupload")
        form.add_field("userhash",     "")
        form.add_field(
            "fileToUpload",
            BytesIO(data),
            filename=filename,
            content_type="application/octet-stream",
        )
        try:
            async with session.post(CATBOX_URL, data=form, timeout=UPLOAD_TIMEOUT) as resp:
                text = (await resp.text()).strip()
                if resp.status == 200 and text.startswith("https://"):
                    log.info(f"[catbox] ✓ {filename} → {text}")
                    return text
                raise ReloadError(f"Catbox HTTP {resp.status}: {text[:120]}")
        except ReloadError:
            raise
        except asyncio.TimeoutError:
            log.warning(f"[catbox] timeout attempt {attempt}/{MAX_RETRIES}")
        except Exception as exc:
            log.warning(f"[catbox] attempt {attempt}/{MAX_RETRIES}: {exc}")
        if attempt < MAX_RETRIES:
            await asyncio.sleep(3 * attempt)

    raise ReloadError(f"Catbox upload failed after {MAX_RETRIES} attempts for {filename}")


# ═══════════════════════════════════════════════════════════════════════════════
# Step 3 — Verify a Catbox URL is alive (used by /godmode scan)
# ═══════════════════════════════════════════════════════════════════════════════

async def _verify_catbox_url(session: aiohttp.ClientSession, url: str) -> bool:
    try:
        async with session.head(url, timeout=HEAD_TIMEOUT, allow_redirects=True) as r:
            return r.status == 200
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# Per-character processor
# ═══════════════════════════════════════════════════════════════════════════════

class ReloadResult:
    __slots__ = ("char_id", "success", "skipped", "error", "fields_updated")

    def __init__(
        self,
        char_id:        str,
        *,
        success:        bool = False,
        skipped:        bool = False,
        error:          str  = "",
        fields_updated: list[str] | None = None,
    ):
        self.char_id       = char_id
        self.success       = success
        self.skipped       = skipped
        self.error         = error
        self.fields_updated = fields_updated or []


async def _resolve_media(
    session:  aiohttp.ClientSession,
    char_id:  str,
    url:      str,
    field:    str,           # "img_url" or "video_url"
    safe_name: str,
) -> str:
    """
    Given a media value (file_id OR url), downloads it and uploads to Catbox.
    Returns the stable Catbox URL.
    """
    if _looks_like_file_id(url):
        log.info(f"[{char_id}:{field}] Telegram file_id → download + upload")
        data, ext = await _tg_download(url)
    else:
        log.info(f"[{char_id}:{field}] HTTP download from {url[:60]}")
        data, ext = await _http_download(session, url)

    ext = ext.lstrip(".")
    # Fallback ext based on field
    if ext in ("bin", "") :
        ext = "mp4" if field == "video_url" else "jpg"

    filename = f"{safe_name}_{field.split('_')[0]}.{ext}"
    return await _upload_to_catbox(session, data, filename)


async def _process_one(
    session:  aiohttp.ClientSession,
    char:     dict,
    force:    bool = False,
) -> ReloadResult:
    char_id    = str(char["id"])
    safe_name  = re.sub(r"[^\w\-]", "_", f"{char_id}_{char.get('name', 'char')}")

    img_url    = (char.get("img_url")   or "").strip()
    vid_url    = (char.get("video_url") or "").strip()

    update:         dict      = {}
    fields_updated: list[str] = []
    errors:         list[str] = []

    for field, url in (("img_url", img_url), ("video_url", vid_url)):
        if not url:
            continue
        needs_fix = force or not _is_working_catbox(url)
        if not needs_fix:
            continue
        try:
            new_url = await _resolve_media(session, char_id, url, field, safe_name)
            update[field] = new_url
            fields_updated.append(field)
        except ReloadError as exc:
            log.error(f"[{char_id}:{field}] FAILED: {exc}")
            errors.append(f"{field}: {exc}")
        except Exception as exc:
            log.exception(f"[{char_id}:{field}] Unexpected error")
            errors.append(f"{field}: unexpected — {exc}")

    if errors and not update:
        # Mark this char as "last failed" in DB so /godmode retry can target it
        await _col("characters").update_one(
            {"id": char_id},
            {"$set": {"_reload_failed": True, "_reload_error": "; ".join(errors)}},
        )
        return ReloadResult(char_id, error="; ".join(errors))

    if update:
        update["_reload_failed"] = False
        update["_reload_error"]  = ""
        await _col("characters").update_one({"id": char_id}, {"$set": update})
        log.info(f"[{char_id}] DB patched — fields: {fields_updated}")
        return ReloadResult(char_id, success=True, fields_updated=fields_updated)

    return ReloadResult(char_id, skipped=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Bulk reload engine
# ═══════════════════════════════════════════════════════════════════════════════

async def _bulk_reload(
    status_msg: Message,
    force:      bool = False,
    retry_only: bool = False,
) -> tuple[int, int, int, list[str]]:
    """
    Returns (success, skipped, failed, failed_id_lines).
    """
    # ── Build query ───────────────────────────────────────────────────────────
    if retry_only:
        query: dict = {"enabled": True, "_reload_failed": True}
    elif force:
        query = {"enabled": True}
    else:
        query = {
            "enabled": True,
            "$or": [
                {"img_url":   {"$exists": True, "$not": {"$regex": r"^https://files\.catbox\.moe/"}}},
                {"video_url": {"$exists": True, "$not": {"$regex": r"^https://files\.catbox\.moe/"}}},
            ],
        }

    # ── Pre-build dump channel index ─────────────────────────────────────────
    try:
        await _build_dump_index(status_msg)
    except ReloadError as exc:
        await safe_edit(status_msg, f"⚠️ **Dump channel warning:**\n`{exc}`\n\nContinuing without Telegram fallback…")
        await asyncio.sleep(2)

    chars = await _col("characters").find(query).to_list(None)
    total = len(chars)

    if total == 0:
        await safe_edit(
            status_msg,
            "✅ **Nothing to do!**\n\nAll enabled characters already have working Catbox URLs.\n"
            "Use `/godmode scan` to verify each URL is still alive.",
        )
        return 0, 0, 0, []

    sem        = asyncio.Semaphore(CONCURRENCY)
    success    = 0
    skipped    = 0
    failed     = 0
    done       = 0
    failed_ids: list[str] = []
    start_ts   = time.time()

    connector = aiohttp.TCPConnector(limit=CONCURRENCY * 3, ssl=False, ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=connector) as session:

        async def _worker(char: dict) -> None:
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
                    failed_ids.append(f"`{result.char_id}` — {result.error[:80]}")

                if done % PROGRESS_EVERY == 0 or done == total:
                    elapsed = time.time() - start_ts
                    eta     = (elapsed / done) * (total - done) if done else 0
                    pct     = int(done / total * 100)
                    filled  = pct // 5
                    bar     = "█" * filled + "░" * (20 - filled)

                    rate = done / elapsed if elapsed > 0 else 0
                    await safe_edit(
                        status_msg,
                        f"⚙️ **GODMODE RELOAD** in progress…\n\n"
                        f"`[{bar}]` {pct}%\n\n"
                        f"📊 **{done}** / **{total}** processed\n"
                        f"✅ Fixed    : **{success}**\n"
                        f"⏭ Skipped  : **{skipped}**\n"
                        f"❌ Failed   : **{failed}**\n\n"
                        f"⚡ Rate     : `{rate:.1f}/s`\n"
                        f"⏱ Elapsed  : `{elapsed:.0f}s`  |  ETA: `{eta:.0f}s`",
                    )

        await asyncio.gather(*[_worker(c) for c in chars])

    return success, skipped, failed, failed_ids


# ═══════════════════════════════════════════════════════════════════════════════
# /godmode scan — verify existing Catbox URLs
# ═══════════════════════════════════════════════════════════════════════════════

async def _godmode_scan(message: Message) -> None:
    msg = await message.reply_text(
        "🔬 **GODMODE SCAN** — verifying all Catbox URLs…\n\n"
        "This checks that each URL is still accessible (HTTP HEAD)."
    )

    chars = await _col("characters").find(
        {"enabled": True, "$or": [
            {"img_url":   {"$regex": r"^https://files\.catbox\.moe/"}},
            {"video_url": {"$regex": r"^https://files\.catbox\.moe/"}},
        ]}
    ).to_list(None)

    total    = len(chars)
    alive    = 0
    dead     = 0
    dead_ids: list[str] = []
    done     = 0
    sem      = asyncio.Semaphore(20)   # lightweight HEAD requests, higher concurrency OK

    connector = aiohttp.TCPConnector(limit=40, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:

        async def _check(char: dict) -> None:
            nonlocal alive, dead, done
            async with sem:
                char_id = str(char["id"])
                for field, key in (("img_url", "img"), ("video_url", "video")):
                    url = (char.get(field) or "").strip()
                    if not _is_working_catbox(url):
                        continue
                    ok = await _verify_catbox_url(session, url)
                    if ok:
                        alive += 1
                    else:
                        dead  += 1
                        dead_ids.append(f"`{char_id}` {key}: {url.split('/')[-1]}")
                        # Mark for re-upload
                        await _col("characters").update_one(
                            {"id": char_id},
                            {"$set": {"_reload_failed": True, "_reload_error": f"{field} 404/dead"}},
                        )
                done += 1
                if done % 50 == 0 or done == total:
                    pct  = int(done / total * 100)
                    bar  = "█" * (pct // 5) + "░" * (20 - pct // 5)
                    await safe_edit(
                        msg,
                        f"🔬 **GODMODE SCAN** `[{bar}]` {pct}%\n\n"
                        f"📊 {done}/{total}  |  ✅ alive: **{alive}**  |  💀 dead: **{dead}**",
                    )

        await asyncio.gather(*[_check(c) for c in chars])

    dead_block = ""
    if dead_ids:
        sample     = dead_ids[:20]
        dead_block = "\n\n**Dead URLs (sample):**\n" + "\n".join(sample)
        if dead > 20:
            dead_block += f"\n…and **{dead - 20}** more. Run `/godmode retry` to re-upload them."

    await safe_edit(
        msg,
        "╔══════════════════════════════╗\n"
        "║   🔬  SCAN  COMPLETE         ║\n"
        "╚══════════════════════════════╝\n\n"
        f"✅ Alive     : **{alive:,}**\n"
        f"💀 Dead/404  : **{dead:,}**\n"
        + dead_block
        + ("\n\n💡 Run `/godmode retry` to re-upload all dead URLs." if dead else "\n\n🎉 All Catbox URLs are alive!"),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# /godmode status
# ═══════════════════════════════════════════════════════════════════════════════

async def _godmode_status(message: Message) -> None:
    msg = await message.reply_text("🔍 **GODMODE STATUS** — scanning database…")

    total          = await _col("characters").count_documents({"enabled": True})
    catbox_photo   = 0
    catbox_video   = 0
    fileid_photo   = 0
    fileid_video   = 0
    broken_photo   = 0
    broken_video   = 0
    no_media       = 0
    last_failed    = await _col("characters").count_documents({"enabled": True, "_reload_failed": True})

    async for char in _col("characters").find({"enabled": True}):
        img = (char.get("img_url")   or "").strip()
        vid = (char.get("video_url") or "").strip()
        if not img and not vid:
            no_media += 1
            continue
        for val, is_vid in ((img, False), (vid, True)):
            if not val:
                continue
            if _is_working_catbox(val):
                if is_vid: catbox_video += 1
                else:      catbox_photo += 1
            elif _looks_like_file_id(val):
                if is_vid: fileid_video += 1
                else:      fileid_photo += 1
            else:
                if is_vid: broken_video += 1
                else:      broken_photo += 1

    needs_work = fileid_photo + fileid_video + broken_photo + broken_video

    await safe_edit(
        msg,
        "╔══════════════════════════════╗\n"
        "║    🛡  GODMODE  STATUS       ║\n"
        "╚══════════════════════════════╝\n\n"
        f"📦 Total characters     : **{total:,}**\n"
        f"🚫 No media at all      : **{no_media:,}**\n"
        f"🔴 Last-run failures    : **{last_failed:,}**\n\n"
        "**Photo (img_url)**\n"
        f"  ✅ Catbox URL         : **{catbox_photo:,}**\n"
        f"  🔁 Telegram file_id  : **{fileid_photo:,}**\n"
        f"  💔 Broken / raw URL  : **{broken_photo:,}**\n\n"
        "**Video (video_url)**\n"
        f"  ✅ Catbox URL         : **{catbox_video:,}**\n"
        f"  🔁 Telegram file_id  : **{fileid_video:,}**\n"
        f"  💔 Broken / raw URL  : **{broken_video:,}**\n\n"
        + (
            f"⚠️ **{needs_work:,}** character(s) need fixing.\n\n"
            "Run `/godmode reload` to fix them.\n"
            "Run `/godmode scan` to check Catbox URL health."
            if needs_work else
            "🎉 **All media on Catbox — no obvious issues!**\n\n"
            "Run `/godmode scan` to verify each URL is still alive."
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# /godmode reload
# ═══════════════════════════════════════════════════════════════════════════════

async def _godmode_reload(message: Message, force: bool, retry_only: bool = False) -> None:
    if retry_only:
        note = " _(retrying previously failed characters)_"
    elif force:
        note = " _(force — re-uploading everything)_"
    else:
        note = ""

    status_msg = await message.reply_text(
        f"🚀 **GODMODE RELOAD** starting…{note}\n\n"
        "Scanning DB and building Telegram dump-channel index…"
    )

    try:
        success, skipped, failed, failed_ids = await _bulk_reload(
            status_msg, force=force, retry_only=retry_only
        )
    except Exception as exc:
        log.exception("_bulk_reload crashed")
        return await safe_edit(status_msg, f"💥 **Fatal error during reload:**\n`{exc}`")

    fail_block = ""
    if failed_ids:
        sample     = failed_ids[:20]
        more       = failed - len(sample)
        fail_block = "\n\n**Failed characters:**\n" + "\n".join(sample)
        if more > 0:
            fail_block += f"\n…and **{more}** more. Run `/godmode retry` to retry just these."

    footer = (
        "\n\n💡 Run `/godmode retry` to re-attempt failures only.\n"
        "Run `/godmode scan` to verify all Catbox URLs are alive."
        if failed else
        "\n\n🎉 All broken media now points to stable Catbox URLs!"
    )

    await safe_edit(
        status_msg,
        "╔══════════════════════════════╗\n"
        "║   ✅  RELOAD  COMPLETE       ║\n"
        "╚══════════════════════════════╝\n\n"
        f"✅ Fixed     : **{success:,}**\n"
        f"⏭ Skipped   : **{skipped:,}** _(already on Catbox)_\n"
        f"❌ Failed    : **{failed:,}**\n"
        + fail_block
        + footer,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# /godmode fix <id>
# ═══════════════════════════════════════════════════════════════════════════════

async def _godmode_fix(message: Message, raw_id: str) -> None:
    # Normalise ID
    try:
        char_id = str(int(raw_id)).zfill(4)
    except ValueError:
        char_id = raw_id.strip()

    char = await get_character(char_id)
    if not char:
        return await message.reply_text(f"❌ Character `{char_id}` not found in database.")

    msg = await message.reply_text(
        f"🔧 **Fixing** `{char_id}` — **{char.get('name', '?')}**\n\n"
        f"📸 `img_url`   : `{(char.get('img_url')   or 'none')[:72]}`\n"
        f"🎬 `video_url` : `{(char.get('video_url') or 'none')[:72]}`"
    )

    # Ensure dump index is ready
    try:
        await _build_dump_index(msg)
    except ReloadError as exc:
        await safe_edit(msg, f"⚠️ Dump channel unavailable: `{exc}`\nAttempting direct download only…")

    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        result = await _process_one(session, char, force=True)

    updated = await get_character(char_id)
    name    = char.get("name", "?")

    if result.success:
        await safe_edit(
            msg,
            f"✅ `{char_id}` — **{name}**\n\n"
            f"📸 `img_url`   : `{(updated.get('img_url')   or 'none')[:80]}`\n"
            f"🎬 `video_url` : `{(updated.get('video_url') or 'none')[:80]}`\n\n"
            f"🔄 Updated fields: `{'`, `'.join(result.fields_updated)}`\n"
            "DB patched with stable Catbox URLs ✓",
        )
    elif result.skipped:
        await safe_edit(
            msg,
            f"⏭ `{char_id}` — **{name}**\n"
            "Already on Catbox — nothing to do.\n\n"
            "_Use `/godmode reload --force` to re-upload anyway._",
        )
    else:
        await safe_edit(
            msg,
            f"❌ `{char_id}` — **{name}**\n\n"
            f"**Error:** `{result.error}`\n\n"
            "**Possible causes:**\n"
            "• File deleted from dump channel\n"
            "• file_id belongs to a different bot session\n"
            "• Source URL is dead / geo-blocked (403 or 404)\n"
            "• Catbox upload quota hit or service down\n\n"
            "Check logs for details: `tail -f soulcatcher.log | grep " + char_id + "`",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Command router  — works in private chats AND group chats
# ═══════════════════════════════════════════════════════════════════════════════

HELP_TEXT = (
    "**GODMODE — command reference**\n\n"
    "`/godmode status`           — DB media health overview\n"
    "`/godmode reload`           — fix all broken / non-Catbox media\n"
    "`/godmode reload --force`   — re-upload everything (even working URLs)\n"
    "`/godmode retry`            — retry only previously-failed characters\n"
    "`/godmode fix <id>`         — re-process a single character\n"
    "`/godmode scan`             — verify every Catbox URL is alive (HTTP HEAD)\n"
    "`/godmode help`             — show this menu\n"
)


@app.on_message(filters.command("godmode"))
async def cmd_godmode(client, message: Message) -> None:
    # Ignore channel posts (no sender)
    if not message.from_user:
        return

    if not _is_sudo(message.from_user.id):
        return await message.reply_text("⛔ You are not authorised to use `/godmode`.")

    args = message.command[1:]
    sub  = args[0].lower() if args else "status"

    if sub == "status":
        await _godmode_status(message)

    elif sub == "reload":
        force = "--force" in args
        await _godmode_reload(message, force=force)

    elif sub == "retry":
        await _godmode_reload(message, force=False, retry_only=True)

    elif sub == "fix":
        if len(args) < 2:
            return await message.reply_text("❌ Usage: `/godmode fix <character_id>`")
        await _godmode_fix(message, args[1])

    elif sub == "scan":
        await _godmode_scan(message)

    elif sub in ("help", "?"):
        await message.reply_text(HELP_TEXT)

    else:
        await message.reply_text(
            f"❓ Unknown subcommand: `{sub}`\n\n" + HELP_TEXT
        )
