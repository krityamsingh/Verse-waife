"""
SoulCatcher — wish.py  ✨ Wish Granter System
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Rules
  • /wish can ONLY be used in the bot's private DM — not in groups
  • The user must have started the bot (/start in DM) at least once
  • If they haven't started the bot, they get a deep-link button to do so

User Commands  (private DM only)
  /wish   <char_id>   — send a wish request to the owner
  /wishlist           — view your wishlist + pending status
  /unwish <char_id>   — remove from wishlist + cancel pending request

Owner Commands
  /wishqueue          — view all pending wish requests

Owner Callbacks  (in owner DM)
  wg_approve:<req_id>:<owner_id>  — grant the wish
  wg_deny:<req_id>:<owner_id>     — deny the wish

Flow
  1. User sends /wish <char_id>  IN BOT DM
  2. Guards: must have started bot · can't already own · no duplicate · max 3 pending
  3. Wish-request card (character photo/video) sent to every owner DM
     with [✅ Approve] [❌ Deny] inline buttons
  4. Owner taps ✅ → character added to harem → user gets "Wish Granted 🌟" DM
     with the character media
  5. Owner taps ❌ → user gets polite denial DM with character picture
  6. All requests tracked in wish_requests collection
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
from __future__ import annotations

import logging
import os
import tempfile
import uuid
from datetime import datetime
from typing import Optional

import aiohttp
from pyrogram import enums, filters
from pyrogram.types import (
    InlineKeyboardButton as IKB,
    InlineKeyboardMarkup as IKM,
    Message,
)

from .. import app
from ..config import OWNER_IDS, BOT_USERNAME
from ..database import (
    _col,
    add_to_harem,
    add_wish,
    get_character,
    get_or_create_user,
    get_wishlist,
    remove_wish,
)
from ..rarity import get_rarity

log = logging.getLogger("SoulCatcher.wish")

DOWNLOAD_TIMEOUT = 90
MAX_WISH_PENDING = 3     # max simultaneous pending wish requests per user


# ─────────────────────────────────────────────────────────────────────────────
#  Bot-started tracking
#  Stored as has_started=True on the users collection doc.
#  Set whenever the user sends /start in the bot's private DM.
#  wish.py reads this flag — missing = user has never opened the bot DM.
# ─────────────────────────────────────────────────────────────────────────────

async def _mark_started(user_id: int) -> None:
    """Mark user as having started the bot in DM. Called on /start in private."""
    await _col("users").update_one(
        {"user_id": user_id},
        {"$set": {"has_started": True}},
        upsert=True,
    )


async def _has_started(user_id: int) -> bool:
    """Return True if user has ever sent /start in bot DM."""
    doc = await _col("users").find_one({"user_id": user_id}, {"has_started": 1})
    return bool(doc and doc.get("has_started"))


# ─────────────────────────────────────────────────────────────────────────────
#  Wish-request DB helpers
#  Collection: wish_requests
#  Schema:
#    _req_id      str       UUID8 primary key
#    user_id      int       requester telegram ID
#    user_name    str       display name at request time
#    char_id      str       character catalogue ID
#    char_name    str
#    anime        str
#    rarity       str       rarity key
#    img_url      str
#    video_url    str
#    status       str       "pending" | "approved" | "denied" | "cancelled"
#    requested_at datetime
#    resolved_at  datetime | None
#    resolved_by  int       owner_id who actioned it | None
# ─────────────────────────────────────────────────────────────────────────────

def _wreqs():
    return _col("wish_requests")


async def _create_request(user_id: int, user_name: str, char: dict) -> str:
    req_id = str(uuid.uuid4())[:8].upper()
    await _wreqs().insert_one({
        "_req_id":      req_id,
        "user_id":      user_id,
        "user_name":    user_name,
        "char_id":      char["id"],
        "char_name":    char["name"],
        "anime":        char.get("anime", "Unknown"),
        "rarity":       char.get("rarity", "common"),
        "img_url":      char.get("img_url", ""),
        "video_url":    char.get("video_url", ""),
        "status":       "pending",
        "requested_at": datetime.utcnow(),
        "resolved_at":  None,
        "resolved_by":  None,
    })
    return req_id


async def _get_request(req_id: str) -> Optional[dict]:
    return await _wreqs().find_one({"_req_id": req_id})


async def _resolve_request(req_id: str, owner_id: int, status: str) -> bool:
    """Atomically mark a pending request resolved. Returns False if already done."""
    res = await _wreqs().update_one(
        {"_req_id": req_id, "status": "pending"},
        {"$set": {
            "status":      status,
            "resolved_at": datetime.utcnow(),
            "resolved_by": owner_id,
        }},
    )
    return res.modified_count > 0


async def _count_pending(user_id: int) -> int:
    return await _wreqs().count_documents({"user_id": user_id, "status": "pending"})


async def _already_requested(user_id: int, char_id: str) -> bool:
    return bool(await _wreqs().find_one({
        "user_id": user_id,
        "char_id": char_id,
        "status":  "pending",
    }))


# ─────────────────────────────────────────────────────────────────────────────
#  Media helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _download(url: str, suffix: str) -> str:
    async with aiohttp.ClientSession() as sess:
        async with sess.get(
            url,
            timeout=aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT),
            headers={"User-Agent": "Mozilla/5.0"},
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status} from {url!r}")
            data = await resp.read()
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(data)
    finally:
        tmp.close()
    return tmp.name


def _rm(path: Optional[str]) -> None:
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass


def _is_video(url: str) -> bool:
    return bool(url) and url.lower().split("?")[0].endswith(
        (".mp4", ".mkv", ".webm", ".mov", ".avi", ".gif")
    )


async def _send_char_media(
    client,
    chat_id: int,
    char: dict,
    caption: str,
    markup: Optional[IKM] = None,
) -> None:
    """
    Send character photo or video to chat_id.
    Downloads to temp file first to avoid Telegram CDN rejections.
    Falls back to plain text on any failure.
    """
    vid = char.get("video_url", "")
    img = char.get("img_url", "")
    tmp: Optional[str] = None

    try:
        if vid and _is_video(vid):
            tmp = await _download(vid, ".mp4")
            with open(tmp, "rb") as fh:
                await client.send_video(
                    chat_id, fh,
                    caption=caption,
                    reply_markup=markup,
                    parse_mode=enums.ParseMode.HTML,
                )
            return
        elif img:
            tmp = await _download(img, ".jpg")
            with open(tmp, "rb") as fh:
                await client.send_photo(
                    chat_id, fh,
                    caption=caption,
                    reply_markup=markup,
                    parse_mode=enums.ParseMode.HTML,
                )
            return
    except Exception as exc:
        log.warning("media send failed (chat=%d char=%s): %s", chat_id, char.get("id"), exc)
    finally:
        _rm(tmp)

    # Last-resort plain text
    try:
        await client.send_message(
            chat_id, caption,
            reply_markup=markup,
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as exc:
        log.error("text fallback also failed (chat=%d): %s", chat_id, exc)


# ─────────────────────────────────────────────────────────────────────────────
#  Rarity helper
# ─────────────────────────────────────────────────────────────────────────────

def _rarity_line(rarity_key: str) -> str:
    tier = get_rarity(rarity_key)
    return f"{tier.emoji} <b>{tier.display_name}</b>" if tier else f"❓ {rarity_key.title()}"


# ─────────────────────────────────────────────────────────────────────────────
#  Owner wish-request card builders
# ─────────────────────────────────────────────────────────────────────────────

def _owner_caption(req: dict) -> str:
    tier    = get_rarity(req["rarity"])
    r_emoji = tier.emoji if tier else "❓"
    r_name  = tier.display_name if tier else req["rarity"].title()
    vid_tag = " 🎬" if req.get("video_url") else ""
    return (
        f"✨ <b>New Wish Request</b>\n"
        f"{'─' * 30}\n"
        f"👤  <b>{req['user_name']}</b>  <code>{req['user_id']}</code>\n"
        f"🆔  Request: <code>{req['_req_id']}</code>\n"
        f"{'─' * 30}\n"
        f"🌸  <b>{req['char_name']}</b>{vid_tag}  <code>{req['char_id']}</code>\n"
        f"📖  <i>{req['anime']}</i>\n"
        f"{r_emoji}  <b>{r_name}</b>\n"
        f"{'─' * 30}\n"
        f"🕒  {req['requested_at'].strftime('%Y-%m-%d  %H:%M UTC')}"
    )


def _owner_markup(req_id: str, owner_id: int) -> IKM:
    return IKM([[
        IKB("✅  Approve", callback_data=f"wg_approve:{req_id}:{owner_id}"),
        IKB("❌  Deny",    callback_data=f"wg_deny:{req_id}:{owner_id}"),
    ]])


# ─────────────────────────────────────────────────────────────────────────────
#  /start  — private DM
#  Intercepts /start to mark has_started and handle wish deep-links.
#  Uses group=1 so it runs BEFORE the main start handler (group=0 default).
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("start") & filters.private, group=1)
async def handle_start_mark(client, message: Message):
    uid = message.from_user.id

    # Always mark started on any /start in DM
    await _mark_started(uid)
    await get_or_create_user(
        uid,
        message.from_user.username   or "",
        message.from_user.first_name or "",
        getattr(message.from_user, "last_name", "") or "",
    )

    # Deep-link: /start wish_<char_id>  — auto-trigger wish flow
    args = message.command
    if len(args) > 1 and args[1].startswith("wish_"):
        char_id = args[1][5:]   # strip "wish_" prefix
        if char_id and char_id != "start":
            message.command = ["wish", char_id]
            await cmd_wish(client, message)
            # Stop propagation so the main start handler doesn't also fire
            message.stop_propagation()


# ─────────────────────────────────────────────────────────────────────────────
#  /wish <char_id>  — PRIVATE DM ONLY
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("wish"))
async def cmd_wish(client, message: Message):
    uid = message.from_user.id

    # ── Guard 1: private DM only ──────────────────────────────────────────────
    if message.chat.type != message.chat.type.PRIVATE:
        char_id_hint = message.command[1] if len(message.command) > 1 else "start"
        bot_me = await client.get_me()
        return await message.reply_text(
            "✨ <b>Wishes can only be made in the bot's DM!</b>\n\n"
            "Tap the button below to open a private chat with the bot:",
            reply_markup=IKM([[
                IKB(
                    "✨ Open Bot DM",
                    url=f"https://t.me/{bot_me.username}?start=wish_{char_id_hint}",
                ),
            ]]),
            parse_mode=enums.ParseMode.HTML,
        )

    # ── Guard 2: must have started the bot ───────────────────────────────────
    if not await _has_started(uid):
        bot_me = await client.get_me()
        return await message.reply_text(
            "✨ <b>One More Step!</b>\n\n"
            "You need to start the bot first before making a wish.\n"
            "Tap the button below and then send <b>/start</b>:",
            reply_markup=IKM([[
                IKB("🌸 Start the Bot", url=f"https://t.me/{bot_me.username}?start=start"),
            ]]),
            parse_mode=enums.ParseMode.HTML,
        )

    args = message.command
    if len(args) < 2:
        return await message.reply_text(
            "✨ <b>Wish Granter</b>\n\n"
            "Send a wish request to the owner for any character:\n"
            "<code>/wish &lt;char_id&gt;</code>\n\n"
            "The owner will review it and may grant your wish! 🌟\n\n"
            "📋 Use /wishlist to track your requests.",
            parse_mode=enums.ParseMode.HTML,
        )

    char_id = args[1].strip()
    char    = await get_character(char_id)
    if not char:
        return await message.reply_text(
            f"❌ Character <code>{char_id}</code> not found in the database.",
            parse_mode=enums.ParseMode.HTML,
        )

    # ── Guard 3: already owns character ──────────────────────────────────────
    if await _col("user_characters").find_one({"user_id": uid, "char_id": char_id}):
        return await message.reply_text(
            f"💫 You already own <b>{char['name']}</b> in your harem!\n"
            "No need to wish for it.",
            parse_mode=enums.ParseMode.HTML,
        )

    # ── Guard 4: duplicate pending request ───────────────────────────────────
    if await _already_requested(uid, char_id):
        return await message.reply_text(
            f"⏳ You already have a <b>pending wish</b> for <b>{char['name']}</b>.\n"
            "Please wait for the owner to review it.",
            parse_mode=enums.ParseMode.HTML,
        )

    # ── Guard 5: too many pending ─────────────────────────────────────────────
    pending_count = await _count_pending(uid)
    if pending_count >= MAX_WISH_PENDING:
        return await message.reply_text(
            f"⚠️ You have <b>{pending_count}/{MAX_WISH_PENDING}</b> pending wish requests.\n\n"
            "Please wait for the owner to review them before sending more.\n"
            "Use /wishlist to check your pending wishes.",
            parse_mode=enums.ParseMode.HTML,
        )

    # Add to passive wishlist for spawn pings — silently ignore if full/duplicate
    await add_wish(uid, char_id, char["name"], char.get("rarity", "common"))
    await get_or_create_user(
        uid,
        message.from_user.username   or "",
        message.from_user.first_name or "",
        getattr(message.from_user, "last_name", "") or "",
    )

    user_name = message.from_user.first_name or f"User {uid}"
    req_id    = await _create_request(uid, user_name, char)
    req       = await _get_request(req_id)

    # Dispatch to every owner DM
    sent_count = 0
    for owner_id in OWNER_IDS:
        try:
            await _send_char_media(
                client, owner_id, char,
                _owner_caption(req),
                _owner_markup(req_id, owner_id),
            )
            sent_count += 1
            log.info("wish %s dispatched to owner %d", req_id, owner_id)
        except Exception as exc:
            log.warning("could not reach owner %d: %s", owner_id, exc)

    if sent_count == 0:
        await _wreqs().delete_one({"_req_id": req_id})
        return await message.reply_text(
            "❌ Could not reach any owner right now. Please try again later.",
            parse_mode=enums.ParseMode.HTML,
        )

    tier    = get_rarity(char.get("rarity", ""))
    r_line  = _rarity_line(char.get("rarity", ""))
    vid_tag = " 🎬" if char.get("video_url") else ""

    await message.reply_text(
        f"✨ <b>Wish Sent!</b>\n"
        f"{'─' * 28}\n"
        f"🌸  <b>{char['name']}</b>{vid_tag}  <code>{char_id}</code>\n"
        f"📖  <i>{char.get('anime', '?')}</i>\n"
        f"{r_line}\n"
        f"{'─' * 28}\n"
        f"🆔  Request ID: <code>{req_id}</code>\n\n"
        f"Your wish has been sent to the owner for review.\n"
        f"You'll receive a DM here once a decision is made. 🌟\n\n"
        f"📋 Use /wishlist to track your wishes.",
        parse_mode=enums.ParseMode.HTML,
    )
    log.info("WISH CREATED  req=%s  user=%d  char=%s (%s)", req_id, uid, char_id, char["name"])


# ─────────────────────────────────────────────────────────────────────────────
#  /wishlist  — private DM
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("wishlist") & filters.private)
async def cmd_wishlist(_, message: Message):
    uid   = message.from_user.id
    items = await get_wishlist(uid)

    if not items:
        return await message.reply_text(
            "💛 Your wishlist is empty!\n\n"
            "Use <code>/wish &lt;char_id&gt;</code> to send a wish to the owner.",
            parse_mode=enums.ParseMode.HTML,
        )

    pending_docs = await _wreqs().find(
        {"user_id": uid, "status": "pending"}
    ).to_list(25)
    pending_ids: set[str] = {d["char_id"] for d in pending_docs}

    lines = [f"💛 <b>{message.from_user.first_name}'s Wishlist</b>\n"]
    for i, item in enumerate(items, 1):
        tier    = get_rarity(item.get("rarity", ""))
        r_emoji = tier.emoji if tier else "❓"
        status  = " ⏳" if item["char_id"] in pending_ids else ""
        lines.append(
            f"<code>{i:>2}.</code> {r_emoji} <b>{item['char_name']}</b>  "
            f"<code>{item['char_id']}</code>{status}"
        )

    lines += [
        "",
        f"<code>{len(items)}/25</code> wishlist slots used",
        f"<code>{len(pending_ids)}/{MAX_WISH_PENDING}</code> wish requests pending",
        "",
        "⏳ = awaiting owner review",
    ]
    await message.reply_text("\n".join(lines), parse_mode=enums.ParseMode.HTML)


@app.on_message(filters.command("wishlist") & filters.group)
async def cmd_wishlist_group(client, message: Message):
    bot_me = await client.get_me()
    await message.reply_text(
        "💛 Use /wishlist in the bot's private DM.",
        reply_markup=IKM([[
            IKB("📋 Open Bot DM", url=f"https://t.me/{bot_me.username}?start=start"),
        ]]),
        parse_mode=enums.ParseMode.HTML,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  /unwish <char_id>
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("unwish"))
async def cmd_unwish(_, message: Message):
    uid  = message.from_user.id
    args = message.command

    if len(args) < 2:
        return await message.reply_text(
            "Usage: <code>/unwish &lt;char_id&gt;</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    char_id = args[1].strip()
    removed = await remove_wish(uid, char_id)

    cancel_res = await _wreqs().update_many(
        {"user_id": uid, "char_id": char_id, "status": "pending"},
        {"$set": {"status": "cancelled", "resolved_at": datetime.utcnow()}},
    )
    cancelled = cancel_res.modified_count

    if removed or cancelled:
        parts = []
        if removed:
            parts.append(f"<code>{char_id}</code> removed from your wishlist")
        if cancelled:
            parts.append(f"{cancelled} pending request(s) cancelled")
        await message.reply_text(
            "💛 " + " and ".join(parts) + ".",
            parse_mode=enums.ParseMode.HTML,
        )
    else:
        await message.reply_text(
            f"❌ <code>{char_id}</code> is not in your wishlist.",
            parse_mode=enums.ParseMode.HTML,
        )


# ─────────────────────────────────────────────────────────────────────────────
#  Owner callback — ✅ Approve  wg_approve:<req_id>:<owner_id>
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^wg_approve:"))
async def cb_wish_approve(client, cb):
    if cb.from_user.id not in OWNER_IDS:
        return await cb.answer("⛔ Owners only!", show_alert=True)

    req_id = cb.data.split(":")[1]
    req    = await _get_request(req_id)

    if not req:
        await cb.answer("❌ Request not found.", show_alert=True)
        try:
            await cb.message.edit_reply_markup(IKM([]))
        except Exception:
            pass
        return

    if req["status"] != "pending":
        await cb.answer(f"Already {req['status'].upper()}.", show_alert=True)
        try:
            await cb.message.edit_reply_markup(IKM([]))
        except Exception:
            pass
        return

    # Atomic resolve — prevents double-grant
    if not await _resolve_request(req_id, cb.from_user.id, "approved"):
        return await cb.answer("Already processed by another owner.", show_alert=True)

    await cb.answer("✅ Wish approved! Adding to harem…")

    char_doc = {
        "id":        req["char_id"],
        "name":      req["char_name"],
        "anime":     req["anime"],
        "rarity":    req["rarity"],
        "img_url":   req.get("img_url", ""),
        "video_url": req.get("video_url", ""),
    }

    await get_or_create_user(req["user_id"], "", "", "")
    iid = await add_to_harem(req["user_id"], char_doc)

    log.info(
        "WISH GRANTED  req=%s  owner=%d  user=%d  char=%s (%s)  iid=%s",
        req_id, cb.from_user.id, req["user_id"],
        req["char_id"], req["char_name"], iid,
    )

    tier       = get_rarity(req["rarity"])
    r_emoji    = tier.emoji if tier else "❓"
    r_name     = tier.display_name if tier else req["rarity"].title()
    owner_name = cb.from_user.first_name or f"Owner {cb.from_user.id}"
    vid_tag    = " 🎬" if req.get("video_url") else ""
    now_str    = datetime.utcnow().strftime("%Y-%m-%d  %H:%M UTC")

    # Update owner card → approved state
    try:
        await cb.message.edit_caption(
            f"✅ <b>Wish Approved</b>\n"
            f"{'─' * 30}\n"
            f"👤  <b>{req['user_name']}</b>  <code>{req['user_id']}</code>\n"
            f"🌸  <b>{req['char_name']}</b>{vid_tag}  <code>{req['char_id']}</code>\n"
            f"📖  <i>{req['anime']}</i>\n"
            f"{r_emoji}  <b>{r_name}</b>\n"
            f"{'─' * 30}\n"
            f"✅  Approved by <b>{owner_name}</b>\n"
            f"🆔  Instance: <code>{iid}</code>\n"
            f"🕒  {now_str}",
            reply_markup=IKM([]),
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception:
        pass

    # DM user — "Wish Granted" card with character media
    grant_caption = (
        f"🌟 <b>Your Wish Has Been Granted!</b>\n"
        f"{'─' * 30}\n"
        f"🌸  <b>{req['char_name']}</b>{vid_tag}  <code>{req['char_id']}</code>\n"
        f"📖  <i>{req['anime']}</i>\n"
        f"{r_emoji}  <b>{r_name}</b>\n"
        f"{'─' * 30}\n"
        f"✨  <b>{req['char_name']}</b> has been added to your harem!\n"
        f"🆔  Instance: <code>{iid}</code>\n\n"
        f"Use /harem to admire your collection. 🌸"
    )
    try:
        await _send_char_media(client, req["user_id"], char_doc, grant_caption)
    except Exception as exc:
        log.warning("grant DM failed user=%d: %s", req["user_id"], exc)


# ─────────────────────────────────────────────────────────────────────────────
#  Owner callback — ❌ Deny  wg_deny:<req_id>:<owner_id>
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^wg_deny:"))
async def cb_wish_deny(client, cb):
    if cb.from_user.id not in OWNER_IDS:
        return await cb.answer("⛔ Owners only!", show_alert=True)

    req_id = cb.data.split(":")[1]
    req    = await _get_request(req_id)

    if not req:
        await cb.answer("❌ Request not found.", show_alert=True)
        try:
            await cb.message.edit_reply_markup(IKM([]))
        except Exception:
            pass
        return

    if req["status"] != "pending":
        await cb.answer(f"Already {req['status'].upper()}.", show_alert=True)
        try:
            await cb.message.edit_reply_markup(IKM([]))
        except Exception:
            pass
        return

    if not await _resolve_request(req_id, cb.from_user.id, "denied"):
        return await cb.answer("Already processed by another owner.", show_alert=True)

    await cb.answer("❌ Wish denied.")

    tier       = get_rarity(req["rarity"])
    r_emoji    = tier.emoji if tier else "❓"
    r_name     = tier.display_name if tier else req["rarity"].title()
    owner_name = cb.from_user.first_name or f"Owner {cb.from_user.id}"
    vid_tag    = " 🎬" if req.get("video_url") else ""
    now_str    = datetime.utcnow().strftime("%Y-%m-%d  %H:%M UTC")

    log.info(
        "WISH DENIED  req=%s  owner=%d  user=%d  char=%s",
        req_id, cb.from_user.id, req["user_id"], req["char_id"],
    )

    # Update owner card → denied state
    try:
        await cb.message.edit_caption(
            f"❌ <b>Wish Denied</b>\n"
            f"{'─' * 30}\n"
            f"👤  <b>{req['user_name']}</b>  <code>{req['user_id']}</code>\n"
            f"🌸  <b>{req['char_name']}</b>{vid_tag}  <code>{req['char_id']}</code>\n"
            f"📖  <i>{req['anime']}</i>\n"
            f"{r_emoji}  <b>{r_name}</b>\n"
            f"{'─' * 30}\n"
            f"❌  Denied by <b>{owner_name}</b>\n"
            f"🕒  {now_str}",
            reply_markup=IKM([]),
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception:
        pass

    # DM user — polite denial with character picture
    char_doc = {
        "id":        req["char_id"],
        "name":      req["char_name"],
        "img_url":   req.get("img_url", ""),
        "video_url": req.get("video_url", ""),
    }
    deny_caption = (
        f"💫 <b>Wish Request Update</b>\n"
        f"{'─' * 28}\n"
        f"🌸  <b>{req['char_name']}</b>{vid_tag}  <code>{req['char_id']}</code>\n"
        f"📖  <i>{req['anime']}</i>\n"
        f"{r_emoji}  <b>{r_name}</b>\n"
        f"{'─' * 28}\n"
        f"We're sorry, your wish was not approved this time. 🌙\n\n"
        f"Keep collecting and try again later! 🌟"
    )
    try:
        await _send_char_media(client, req["user_id"], char_doc, deny_caption)
    except Exception as exc:
        log.warning("denial DM failed user=%d: %s", req["user_id"], exc)


# ─────────────────────────────────────────────────────────────────────────────
#  /wishqueue  — owner only, see all pending
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("wishqueue") & filters.user(OWNER_IDS))
async def cmd_wishqueue(_, message: Message):
    pending = (
        await _wreqs()
        .find({"status": "pending"})
        .sort("requested_at", 1)
        .to_list(50)
    )

    if not pending:
        return await message.reply_text("✅ No pending wish requests right now.")

    lines = [f"⏳ <b>Pending Wish Requests  ({len(pending)})</b>\n"]
    for req in pending:
        tier    = get_rarity(req["rarity"])
        r_emoji = tier.emoji if tier else "❓"
        vid_tag = " 🎬" if req.get("video_url") else ""
        lines.append(
            f"<code>{req['_req_id']}</code>  "
            f"{r_emoji} <b>{req['char_name']}</b>{vid_tag}  "
            f"<code>{req['char_id']}</code>\n"
            f"   👤 {req['user_name']}  <code>{req['user_id']}</code>  "
            f"· {req['requested_at'].strftime('%m-%d  %H:%M')}\n"
        )

    await message.reply_text("\n".join(lines), parse_mode=enums.ParseMode.HTML)
