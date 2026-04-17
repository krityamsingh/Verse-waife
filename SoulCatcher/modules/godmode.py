"""SoulCatcher/modules/reloader.py  ─── GODMODE v3  ─────────────────────────
SOURCE OF TRUTH: The dump channel.

How it works
────────────
On first use of any /godmode command the bot does a **full scan** of
DUMP_CHANNEL (up to DUMP_SCAN_LIMIT messages).  For every media message it
reads the caption (expected format: "0042 | Toshiro Hitsugaya" or just an
integer ID anywhere in the caption) and builds a per-character registry:

    _registry[char_id] = {
        "name":      "Toshiro Hitsugaya",
        "photo_fid": "<file_id>",     # latest photo seen
        "video_fid": "<file_id>",     # latest video seen
        "photo_mid": 12345,           # message_id in dump channel
        "video_mid": 12346,
    }

Commands
────────
  /godmode status            — DB health overview + registry size
  /godmode list              — paginated list of all chars in registry
  /godmode list <query>      — search registry by name or ID
  /godmode fix <id>          — download media for <id> from dump channel,
                               upload to Catbox, patch MongoDB
  /godmode reload            — fix every char whose DB URL is not Catbox
  /godmode reload --force    — re-upload even working Catbox URLs
  /godmode retry             — retry only previously-failed characters
  /godmode scan              — HEAD-check every existing Catbox URL
  /godmode rescan            — rebuild the dump-channel registry from scratch
  /godmode help              — show this menu

Only OWNER_ID / SUDO_USERS may invoke.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
import re
import time
from dataclasses import dataclass, field
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

# ─── auth ─────────────────────────────────────────────────────────────────────
OWNER_ID   = 6118760915
SUDO_USERS: set[int] = {6118760915}

# ─── tunables ─────────────────────────────────────────────────────────────────
DUMP_CHANNEL      = -1003869604435
CATBOX_URL        = "https://catbox.moe/user/api.php"
CATBOX_CDN_PREFIX = "https://files.catbox.moe/"

DOWNLOAD_TIMEOUT  = aiohttp.ClientTimeout(total=90,  connect=15)
UPLOAD_TIMEOUT    = aiohttp.ClientTimeout(total=180, connect=15)
HEAD_TIMEOUT      = aiohttp.ClientTimeout(total=10,  connect=5)

MAX_RETRIES       = 4
CONCURRENCY       = 6
PROGRESS_EVERY    = 5
DUMP_SCAN_LIMIT   = 10_000   # messages to scan; raise if your dump channel is huge
MIN_EDIT_INTERVAL = 3.5
MAX_MSG_LEN       = 4_096

# Caption patterns we try in order:
#   "0042 | Toshiro Hitsugaya"
#   "ID: 42 – Name: Toshiro"
#   bare integer somewhere in caption
_CAP_PATTERNS = [
    re.compile(r"(\d+)\s*[\||\-|–|:]\s*(.+)"),   # "042 | Name" or "042 - Name"
    re.compile(r"id[:\s]+(\d+)[^\n]*name[:\s]+(.+)", re.IGNORECASE),
    re.compile(r"^\s*(\d+)\s*$", re.MULTILINE),   # bare integer line
    re.compile(r"\b(\d{2,6})\b"),                  # any 2-6 digit number
]

_TG_FILEID_RE      = re.compile(r"^[A-Za-z0-9_\-]{20,}$")
_TG_VIDEO_PREFIXES = ("BAA", "BQA", "BAAC")
_TG_PHOTO_PREFIXES = ("AgAC", "AAKC", "AQA")


# ═══════════════════════════════════════════════════════════════════════════════
# Registry — in-memory, rebuilt from dump channel on startup / rescan
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class CharMedia:
    name:      str  = ""
    photo_fid: str  = ""   # Telegram file_id
    video_fid: str  = ""
    photo_mid: int  = 0    # message_id in DUMP_CHANNEL
    video_mid: int  = 0

# char_id (str, zero-padded or raw) → CharMedia
_registry:     dict[str, CharMedia] = {}
_registry_built = False
_registry_lock  = asyncio.Lock()


def _parse_char_id_name(caption: str) -> tuple[str, str]:
    """
    Return (char_id_str, name) from a dump-channel message caption.
    char_id_str is stripped of leading zeros for DB lookup but kept as-is for
    the registry key so both "0042" and "42" resolve to the same entry.
    Returns ("", "") if nothing could be extracted.
    """
    if not caption:
        return "", ""
    caption = caption.strip()
    for pat in _CAP_PATTERNS:
        m = pat.search(caption)
        if m:
            raw_id = m.group(1).strip()
            name   = m.group(2).strip() if m.lastindex >= 2 else ""
            # normalise: strip leading zeros for numeric IDs
            try:
                norm_id = str(int(raw_id))
            except ValueError:
                norm_id = raw_id
            return norm_id, name
    return "", ""


async def _build_registry(
    status_msg: Optional[Message] = None,
    force:      bool = False,
) -> None:
    """
    Full scan of DUMP_CHANNEL.  Builds _registry mapping char_id → CharMedia.
    Each message caption is parsed for an ID and optional name.
    Photo/video file_ids are stored; if a char_id appears in multiple messages,
    the most recent photo and the most recent video win (channel is
    chronological, so later messages overwrite earlier ones).
    """
    global _registry_built

    async with _registry_lock:
        if _registry_built and not force:
            return

        if status_msg:
            await safe_edit(
                status_msg,
                "📡 **Scanning dump channel** — building character registry…\n"
                "_(This only runs once per bot session)_"
            )

        photo_count = 0
        video_count = 0
        unknown     = 0
        scanned     = 0

        try:
            async for msg in app.get_chat_history(DUMP_CHANNEL, limit=DUMP_SCAN_LIMIT):
                scanned += 1
                caption = msg.caption or msg.text or ""
                char_id, name = _parse_char_id_name(caption)

                media = msg.photo or msg.video or msg.document or msg.animation
                if not media:
                    continue

                if not char_id:
                    unknown += 1
                    continue

                # Upsert registry entry
                entry = _registry.setdefault(char_id, CharMedia(name=name))
                if name and not entry.name:
                    entry.name = name

                is_video = bool(msg.video or msg.animation or (
                    msg.document and (msg.document.mime_type or "").startswith("video")
                ))

                if is_video:
                    entry.video_fid = media.file_id
                    entry.video_mid = msg.id
                    video_count += 1
                else:
                    entry.photo_fid = media.file_id
                    entry.photo_mid = msg.id
                    photo_count += 1

                # Live scan progress every 500 messages
                if status_msg and scanned % 500 == 0:
                    await safe_edit(
                        status_msg,
                        f"📡 **Scanning dump channel…**\n\n"
                        f"Messages scanned : `{scanned:,}`\n"
                        f"Characters found : `{len(_registry):,}`\n"
                        f"Photos indexed   : `{photo_count:,}`\n"
                        f"Videos indexed   : `{video_count:,}`\n"
                        f"Unknown captions : `{unknown:,}`"
                    )

        except (ChannelInvalid, ChannelPrivate) as exc:
            raise ReloadError(
                f"Cannot access dump channel {DUMP_CHANNEL}: {exc}\n"
                "Ensure the bot is a member/admin of that channel."
            ) from exc
        except Exception as exc:
            log.exception("[registry] scan failed")
            raise ReloadError(f"Dump channel scan failed: {exc}") from exc

        _registry_built = True
        log.info(
            f"[registry] built — {len(_registry)} chars, "
            f"{photo_count} photos, {video_count} videos, "
            f"{scanned} messages scanned"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _is_sudo(uid: int) -> bool:
    return uid == OWNER_ID or uid in SUDO_USERS

def _is_working_catbox(url: str) -> bool:
    return isinstance(url, str) and url.startswith(CATBOX_CDN_PREFIX)

def _looks_like_file_id(value: str) -> bool:
    return bool(value and not value.startswith("http") and _TG_FILEID_RE.match(value))

def _mime_to_ext(content_type: str) -> str:
    ct  = content_type.split(";")[0].strip()
    ext = mimetypes.guess_extension(ct) or ".bin"
    return {".jpe": ".jpg", ".jpeg": ".jpg", ".jfif": ".jpg"}.get(ext, ext).lstrip(".")

def _guess_ext_from_file_id(fid: str) -> str:
    for p in _TG_VIDEO_PREFIXES:
        if fid.startswith(p): return "mp4"
    for p in _TG_PHOTO_PREFIXES:
        if fid.startswith(p): return "jpg"
    return "bin"

def _norm_id(raw: str) -> str:
    """Normalise a character ID string for registry/DB lookups."""
    try:
        return str(int(raw))
    except ValueError:
        return raw.strip()


class ReloadError(Exception):
    """Non-retryable failure for a single media item."""


# ═══════════════════════════════════════════════════════════════════════════════
# safe_edit — rate-limit-aware message editor
# ═══════════════════════════════════════════════════════════════════════════════

_edit_state: dict[int, tuple[str, float]] = {}
_edit_locks: dict[int, asyncio.Lock]      = {}

def _elock(mid: int) -> asyncio.Lock:
    if mid not in _edit_locks:
        _edit_locks[mid] = asyncio.Lock()
    return _edit_locks[mid]

async def safe_edit(msg: Message, text: str) -> None:
    if len(text) > MAX_MSG_LEN:
        cut  = text.rfind("\n", 0, MAX_MSG_LEN - 80)
        cut  = cut if cut > 0 else MAX_MSG_LEN - 80
        text = text[:cut] + "\n\n…_(truncated — see logs)_"
    mid = msg.id
    async with _elock(mid):
        now = time.monotonic()
        last_text, last_ts = _edit_state.get(mid, ("", 0.0))
        if text == last_text:
            return
        wait = MIN_EDIT_INTERVAL - (now - last_ts)
        if wait > 0:
            await asyncio.sleep(wait)
        for attempt in range(3):
            try:
                await msg.edit_text(text)
                _edit_state[mid] = (text, time.monotonic())
                return
            except FloodWait as fw:
                log.warning(f"[safe_edit] FloodWait {fw.value}s")
                await asyncio.sleep(fw.value + 1)
            except (MessageNotModified, MessageIdInvalid):
                _edit_state[mid] = (text, time.monotonic())
                return
            except Exception as exc:
                log.warning(f"[safe_edit] attempt {attempt}: {exc}")
                if attempt < 2: await asyncio.sleep(2)
                return


# ═══════════════════════════════════════════════════════════════════════════════
# Telegram download  (file_id → bytes)
# ═══════════════════════════════════════════════════════════════════════════════

async def _tg_download_fid(file_id: str) -> bytes:
    """Download a Telegram media item by file_id (current session)."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            buf = BytesIO()
            await app.download_media(file_id, in_memory=True, file_name=buf)
            buf.seek(0)
            data = buf.read()
            if data:
                return data
            raise ReloadError("Empty download from Telegram")
        except FileIdInvalid:
            raise ReloadError(f"file_id expired/invalid: {file_id[:24]}…")
        except FloodWait as fw:
            await asyncio.sleep(fw.value + 1)
        except ReloadError:
            raise
        except RPCError as exc:
            log.warning(f"[tg-dl] RPC {exc} attempt {attempt}/{MAX_RETRIES}")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(2 ** attempt)
    raise ReloadError(f"Telegram download failed after {MAX_RETRIES} attempts")


async def _tg_download_msg(msg_id: int) -> tuple[bytes, str]:
    """
    Download media from a specific dump-channel message (more reliable —
    fetches a fresh file_id from the live message, not the cached one).
    Returns (data, ext).
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            dump_msg = await app.get_messages(DUMP_CHANNEL, msg_id)
            media    = (dump_msg.photo or dump_msg.video
                        or dump_msg.document or dump_msg.animation)
            if not media:
                raise ReloadError(f"Dump msg {msg_id} has no media")
            fresh_fid = media.file_id
            ext = "mp4" if (dump_msg.video or dump_msg.animation) else "jpg"
            data = await _tg_download_fid(fresh_fid)
            if data:
                log.info(f"[tg-dl-msg] msg {msg_id} OK ({len(data):,} bytes)")
                return data, ext
        except FloodWait as fw:
            await asyncio.sleep(fw.value + 1)
        except ReloadError:
            raise
        except Exception as exc:
            log.warning(f"[tg-dl-msg] attempt {attempt}: {exc}")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(2 ** attempt)
    raise ReloadError(f"Dump-channel message {msg_id} download failed")


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP download  (URL → bytes)
# ═══════════════════════════════════════════════════════════════════════════════

async def _http_download(session: aiohttp.ClientSession, url: str) -> tuple[bytes, str]:
    ext = "jpg"
    try:
        async with session.head(url, timeout=HEAD_TIMEOUT, allow_redirects=True) as r:
            if r.status == 404:
                raise ReloadError(f"404 Not Found: {url}")
            ext = _mime_to_ext(r.headers.get("Content-Type", "image/jpeg"))
    except ReloadError:
        raise
    except Exception:
        pass   # HEAD failed — try GET anyway

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with session.get(url, timeout=DOWNLOAD_TIMEOUT, allow_redirects=True) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    if data:
                        ct = resp.headers.get("Content-Type", "")
                        if ct: ext = _mime_to_ext(ct)
                        return data, ext
                    raise ReloadError(f"Empty body: {url}")
                if resp.status in (403, 404, 410):
                    raise ReloadError(f"HTTP {resp.status} — gone: {url}")
                raise ValueError(f"HTTP {resp.status}")
        except ReloadError:
            raise
        except asyncio.TimeoutError:
            log.warning(f"[http-dl] timeout attempt {attempt}: {url}")
        except Exception as exc:
            log.warning(f"[http-dl] attempt {attempt}: {exc}")
        if attempt < MAX_RETRIES:
            await asyncio.sleep(2 ** attempt)
    raise ReloadError(f"HTTP download failed after {MAX_RETRIES} attempts: {url}")


# ═══════════════════════════════════════════════════════════════════════════════
# Catbox upload
# ═══════════════════════════════════════════════════════════════════════════════

async def _upload_catbox(
    session:  aiohttp.ClientSession,
    data:     bytes,
    filename: str,
) -> str:
    md5 = hashlib.md5(data).hexdigest()[:8]
    log.debug(f"[catbox] uploading {filename} ({len(data):,} B  md5={md5})")

    for attempt in range(1, MAX_RETRIES + 1):
        form = aiohttp.FormData()
        form.add_field("reqtype",  "fileupload")
        form.add_field("userhash", "")
        form.add_field(
            "fileToUpload", BytesIO(data),
            filename=filename,
            content_type="application/octet-stream",
        )
        try:
            async with session.post(CATBOX_URL, data=form, timeout=UPLOAD_TIMEOUT) as resp:
                body = (await resp.text()).strip()
                if resp.status == 200 and body.startswith("https://"):
                    log.info(f"[catbox] ✓ {filename} → {body}")
                    return body
                raise ReloadError(f"Catbox {resp.status}: {body[:120]}")
        except ReloadError:
            raise
        except asyncio.TimeoutError:
            log.warning(f"[catbox] timeout attempt {attempt}")
        except Exception as exc:
            log.warning(f"[catbox] attempt {attempt}: {exc}")
        if attempt < MAX_RETRIES:
            await asyncio.sleep(3 * attempt)

    raise ReloadError(f"Catbox upload failed after {MAX_RETRIES} attempts: {filename}")


# ═══════════════════════════════════════════════════════════════════════════════
# Core: resolve one media field for one character
# ═══════════════════════════════════════════════════════════════════════════════

async def _resolve_media(
    session:   aiohttp.ClientSession,
    char_id:   str,
    field:     str,          # "img_url" | "video_url"
    current:   str,          # current DB value (may be file_id, broken URL, etc.)
    safe_name: str,
    entry:     Optional[CharMedia] = None,
) -> str:
    """
    Resolution priority:
      1. Use dump-channel message_id (fresh file_id from live message) — most reliable
      2. Use dump-channel file_id (cached, may be expired)
      3. Fall back to HTTP download of current URL (if it's a URL, not a file_id)
    Returns the new Catbox URL.
    """
    is_video = (field == "video_url")
    ext_hint = "mp4" if is_video else "jpg"
    data: Optional[bytes] = None
    ext  = ext_hint

    # ── Priority 1: registry message_id → fresh download ────────────────────
    if entry:
        mid = entry.video_mid if is_video else entry.photo_mid
        fid = entry.video_fid if is_video else entry.photo_fid

        if mid:
            log.info(f"[{char_id}:{field}] strategy=dump_msg mid={mid}")
            try:
                data, ext = await _tg_download_msg(mid)
            except ReloadError as exc:
                log.warning(f"[{char_id}:{field}] dump_msg failed: {exc}")

        # ── Priority 2: registry file_id ─────────────────────────────────────
        if data is None and fid:
            log.info(f"[{char_id}:{field}] strategy=dump_fid fid={fid[:20]}…")
            try:
                data = await _tg_download_fid(fid)
            except ReloadError as exc:
                log.warning(f"[{char_id}:{field}] dump_fid failed: {exc}")

    # ── Priority 3: current DB value is a file_id ────────────────────────────
    if data is None and _looks_like_file_id(current):
        log.info(f"[{char_id}:{field}] strategy=db_fid")
        try:
            data = await _tg_download_fid(current)
            ext  = _guess_ext_from_file_id(current)
        except ReloadError as exc:
            log.warning(f"[{char_id}:{field}] db_fid failed: {exc}")

    # ── Priority 4: current DB value is an HTTP URL ───────────────────────────
    if data is None and current.startswith("http"):
        log.info(f"[{char_id}:{field}] strategy=http_url {current[:60]}")
        try:
            data, ext = await _http_download(session, current)
        except ReloadError as exc:
            log.warning(f"[{char_id}:{field}] http_url failed: {exc}")

    if data is None:
        raise ReloadError(
            f"All download strategies exhausted for {char_id}:{field}.\n"
            f"  DB value: {current[:60]}\n"
            f"  Registry entry: {entry}"
        )

    ext = (ext or ext_hint).lstrip(".")
    if ext in ("bin", ""):
        ext = ext_hint

    filename  = f"{safe_name}_{field.split('_')[0]}.{ext}"
    catbox_url = await _upload_catbox(session, data, filename)
    return catbox_url


# ═══════════════════════════════════════════════════════════════════════════════
# Per-character processor
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ReloadResult:
    char_id:        str
    success:        bool       = False
    skipped:        bool       = False
    error:          str        = ""
    fields_updated: list[str]  = field(default_factory=list)


async def _process_one(
    session: aiohttp.ClientSession,
    char:    dict,
    force:   bool = False,
) -> ReloadResult:
    char_id   = _norm_id(str(char["id"]))
    safe_name = re.sub(r"[^\w\-]", "_", f"{char_id}_{char.get('name', 'char')}")

    img_url = (char.get("img_url")   or "").strip()
    vid_url = (char.get("video_url") or "").strip()

    # Get registry entry for this character (may be None if not in dump channel)
    entry = _registry.get(char_id)

    update:  dict     = {}
    updated: list[str] = []
    errors:  list[str] = []

    for field_name, current_url in (("img_url", img_url), ("video_url", vid_url)):
        if not current_url and not (
            entry and (entry.photo_fid if field_name == "img_url" else entry.video_fid)
        ):
            continue   # nothing in DB and nothing in registry — skip

        if not force and _is_working_catbox(current_url):
            continue   # already good

        try:
            new_url = await _resolve_media(
                session, char_id, field_name,
                current_url, safe_name, entry
            )
            update[field_name] = new_url
            updated.append(field_name)
        except ReloadError as exc:
            log.error(f"[{char_id}:{field_name}] FAILED: {exc}")
            errors.append(f"{field_name}: {exc}")
        except Exception as exc:
            log.exception(f"[{char_id}] unexpected")
            errors.append(f"{field_name}: unexpected — {exc}")

    if errors and not update:
        await _col("characters").update_one(
            {"id": char_id},
            {"$set": {"_reload_failed": True, "_reload_error": "; ".join(errors)}},
        )
        return ReloadResult(char_id, error="; ".join(errors))

    if update:
        update["_reload_failed"] = False
        update["_reload_error"]  = ""
        await _col("characters").update_one({"id": char_id}, {"$set": update})
        log.info(f"[{char_id}] DB patched — {updated}")
        return ReloadResult(char_id, success=True, fields_updated=updated)

    return ReloadResult(char_id, skipped=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Bulk engine
# ═══════════════════════════════════════════════════════════════════════════════

async def _bulk_reload(
    status_msg: Message,
    force:      bool = False,
    retry_only: bool = False,
) -> tuple[int, int, int, list[str]]:
    # Ensure registry is built first
    try:
        await _build_registry(status_msg)
    except ReloadError as exc:
        await safe_edit(status_msg, f"⚠️ **Registry warning:**\n`{exc}`\n\nContinuing…")
        await asyncio.sleep(2)

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

    chars = await _col("characters").find(query).to_list(None)
    total = len(chars)

    if total == 0:
        await safe_edit(
            status_msg,
            "✅ **Nothing to do!**\n\n"
            "All enabled characters already have working Catbox URLs.\n"
            "Run `/godmode scan` to verify each URL is still alive.",
        )
        return 0, 0, 0, []

    sem        = asyncio.Semaphore(CONCURRENCY)
    success = skipped = failed = done = 0
    failed_ids: list[str] = []
    start_ts = time.time()

    conn = aiohttp.TCPConnector(limit=CONCURRENCY * 3, ssl=False, ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=conn) as session:

        async def _worker(char: dict) -> None:
            nonlocal success, skipped, failed, done
            async with sem:
                result = await _process_one(session, char, force=force)
                done += 1
                if result.success:   success += 1
                elif result.skipped: skipped += 1
                else:
                    failed += 1
                    failed_ids.append(f"`{result.char_id}` — {result.error[:80]}")

                if done % PROGRESS_EVERY == 0 or done == total:
                    el   = time.time() - start_ts
                    eta  = (el / done) * (total - done) if done else 0
                    pct  = int(done / total * 100)
                    bar  = "█" * (pct // 5) + "░" * (20 - pct // 5)
                    rate = done / el if el else 0
                    await safe_edit(
                        status_msg,
                        f"⚙️ **GODMODE RELOAD** in progress…\n\n"
                        f"`[{bar}]` {pct}%\n\n"
                        f"📊 **{done}** / **{total}** processed\n"
                        f"✅ Fixed    : **{success}**\n"
                        f"⏭ Skipped  : **{skipped}**\n"
                        f"❌ Failed   : **{failed}**\n\n"
                        f"⚡ Rate     : `{rate:.1f}/s`\n"
                        f"⏱ Elapsed  : `{el:.0f}s`  |  ETA: `{eta:.0f}s`",
                    )

        await asyncio.gather(*[_worker(c) for c in chars])

    return success, skipped, failed, failed_ids


# ═══════════════════════════════════════════════════════════════════════════════
# /godmode list  — browse the registry
# ═══════════════════════════════════════════════════════════════════════════════

async def _godmode_list(message: Message, query: str = "") -> None:
    await _ensure_registry(message)

    if query:
        ql = query.lower()
        matches = {
            cid: e for cid, e in sorted(_registry.items())
            if ql in e.name.lower() or ql in cid
        }
    else:
        matches = dict(sorted(_registry.items()))

    if not matches:
        return await message.reply_text(
            f"🔍 No characters matching `{query}` found in dump-channel registry."
        )

    # Paginate: show up to 50 at a time
    items   = list(matches.items())
    total   = len(items)
    page    = items[:50]

    lines = [f"📋 **Registry** ({total} entries{f' matching `{query}`' if query else ''})\n"]
    for cid, e in page:
        photo = "📸" if e.photo_fid or e.photo_mid else "✗"
        video = "🎬" if e.video_fid or e.video_mid else "✗"
        name  = e.name or "_(unnamed)_"
        lines.append(f"`{cid:>5}` {photo}{video}  {name}")

    if total > 50:
        lines.append(f"\n…and **{total - 50}** more. Narrow with `/godmode list <query>`.")

    await message.reply_text("\n".join(lines))


# ═══════════════════════════════════════════════════════════════════════════════
# /godmode fix <id>  — the star of the show
# ═══════════════════════════════════════════════════════════════════════════════

async def _godmode_fix(message: Message, raw_id: str) -> None:
    char_id = _norm_id(raw_id)

    # ── Ensure registry is loaded ────────────────────────────────────────────
    msg = await message.reply_text(
        f"🔍 Looking up `{char_id}`…"
    )
    await _ensure_registry(msg)

    # ── Resolve character ────────────────────────────────────────────────────
    char = await get_character(char_id)

    # Also check padded IDs (DB stores "0042", we normalise to "42")
    if not char:
        for padlen in (4, 5, 6):
            char = await get_character(char_id.zfill(padlen))
            if char:
                char_id = _norm_id(str(char["id"]))
                break

    entry = _registry.get(char_id)

    # ── Neither in DB nor in registry → error ────────────────────────────────
    if not char and not entry:
        return await safe_edit(
            msg,
            f"❌ Character `{char_id}` not found in **database** or **dump-channel registry**.\n\n"
            f"Run `/godmode list {char_id}` to search the registry.\n"
            f"Run `/godmode rescan` if the dump channel was recently updated."
        )

    # Use registry name if DB has no record yet
    display_name = (char.get("name") if char else None) or (entry.name if entry else "?")

    current_img = (char.get("img_url")   or "").strip() if char else ""
    current_vid = (char.get("video_url") or "").strip() if char else ""

    registry_info = ""
    if entry:
        registry_info = (
            f"\n\n**Dump-channel registry:**\n"
            f"  📸 photo  msg_id=`{entry.photo_mid}`  fid=`{(entry.photo_fid or '—')[:24]}…`\n"
            f"  🎬 video  msg_id=`{entry.video_mid}`  fid=`{(entry.video_fid or '—')[:24]}…`"
        )
    else:
        registry_info = "\n\n⚠️ Character **not** in dump-channel registry — will attempt DB value only."

    await safe_edit(
        msg,
        f"🔧 **Fixing** `{char_id}` — **{display_name}**\n\n"
        f"**Current DB values:**\n"
        f"  📸 `img_url`   : `{current_img[:70] or '(none)'}`\n"
        f"  🎬 `video_url` : `{current_vid[:70] or '(none)'}`"
        + registry_info
        + "\n\n⏳ Downloading & uploading to Catbox…"
    )

    # ── If no DB record, create a stub so _process_one can write to it ───────
    if not char:
        char = {"id": char_id, "name": display_name, "img_url": "", "video_url": ""}

    conn = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=conn) as session:
        result = await _process_one(session, char, force=True)

    updated_char = await get_character(char_id) or char

    # ── Format result ────────────────────────────────────────────────────────
    if result.success:
        new_img = (updated_char.get("img_url")   or "—")[:80]
        new_vid = (updated_char.get("video_url") or "—")[:80]
        await safe_edit(
            msg,
            f"✅ **Fixed** `{char_id}` — **{display_name}**\n\n"
            f"**Updated fields:** `{'`, `'.join(result.fields_updated)}`\n\n"
            f"📸 `img_url`   : `{new_img}`\n"
            f"🎬 `video_url` : `{new_vid}`\n\n"
            f"DB patched with stable Catbox URLs ✓",
        )

    elif result.skipped:
        await safe_edit(
            msg,
            f"⏭ `{char_id}` — **{display_name}**\n\n"
            "Both media fields already point to Catbox — nothing to do.\n\n"
            "_Use `/godmode fix " + char_id + " --force` to re-upload anyway._",
        )

    else:
        await safe_edit(
            msg,
            f"❌ **Failed** `{char_id}` — **{display_name}**\n\n"
            f"**Error:**\n`{result.error}`\n\n"
            "**What to check:**\n"
            "• Is the media still present in the dump channel?\n"
            "• Was the dump channel migrated / recreated (new channel ID)?\n"
            "• Is the bot still a member of the dump channel?\n"
            "• Is Catbox reachable?  (`/godmode scan` to check)\n\n"
            f"Logs: `grep '\\[{char_id}\\]' soulcatcher.log | tail -30`",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# /godmode scan  — HEAD-check all existing Catbox URLs
# ═══════════════════════════════════════════════════════════════════════════════

async def _godmode_scan(message: Message) -> None:
    msg = await message.reply_text(
        "🔬 **GODMODE SCAN** — verifying all Catbox URLs…\n"
        "_(HTTP HEAD check on every stored URL)_"
    )
    chars = await _col("characters").find({
        "enabled": True,
        "$or": [
            {"img_url":   {"$regex": r"^https://files\.catbox\.moe/"}},
            {"video_url": {"$regex": r"^https://files\.catbox\.moe/"}},
        ]
    }).to_list(None)

    total = len(chars)
    alive = dead = done = 0
    dead_ids: list[str] = []
    sem = asyncio.Semaphore(20)

    async def _hcheck(session: aiohttp.ClientSession, url: str) -> bool:
        try:
            async with session.head(url, timeout=HEAD_TIMEOUT, allow_redirects=True) as r:
                return r.status == 200
        except Exception:
            return False

    conn = aiohttp.TCPConnector(limit=40, ssl=False)
    async with aiohttp.ClientSession(connector=conn) as session:

        async def _check(char: dict) -> None:
            nonlocal alive, dead, done
            async with sem:
                cid = _norm_id(str(char["id"]))
                for fkey, label in (("img_url", "img"), ("video_url", "vid")):
                    url = (char.get(fkey) or "").strip()
                    if not _is_working_catbox(url): continue
                    ok = await _hcheck(session, url)
                    if ok:
                        alive += 1
                    else:
                        dead  += 1
                        dead_ids.append(f"`{cid}` {label}: `{url.split('/')[-1]}`")
                        await _col("characters").update_one(
                            {"id": cid},
                            {"$set": {"_reload_failed": True, "_reload_error": f"{fkey} 404"}},
                        )
                done += 1
                if done % 50 == 0 or done == total:
                    pct = int(done / total * 100)
                    bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
                    await safe_edit(
                        msg,
                        f"🔬 **SCAN** `[{bar}]` {pct}%\n\n"
                        f"📊 {done}/{total}  |  ✅ alive: **{alive}**  |  💀 dead: **{dead}**",
                    )

        await asyncio.gather(*[_check(c) for c in chars])

    dead_block = ""
    if dead_ids:
        sample = dead_ids[:20]
        dead_block = "\n\n**Dead URLs:**\n" + "\n".join(sample)
        if dead > 20:
            dead_block += f"\n…and **{dead - 20}** more."

    await safe_edit(
        msg,
        "╔══════════════════════════════╗\n"
        "║   🔬  SCAN  COMPLETE         ║\n"
        "╚══════════════════════════════╝\n\n"
        f"✅ Alive     : **{alive:,}**\n"
        f"💀 Dead/404  : **{dead:,}**\n"
        + dead_block
        + ("\n\n💡 Run `/godmode retry` to re-upload dead URLs." if dead else "\n\n🎉 All Catbox URLs alive!"),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# /godmode status
# ═══════════════════════════════════════════════════════════════════════════════

async def _godmode_status(message: Message) -> None:
    msg = await message.reply_text("🔍 **GODMODE STATUS** — scanning…")
    await _ensure_registry(msg)

    total        = await _col("characters").count_documents({"enabled": True})
    last_failed  = await _col("characters").count_documents({"enabled": True, "_reload_failed": True})
    no_media     = 0
    catbox_p = catbox_v = fileid_p = fileid_v = broken_p = broken_v = 0

    async for char in _col("characters").find({"enabled": True}):
        img = (char.get("img_url")   or "").strip()
        vid = (char.get("video_url") or "").strip()
        if not img and not vid:
            no_media += 1
            continue
        for val, is_v in ((img, False), (vid, True)):
            if not val: continue
            if _is_working_catbox(val):
                if is_v: catbox_v += 1
                else:    catbox_p += 1
            elif _looks_like_file_id(val):
                if is_v: fileid_v += 1
                else:    fileid_p += 1
            else:
                if is_v: broken_v += 1
                else:    broken_p += 1

    needs_work = fileid_p + fileid_v + broken_p + broken_v

    # Registry stats
    reg_total   = len(_registry)
    reg_w_photo = sum(1 for e in _registry.values() if e.photo_fid or e.photo_mid)
    reg_w_video = sum(1 for e in _registry.values() if e.video_fid or e.video_mid)

    await safe_edit(
        msg,
        "╔══════════════════════════════╗\n"
        "║    🛡  GODMODE  STATUS       ║\n"
        "╚══════════════════════════════╝\n\n"
        f"📦 DB characters        : **{total:,}**\n"
        f"🚫 No media at all      : **{no_media:,}**\n"
        f"🔴 Last-run failures    : **{last_failed:,}**\n\n"
        "**Photo (img_url)**\n"
        f"  ✅ Catbox URL         : **{catbox_p:,}**\n"
        f"  🔁 Telegram file_id  : **{fileid_p:,}**\n"
        f"  💔 Broken / raw      : **{broken_p:,}**\n\n"
        "**Video (video_url)**\n"
        f"  ✅ Catbox URL         : **{catbox_v:,}**\n"
        f"  🔁 Telegram file_id  : **{fileid_v:,}**\n"
        f"  💔 Broken / raw      : **{broken_v:,}**\n\n"
        "**Dump-channel registry**\n"
        f"  📋 Characters indexed : **{reg_total:,}**\n"
        f"  📸 Have photo media   : **{reg_w_photo:,}**\n"
        f"  🎬 Have video media   : **{reg_w_video:,}**\n\n"
        + (
            f"⚠️ **{needs_work:,}** character(s) need fixing.\n"
            "Run `/godmode reload` to fix all."
            if needs_work else
            "🎉 **All media on Catbox!**  Run `/godmode scan` to verify URLs are alive."
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# /godmode reload
# ═══════════════════════════════════════════════════════════════════════════════

async def _godmode_reload(
    message:    Message,
    force:      bool = False,
    retry_only: bool = False,
) -> None:
    note = (
        " _(retrying failed characters)_" if retry_only else
        " _(force — re-uploading everything)_" if force else ""
    )
    status_msg = await message.reply_text(
        f"🚀 **GODMODE RELOAD** starting…{note}\n\n"
        "Building dump-channel registry…"
    )
    try:
        success, skipped, failed, failed_ids = await _bulk_reload(
            status_msg, force=force, retry_only=retry_only
        )
    except Exception as exc:
        log.exception("_bulk_reload crashed")
        return await safe_edit(status_msg, f"💥 **Fatal error:**\n`{exc}`")

    fail_block = ""
    if failed_ids:
        sample = failed_ids[:20]
        more   = failed - len(sample)
        fail_block = "\n\n**Failed:**\n" + "\n".join(sample)
        if more: fail_block += f"\n…and **{more}** more."

    footer = (
        "\n\n💡 Run `/godmode retry` to re-attempt failures.\nRun `/godmode scan` to verify URLs."
        if failed else
        "\n\n🎉 All broken media now on stable Catbox URLs!"
    )

    await safe_edit(
        status_msg,
        "╔══════════════════════════════╗\n"
        "║   ✅  RELOAD  COMPLETE       ║\n"
        "╚══════════════════════════════╝\n\n"
        f"✅ Fixed     : **{success:,}**\n"
        f"⏭ Skipped   : **{skipped:,}**\n"
        f"❌ Failed    : **{failed:,}**\n"
        + fail_block + footer,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Shared helper: ensure registry is ready, show user a nice message if building
# ═══════════════════════════════════════════════════════════════════════════════

async def _ensure_registry(status_msg: Optional[Message] = None) -> None:
    if not _registry_built:
        await _build_registry(status_msg)


# ═══════════════════════════════════════════════════════════════════════════════
# Command router
# ═══════════════════════════════════════════════════════════════════════════════

HELP_TEXT = (
    "**GODMODE — command reference**\n\n"
    "`/godmode status`            — DB + registry health overview\n"
    "`/godmode list`              — list all characters in dump-channel registry\n"
    "`/godmode list <query>`      — search registry by name or ID\n"
    "`/godmode fix <id>`          — download from dump channel, upload to Catbox, patch DB\n"
    "`/godmode reload`            — fix all broken / non-Catbox media\n"
    "`/godmode reload --force`    — re-upload everything\n"
    "`/godmode retry`             — retry only previously-failed characters\n"
    "`/godmode scan`              — HEAD-check every existing Catbox URL\n"
    "`/godmode rescan`            — rebuild dump-channel registry from scratch\n"
    "`/godmode help`              — this menu\n"
)


@app.on_message(filters.command("godmode"))
async def cmd_godmode(client, message: Message) -> None:
    if not message.from_user:
        return
    if not _is_sudo(message.from_user.id):
        return await message.reply_text("⛔ Not authorised.")

    args = message.command[1:]
    sub  = (args[0].lower() if args else "status")

    if sub == "status":
        await _godmode_status(message)

    elif sub == "list":
        query = " ".join(args[1:]).strip()
        await _godmode_list(message, query)

    elif sub == "fix":
        if len(args) < 2:
            return await message.reply_text("❌ Usage: `/godmode fix <character_id>`")
        await _godmode_fix(message, args[1])

    elif sub == "reload":
        force = "--force" in args
        await _godmode_reload(message, force=force)

    elif sub == "retry":
        await _godmode_reload(message, retry_only=True)

    elif sub == "scan":
        await _godmode_scan(message)

    elif sub == "rescan":
        global _registry_built
        _registry_built = False
        _registry.clear()
        msg = await message.reply_text("🔄 **Registry cleared.** Rebuilding from dump channel…")
        await _build_registry(msg)
        await safe_edit(
            msg,
            f"✅ **Registry rebuilt.**\n\n"
            f"📋 Characters indexed : **{len(_registry):,}**\n"
            f"📸 With photo         : **{sum(1 for e in _registry.values() if e.photo_fid or e.photo_mid):,}**\n"
            f"🎬 With video         : **{sum(1 for e in _registry.values() if e.video_fid or e.video_mid):,}**",
        )

    elif sub in ("help", "?"):
        await message.reply_text(HELP_TEXT)

    else:
        await message.reply_text(f"❓ Unknown: `{sub}`\n\n" + HELP_TEXT)
