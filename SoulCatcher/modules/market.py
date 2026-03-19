"""
SoulCatcher/modules/market.py
══════════════════════════════════════════════════════════════════════════════
Stock-based character marketplace — fully wired to database.py v2.

UPLOADER / OWNER COMMANDS
  /add  <char_id> <price> <stock> [per_user_limit]
        List a character for sale.
        char_id        — 4-digit catalogue ID (e.g. 0042)
        price          — kakera per copy
        stock          — total copies available (1–9999)
        per_user_limit — max copies per buyer  (default 1, 0 = unlimited)

  /mremove  <listing_id>              — remove a listing
  /mrestock <listing_id> <amount>     — add stock to existing listing
  /mprice   <listing_id> <new_price>  — update price (uploader/owner)

OWNER COMMANDS
  /mstats   — market-wide aggregate stats
  /delh <user_id> — wipe a user's entire harem

USER COMMANDS
  /market [rarity]  — browse all active listings
  /mybuys           — your purchase history

INLINE FLOW
  Browse list → View card → Buy confirm → Purchase success
  ↕ filter by rarity strip  ↕ pagination  ↕ back navigation

DATABASE FUNCTIONS USED (all from database.py v2)
  create_market_listing / get_market_listing / update_market_listing
  get_active_market_listings / count_active_market_listings
  atomic_market_buy / log_market_purchase
  get_user_market_purchase_count / get_user_market_history
  market_aggregate_stats / top_market_listings
  get_character / add_to_harem / add_xp
  deduct_balance (atomic) / get_balance / get_or_create_user / add_balance
  _col (direct, only for /delh harem wipe)

RARITY FUNCTIONS USED (all from rarity.py v2)
  get_rarity / RARITIES / SUB_RARITIES
  can_list_on_market / get_xp_reward / rarity_display
  ECONOMY
══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime
from html import escape as he
from typing import Optional

from pyrogram import enums, filters
from pyrogram.errors import QueryIdInvalid
from pyrogram.types import (
    CallbackQuery,
    InputMediaPhoto,
    InputMediaVideo,
    InlineKeyboardButton as IKB,
    InlineKeyboardMarkup as IKM,
    Message,
)

from .. import app, uploader_filter, owner_filter
from ..config import LOG_CHANNEL_ID, OWNER_IDS
from ..database import (
    # character catalogue
    get_character,
    # harem
    add_to_harem,
    _col,
    # economy
    get_balance,
    deduct_balance,
    add_balance,
    get_or_create_user,
    add_xp,
    # market — stock-based (new)
    create_market_listing,
    get_market_listing,
    update_market_listing,
    get_active_market_listings,
    count_active_market_listings,
    atomic_market_buy,
    log_market_purchase,
    get_user_market_purchase_count,
    get_user_market_history,
    market_aggregate_stats,
    top_market_listings,
)
from ..rarity import (
    ECONOMY,
    RARITIES,
    SUB_RARITIES,
    can_list_on_market,
    get_rarity,
    get_xp_reward,
    rarity_display,
)

log = logging.getLogger("SoulCatcher.market")

# ── Constants ──────────────────────────────────────────────────────────────────

LISTINGS_PER_PAGE  = 5          # cards shown per /market page
BUY_COOLDOWN_SECS  = 5          # per-user anti-spam cooldown between purchases
MARKET_BUY_XP      = ECONOMY.get("market_buy_xp", 20)  # XP per market purchase

# All rarity keys ordered weight-desc for the filter strip
_ALL_RARITY_KEYS: list[str] = sorted(
    {**RARITIES, **SUB_RARITIES}.keys(),
    key=lambda k: {**RARITIES, **SUB_RARITIES}[k].weight,
    reverse=True,
)

# Per-user asyncio buy locks  {user_id: Lock}
_buy_locks: dict[int, asyncio.Lock] = {}
# Anti-spam timestamps        {user_id: epoch}
_last_buy:  dict[int, float]        = {}


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fmt(n) -> str:
    try:    return f"{int(n):,}"
    except: return str(n)


def _new_lid() -> str:
    return f"MKT-{uuid.uuid4().hex[:6].upper()}"


def _new_pid() -> str:
    return f"PUR-{uuid.uuid4().hex[:6].upper()}"


def _get_buy_lock(uid: int) -> asyncio.Lock:
    if uid not in _buy_locks:
        _buy_locks[uid] = asyncio.Lock()
    return _buy_locks[uid]


def _rarity_label(key: str) -> str:
    r = get_rarity(key)
    return f"{r.emoji} {r.display_name}" if r else f"❓ {key}"


def _stock_bar(remaining: int, total: int, width: int = 10) -> str:
    if total <= 0:
        return "░" * width + "  0/0"
    filled = round(max(0, min(remaining / total, 1.0)) * width)
    return f"{'█' * filled}{'░' * (width - filled)}  {remaining}/{total}"


def _stock_dot(remaining: int, total: int) -> str:
    if total <= 0:
        return "⬜"
    pct = remaining / total
    if pct > 0.5:  return "🟢"
    if pct > 0.15: return "🟡"
    if pct > 0:    return "🔴"
    return "⚫"


async def _safe_answer(cb, text: str, alert: bool = False) -> None:
    try:
        await cb.answer(text, show_alert=alert)
    except (QueryIdInvalid, Exception):
        pass


def _pad_id(raw: str) -> str:
    return raw.strip().zfill(4) if raw.strip().isdigit() else raw.strip()


# ─────────────────────────────────────────────────────────────────────────────
#  Card builders
# ─────────────────────────────────────────────────────────────────────────────

def _listing_card(lst: dict, per_u_bought: int = 0) -> str:
    status      = lst.get("status", "active")
    rarity_key  = lst.get("rarity", "")
    remaining   = lst.get("stock_remaining", 0)
    total_stock = lst.get("stock_total", 1)
    sold        = lst.get("stock_sold", 0)
    price       = lst.get("price", 0)
    per_u       = lst.get("per_user_limit", 1)
    per_u_str   = f"{per_u}/user" if per_u > 0 else "unlimited"
    r           = get_rarity(rarity_key)

    status_line = ""
    if status == "soldout":
        status_line = "\n\n🚫 <b>SOLD OUT</b>"
    elif status == "removed":
        status_line = "\n\n❌ <b>REMOVED BY STAFF</b>"

    restriction_parts = []
    if r:
        if not r.trade_allowed: restriction_parts.append("🚫 No Trade")
        if not r.gift_allowed:  restriction_parts.append("🚫 No Gift")
        if r.max_per_user:      restriction_parts.append(f"👤 Max {r.max_per_user}/harem")
    restrictions = "  ·  ".join(restriction_parts) if restriction_parts else "✅ Tradeable & Giftable"

    user_line = ""
    if per_u > 0:
        user_line = f"\n👤 Your purchases  <code>{per_u_bought}/{per_u}</code>"
    elif per_u_bought > 0:
        user_line = f"\n👤 You own  <code>{per_u_bought}</code> cop{'y' if per_u_bought == 1 else 'ies'}"

    return (
        f"🆔 <code>{lst.get('listing_id', '?')}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>{he(lst.get('char_name', '?'))}</b>\n"
        f"📖 <i>{he(lst.get('anime', '?'))}</i>\n"
        f"✨ {_rarity_label(rarity_key)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Price       <code>{_fmt(price)} kakera</code>\n"
        f"📦 Stock       <code>{_stock_bar(remaining, total_stock)}</code>\n"
        f"🛒 Sold        <code>{_fmt(sold)}</code>  ·  Limit <code>{per_u_str}</code>\n"
        f"⚠️ {restrictions}"
        f"{user_line}"
        f"{status_line}"
    )


def _build_browse_text(listings: list[dict], page: int, total: int,
                        rarity: Optional[str]) -> str:
    total_pages = max(1, -(-total // LISTINGS_PER_PAGE))
    filter_line = f"  ·  filter: {_rarity_label(rarity)}" if rarity else ""

    header = (
        f"╔══════════════════════════════╗\n"
        f"║   🛍️   S O U L   M A R K E T   ║\n"
        f"╚══════════════════════════════╝\n"
        f"📋 <b>{_fmt(total)}</b> listing(s){filter_line}\n"
        f"📄 Page <b>{page}/{total_pages}</b>"
    )

    if not listings:
        return header + "\n\n🌌 <i>Nothing on sale right now. Check back later!</i>"

    rows = []
    for lst in listings:
        remaining   = lst.get("stock_remaining", 0)
        total_stock = lst.get("stock_total", 1)
        dot         = _stock_dot(remaining, total_stock)
        lid_short   = lst.get("listing_id", "?")[-6:]
        rows.append(
            f"\n{dot} <b>{he(lst.get('char_name', '?'))}</b>  "
            f"<code>…{lid_short}</code>\n"
            f"   {_rarity_label(lst.get('rarity', ''))}  ·  "
            f"<code>{_fmt(lst.get('price', 0))} 🌸</code>  ·  "
            f"<code>{remaining}/{total_stock}</code> left"
        )
    return header + "".join(rows)


# ─────────────────────────────────────────────────────────────────────────────
#  Keyboard builders
# ─────────────────────────────────────────────────────────────────────────────

def _browse_kb(listings: list[dict], page: int, total: int,
               rarity: Optional[str]) -> IKM:
    total_pages = max(1, -(-total // LISTINGS_PER_PAGE))
    rows: list[list[IKB]] = []
    rar_str = rarity or ""

    # Per-listing view buttons (2 per row)
    view_row: list[IKB] = []
    for lst in listings:
        status = lst.get("status", "active")
        icon   = "🚫" if status != "active" else "👁"
        label  = f"{icon} {he(lst.get('char_name', '?')[:15])}"
        view_row.append(
            IKB(label, callback_data=f"mkt_view:{lst['listing_id']}:{page}:{rar_str}")
        )
        if len(view_row) == 2:
            rows.append(view_row)
            view_row = []
    if view_row:
        rows.append(view_row)

    # Pagination
    nav: list[IKB] = []
    if page > 1:
        nav.append(IKB("⬅️", callback_data=f"mkt_page:{page - 1}:{rar_str}"))
    nav.append(IKB(f"📖 {page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(IKB("➡️", callback_data=f"mkt_page:{page + 1}:{rar_str}"))
    rows.append(nav)

    # Rarity filter strip (5 per row)
    strip_row: list[IKB] = []
    for rkey in _ALL_RARITY_KEYS:
        r = get_rarity(rkey)
        if not r:
            continue
        mark = "✓" if rarity == rkey else ""
        strip_row.append(
            IKB(f"{mark}{r.emoji}", callback_data=f"mkt_filter:{rkey}")
        )
        if len(strip_row) == 5:
            rows.append(strip_row)
            strip_row = []
    if strip_row:
        rows.append(strip_row)

    # Bottom row
    bottom: list[IKB] = []
    if rarity:
        bottom.append(IKB("❌ Clear Filter", callback_data="mkt_filter:"))
    bottom.append(IKB("🔄 Refresh", callback_data=f"mkt_page:{page}:{rar_str}"))
    rows.append(bottom)

    return IKM(rows)


def _detail_kb(listing_id: str, can_buy: bool,
               back_page: int, back_rar: str) -> IKM:
    rows: list[list[IKB]] = []
    if can_buy:
        rows.append([IKB("🛒  Buy Now",
                         callback_data=f"mkt_buy_init:{listing_id}:{back_page}:{back_rar}")])
    else:
        rows.append([IKB("🚫  Not Available", callback_data="noop")])
    rows.append([IKB("◀️  Back to Market",
                     callback_data=f"mkt_page:{back_page}:{back_rar}")])
    return IKM(rows)


def _confirm_kb(listing_id: str, price: int,
                back_page: int, back_rar: str) -> IKM:
    return IKM([[
        IKB(f"✅  Confirm  ({_fmt(price)} 🌸)",
            callback_data=f"mkt_buy_confirm:{listing_id}:{back_page}:{back_rar}"),
        IKB("❌  Cancel",
            callback_data=f"mkt_view:{listing_id}:{back_page}:{back_rar}"),
    ]])


def _post_buy_kb(back_page: int) -> IKM:
    return IKM([[
        IKB("🛍️  Browse More", callback_data=f"mkt_page:{back_page}:"),
        IKB("📦  My Purchases", callback_data="mkt_mybuys"),
    ]])


# ─────────────────────────────────────────────────────────────────────────────
#  Edit helper
# ─────────────────────────────────────────────────────────────────────────────

async def _try_edit(cb: CallbackQuery, text: str, markup: IKM,
                    img: str = "", vid: str = "") -> None:
    try:
        if vid:
            await cb.message.edit_media(
                InputMediaVideo(vid, caption=text, parse_mode=enums.ParseMode.HTML),
                reply_markup=markup,
            )
            return
        if img:
            await cb.message.edit_media(
                InputMediaPhoto(img, caption=text, parse_mode=enums.ParseMode.HTML),
                reply_markup=markup,
            )
            return
    except Exception:
        pass
    try:
        await cb.message.edit_caption(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
    except Exception:
        try:
            await cb.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
        except Exception:
            pass


async def _send_market_page(
    *,
    source: Optional[Message] = None,
    cb: Optional[CallbackQuery] = None,
    page: int,
    rarity: Optional[str],
    is_initial: bool = False,
) -> None:
    skip     = (page - 1) * LISTINGS_PER_PAGE
    total    = await count_active_market_listings(rarity)
    listings = await get_active_market_listings(rarity=rarity, skip=skip, limit=LISTINGS_PER_PAGE)
    text     = _build_browse_text(listings, page, total, rarity)
    markup   = _browse_kb(listings, page, total, rarity)

    if is_initial and source:
        cover_img = next((l["img_url"]   for l in listings if l.get("img_url")),   "")
        cover_vid = "" if cover_img else next(
            (l["video_url"] for l in listings if l.get("video_url")), ""
        )
        sent = False
        try:
            if cover_vid:
                await source.reply_video(cover_vid, caption=text,
                                         reply_markup=markup, parse_mode=enums.ParseMode.HTML)
                sent = True
            elif cover_img:
                await source.reply_photo(cover_img, caption=text,
                                         reply_markup=markup, parse_mode=enums.ParseMode.HTML)
                sent = True
        except Exception:
            pass
        if not sent:
            await source.reply_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)

    elif cb:
        await _try_edit(cb, text, markup)


# ═════════════════════════════════════════════════════════════════════════════
#  COMMAND — /add
# ═════════════════════════════════════════════════════════════════════════════

_ADD_USAGE = (
    "📌 <b>Add to Market</b>\n\n"
    "<code>/add &lt;char_id&gt; &lt;price&gt; &lt;stock&gt; [limit]</code>\n\n"
    "<b>Arguments</b>\n"
    "  <code>char_id</code>  — 4-digit catalogue ID  (e.g. <code>0042</code>)\n"
    "  <code>price</code>    — kakera per copy\n"
    "  <code>stock</code>    — total copies (1–9999)\n"
    "  <code>limit</code>    — max per buyer (default 1, 0 = unlimited)\n\n"
    "<b>Examples</b>\n"
    "  <code>/add 0042 5000 10</code>    — 10 copies, 1 per user\n"
    "  <code>/add 0042 5000 10 3</code>  — 10 copies, max 3 per user\n"
    "  <code>/add 0042 5000 10 0</code>  — 10 copies, unlimited per user\n\n"
    "💡 <i>Use /check &lt;id&gt; to find a character's catalogue ID.</i>"
)


@app.on_message(filters.command("add") & (uploader_filter | owner_filter))
async def cmd_add(client, message: Message) -> None:
    args = message.command
    if len(args) < 4:
        return await message.reply_text(_ADD_USAGE, parse_mode=enums.ParseMode.HTML)

    char_id = _pad_id(args[1])

    try:
        price = int(args[2])
        if price < 1:
            raise ValueError
    except ValueError:
        return await message.reply_text(
            "❌ <b>price</b> must be a positive integer.", parse_mode=enums.ParseMode.HTML
        )

    try:
        stock = int(args[3])
        if not 1 <= stock <= 9999:
            raise ValueError
    except ValueError:
        return await message.reply_text(
            "❌ <b>stock</b> must be between 1 and 9999.", parse_mode=enums.ParseMode.HTML
        )

    per_user_limit = 1
    if len(args) >= 5:
        try:
            per_user_limit = int(args[4])
            if per_user_limit < 0:
                raise ValueError
        except ValueError:
            return await message.reply_text(
                "❌ <b>per_user_limit</b> must be 0 (unlimited) or a positive integer.",
                parse_mode=enums.ParseMode.HTML,
            )

    loading = await message.reply_text(
        "🔍 <i>Looking up character…</i>", parse_mode=enums.ParseMode.HTML
    )
    char = await get_character(char_id)
    if not char:
        await loading.delete()
        return await message.reply_text(
            f"❌ Character <code>{he(char_id)}</code> not found.\n"
            "Use /check to browse the catalogue.",
            parse_mode=enums.ParseMode.HTML,
        )

    rarity_key = char.get("rarity", "")
    if not can_list_on_market(rarity_key):
        tier  = get_rarity(rarity_key)
        label = f"{tier.emoji} {tier.display_name}" if tier else rarity_key
        await loading.delete()
        return await message.reply_text(
            f"❌ <b>{label}</b> characters cannot be listed on the market.\n"
            "<i>Only Common → Festival rarities are listable.</i>",
            parse_mode=enums.ParseMode.HTML,
        )

    existing = await _col("market_listings").find_one(
        {"char_id": char_id, "status": "active"}
    )
    if existing:
        await loading.delete()
        return await message.reply_text(
            f"⚠️ <b>{he(char['name'])}</b> already has an active listing!\n"
            f"ID: <code>{existing['listing_id']}</code>\n\n"
            "Use <code>/mremove &lt;id&gt;</code> to remove it first,\n"
            "or <code>/mrestock &lt;id&gt; &lt;amount&gt;</code> to add more stock.",
            parse_mode=enums.ParseMode.HTML,
        )

    tier  = get_rarity(rarity_key)
    lid   = _new_lid()
    await create_market_listing({
        "listing_id":      lid,
        "char_id":         char_id,
        "char_name":       char.get("name", "Unknown"),
        "anime":           char.get("anime", "Unknown"),
        "rarity":          rarity_key,
        "img_url":         char.get("img_url", ""),
        "video_url":       char.get("video_url", ""),
        "price":           price,
        "stock_total":     stock,
        "stock_sold":      0,
        "stock_remaining": stock,
        "per_user_limit":  per_user_limit,
        "added_by":        message.from_user.id,
        "added_at":        datetime.utcnow(),
        "status":          "active",
    })
    await loading.delete()

    rarity_label = f"{tier.emoji} {tier.display_name}" if tier else rarity_key
    per_u_str    = f"{per_user_limit}/user" if per_user_limit > 0 else "unlimited"
    caption = (
        f"✅ <b>Listing Created</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 <code>{lid}</code>\n"
        f"👤 <b>{he(char['name'])}</b>\n"
        f"📖 <i>{he(char.get('anime', '?'))}</i>\n"
        f"✨ {rarity_label}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Price   <code>{_fmt(price)} kakera</code>\n"
        f"📦 Stock   <code>{stock} copies</code>\n"
        f"🛒 Limit   <code>{per_u_str}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📣 <i>Visible in /market immediately.</i>"
    )
    vid = char.get("video_url", "")
    img = char.get("img_url", "")
    try:
        if vid:   await message.reply_video(vid, caption=caption, parse_mode=enums.ParseMode.HTML)
        elif img: await message.reply_photo(img, caption=caption, parse_mode=enums.ParseMode.HTML)
        else:     await message.reply_text(caption, parse_mode=enums.ParseMode.HTML)
    except Exception:
        await message.reply_text(caption, parse_mode=enums.ParseMode.HTML)

    log.info("MARKET ADD  lid=%s  char=%s  price=%d  stock=%d  by=%d",
             lid, char_id, price, stock, message.from_user.id)

    if LOG_CHANNEL_ID:
        try:
            await client.send_message(
                LOG_CHANNEL_ID,
                f"🛍️ <b>New Market Listing</b>\n"
                f"<code>{lid}</code>  ·  {he(char['name'])}  ·  {rarity_label}\n"
                f"💰 {_fmt(price)}  ·  📦 {stock} copies  ·  🛒 {per_u_str}\n"
                f"By: <code>{message.from_user.id}</code>",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass


# ═════════════════════════════════════════════════════════════════════════════
#  COMMAND — /mremove
# ═════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("mremove") & (uploader_filter | owner_filter))
async def cmd_mremove(_, message: Message) -> None:
    args = message.command
    if len(args) < 2:
        return await message.reply_text(
            "Usage: <code>/mremove &lt;listing_id&gt;</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    lid = args[1].upper()
    lst = await get_market_listing(lid)
    if not lst:
        return await message.reply_text(
            f"❌ Listing <code>{he(lid)}</code> not found.",
            parse_mode=enums.ParseMode.HTML,
        )
    uid = message.from_user.id
    if uid not in OWNER_IDS and lst.get("added_by") != uid:
        return await message.reply_text(
            "❌ You can only remove your own listings.",
            parse_mode=enums.ParseMode.HTML,
        )
    await update_market_listing(lid, {"$set": {"status": "removed"}})
    log.info("MARKET REMOVE  lid=%s  by=%d", lid, uid)
    await message.reply_text(
        f"✅ Listing <code>{lid}</code> — <b>{he(lst['char_name'])}</b> removed.\n"
        f"<i>{_fmt(lst.get('stock_remaining', 0))} unsold copies were not distributed.</i>",
        parse_mode=enums.ParseMode.HTML,
    )


# ═════════════════════════════════════════════════════════════════════════════
#  COMMAND — /mrestock
# ═════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("mrestock") & (uploader_filter | owner_filter))
async def cmd_mrestock(_, message: Message) -> None:
    args = message.command
    if len(args) < 3:
        return await message.reply_text(
            "Usage: <code>/mrestock &lt;listing_id&gt; &lt;amount&gt;</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    lid = args[1].upper()
    try:
        amount = int(args[2])
        if amount < 1:
            raise ValueError
    except ValueError:
        return await message.reply_text(
            "❌ Amount must be a positive integer.", parse_mode=enums.ParseMode.HTML
        )
    lst = await get_market_listing(lid)
    if not lst:
        return await message.reply_text(
            f"❌ Listing <code>{he(lid)}</code> not found.",
            parse_mode=enums.ParseMode.HTML,
        )
    uid = message.from_user.id
    if uid not in OWNER_IDS and lst.get("added_by") != uid:
        return await message.reply_text(
            "❌ You can only restock your own listings.", parse_mode=enums.ParseMode.HTML
        )
    if lst.get("status") == "removed":
        return await message.reply_text(
            "❌ Cannot restock a removed listing.", parse_mode=enums.ParseMode.HTML
        )
    await update_market_listing(lid, {
        "$inc": {"stock_total": amount, "stock_remaining": amount},
        "$set": {"status": "active"},
    })
    updated      = await get_market_listing(lid)
    new_remaining = updated.get("stock_remaining", "?")
    log.info("MARKET RESTOCK  lid=%s  +%d  by=%d", lid, amount, uid)
    await message.reply_text(
        f"✅ <b>Restocked!</b>\n"
        f"<code>{lid}</code>  —  <b>{he(lst['char_name'])}</b>\n"
        f"Added <code>+{amount}</code>  →  <code>{_fmt(new_remaining)}</code> copies available.",
        parse_mode=enums.ParseMode.HTML,
    )


# ═════════════════════════════════════════════════════════════════════════════
#  COMMAND — /mprice
# ═════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("mprice") & (uploader_filter | owner_filter))
async def cmd_mprice(_, message: Message) -> None:
    args = message.command
    if len(args) < 3:
        return await message.reply_text(
            "Usage: <code>/mprice &lt;listing_id&gt; &lt;new_price&gt;</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    lid = args[1].upper()
    try:
        new_price = int(args[2])
        if new_price < 1:
            raise ValueError
    except ValueError:
        return await message.reply_text(
            "❌ Price must be a positive integer.", parse_mode=enums.ParseMode.HTML
        )
    lst = await get_market_listing(lid)
    if not lst:
        return await message.reply_text(
            f"❌ Listing <code>{he(lid)}</code> not found.", parse_mode=enums.ParseMode.HTML
        )
    uid = message.from_user.id
    if uid not in OWNER_IDS and lst.get("added_by") != uid:
        return await message.reply_text(
            "❌ You can only update your own listings.", parse_mode=enums.ParseMode.HTML
        )
    old_price = lst.get("price", 0)
    await update_market_listing(lid, {"$set": {"price": new_price}})
    log.info("MARKET PRICE  lid=%s  %d→%d  by=%d", lid, old_price, new_price, uid)
    await message.reply_text(
        f"✅ <b>Price Updated</b>\n"
        f"<code>{lid}</code>  —  <b>{he(lst['char_name'])}</b>\n"
        f"<code>{_fmt(old_price)} 🌸</code>  →  <code>{_fmt(new_price)} 🌸</code>",
        parse_mode=enums.ParseMode.HTML,
    )


# ═════════════════════════════════════════════════════════════════════════════
#  COMMAND — /market
# ═════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("market"))
async def cmd_market(_, message: Message) -> None:
    args   = message.command
    rarity = args[1].lower() if len(args) > 1 else None
    if rarity and rarity not in {**RARITIES, **SUB_RARITIES}:
        valid = ", ".join(f"<code>{k}</code>" for k in _ALL_RARITY_KEYS)
        return await message.reply_text(
            f"❌ Unknown rarity <code>{he(rarity)}</code>.\nValid: {valid}",
            parse_mode=enums.ParseMode.HTML,
        )
    await _send_market_page(source=message, page=1, rarity=rarity, is_initial=True)


# ═════════════════════════════════════════════════════════════════════════════
#  COMMAND — /mybuys
# ═════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("mybuys"))
async def cmd_mybuys(_, message: Message) -> None:
    uid     = message.from_user.id
    history = await get_user_market_history(uid, limit=20)
    if not history:
        return await message.reply_text(
            "🛍️ You haven't purchased anything from the market yet.\n"
            "Use /market to browse listings!",
            parse_mode=enums.ParseMode.HTML,
        )
    lines = [f"🛍️ <b>Your Market Purchases</b>",
             f"<i>Showing last {len(history)} purchase(s)</i>\n"]
    for p in history:
        dt     = p.get("purchased_at")
        dt_str = dt.strftime("%Y-%m-%d %H:%M") if isinstance(dt, datetime) else "?"
        lines.append(
            f"• <b>{he(p.get('char_name', '?'))}</b>  "
            f"<code>{p.get('listing_id', '?')}</code>\n"
            f"  💰 <code>{_fmt(p.get('price', 0))}</code> 🌸  ·  "
            f"🆔 <code>{p.get('instance_id', '?')}</code>  ·  "
            f"🕐 <code>{dt_str}</code>"
        )
    await message.reply_text("\n".join(lines), parse_mode=enums.ParseMode.HTML)


# ═════════════════════════════════════════════════════════════════════════════
#  COMMAND — /mstats  (owner only)
# ═════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("mstats") & owner_filter)
async def cmd_mstats(_, message: Message) -> None:
    wait  = await message.reply_text(
        "⏳ <i>Crunching market stats…</i>", parse_mode=enums.ParseMode.HTML
    )
    stats = await market_aggregate_stats()
    top   = await top_market_listings(limit=5)

    top_lines = [
        f"  {i + 1}. <b>{he(lst['char_name'])}</b>  "
        f"<code>{_fmt(lst['stock_sold'])} sold</code>  "
        f"@ <code>{_fmt(lst['price'])} 🌸</code>"
        for i, lst in enumerate(top)
    ] or ["  <i>No sales yet.</i>"]

    text = (
        f"📊 <b>MARKET STATS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Total Listings      <code>{_fmt(stats['total_listings'])}</code>\n"
        f"🟢 Active              <code>{_fmt(stats['active'])}</code>\n"
        f"🔴 Sold Out            <code>{_fmt(stats['soldout'])}</code>\n"
        f"❌ Removed             <code>{_fmt(stats['removed'])}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🛒 Total Purchases     <code>{_fmt(stats['total_purchases'])}</code>\n"
        f"🌸 Total Kakera Spent  <code>{_fmt(stats['kakera_spent'])}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 <b>Top Sellers</b>\n"
        + "\n".join(top_lines)
    )
    try:
        await wait.edit_text(text, parse_mode=enums.ParseMode.HTML)
    except Exception:
        await message.reply_text(text, parse_mode=enums.ParseMode.HTML)


# ═════════════════════════════════════════════════════════════════════════════
#  COMMAND — /delh  (owner only)
# ═════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("delh") & owner_filter)
async def cmd_delh(_, message: Message) -> None:
    args = message.command
    if len(args) < 2:
        return await message.reply_text(
            "Usage: <code>/delh &lt;user_id&gt;</code>", parse_mode=enums.ParseMode.HTML
        )
    try:
        target_uid = int(args[1])
    except ValueError:
        return await message.reply_text("❌ Invalid user ID.", parse_mode=enums.ParseMode.HTML)

    count = await _col("user_characters").count_documents({"user_id": target_uid})
    if count == 0:
        return await message.reply_text(
            f"❌ User <code>{target_uid}</code> has no characters.",
            parse_mode=enums.ParseMode.HTML,
        )
    await _col("user_characters").delete_many({"user_id": target_uid})
    user_doc = await _col("users").find_one({"user_id": target_uid})
    name     = user_doc.get("first_name", str(target_uid)) if user_doc else str(target_uid)
    await message.reply_text(
        f"✅ Harem of <b>{he(name)}</b> (<code>{target_uid}</code>) deleted.\n"
        f"Removed <b>{count}</b> characters.",
        parse_mode=enums.ParseMode.HTML,
    )
    log.warning("Owner %d wiped harem of uid=%d — %d chars",
                message.from_user.id, target_uid, count)


# ═════════════════════════════════════════════════════════════════════════════
#  CALLBACK — pagination   mkt_page:<page>:<rarity>
# ═════════════════════════════════════════════════════════════════════════════

@app.on_callback_query(filters.regex(r"^mkt_page:(\d+):(.*)$"))
async def cb_mkt_page(_, cb: CallbackQuery) -> None:
    page   = int(cb.matches[0].group(1))
    rarity = cb.matches[0].group(2) or None
    await cb.answer()
    await _send_market_page(cb=cb, page=page, rarity=rarity)


# ═════════════════════════════════════════════════════════════════════════════
#  CALLBACK — rarity filter   mkt_filter:<key>   (empty = clear)
# ═════════════════════════════════════════════════════════════════════════════

@app.on_callback_query(filters.regex(r"^mkt_filter:(.*)$"))
async def cb_mkt_filter(_, cb: CallbackQuery) -> None:
    rarity = cb.matches[0].group(1) or None
    await cb.answer()
    await _send_market_page(cb=cb, page=1, rarity=rarity)


# ═════════════════════════════════════════════════════════════════════════════
#  CALLBACK — detail view   mkt_view:<lid>:<back_page>:<back_rar>
# ═════════════════════════════════════════════════════════════════════════════

@app.on_callback_query(filters.regex(r"^mkt_view:([^:]+):(\d+):(.*)$"))
async def cb_mkt_view(_, cb: CallbackQuery) -> None:
    lid       = cb.matches[0].group(1)
    back_page = int(cb.matches[0].group(2))
    back_rar  = cb.matches[0].group(3) or ""

    lst = await get_market_listing(lid)
    if not lst:
        return await _safe_answer(cb, "❌ Listing no longer exists.", alert=True)

    uid          = cb.from_user.id
    per_u_bought = await get_user_market_purchase_count(lid, uid)
    per_u        = lst.get("per_user_limit", 1)
    can_buy      = (
        lst.get("status") == "active"
        and lst.get("stock_remaining", 0) > 0
        and (per_u == 0 or per_u_bought < per_u)
    )

    card   = _listing_card(lst, per_u_bought=per_u_bought)
    markup = _detail_kb(lid, can_buy, back_page, back_rar)

    if not can_buy and lst.get("status") == "active" and lst.get("stock_remaining", 0) > 0:
        card += "\n⚠️ <i>You've reached your purchase limit for this listing.</i>"

    await cb.answer()
    await _try_edit(cb, card, markup,
                    img=lst.get("img_url", ""), vid=lst.get("video_url", ""))


# ═════════════════════════════════════════════════════════════════════════════
#  CALLBACK — buy init   mkt_buy_init:<lid>:<back_page>:<back_rar>
# ═════════════════════════════════════════════════════════════════════════════

@app.on_callback_query(filters.regex(r"^mkt_buy_init:([^:]+):(\d+):(.*)$"))
async def cb_mkt_buy_init(_, cb: CallbackQuery) -> None:
    lid       = cb.matches[0].group(1)
    back_page = int(cb.matches[0].group(2))
    back_rar  = cb.matches[0].group(3) or ""

    lst = await get_market_listing(lid)
    if not lst:
        return await _safe_answer(cb, "❌ Listing not found.", alert=True)
    if lst.get("status") != "active":
        return await _safe_answer(cb, "🚫 This listing is no longer available.", alert=True)
    if lst.get("stock_remaining", 0) <= 0:
        return await _safe_answer(cb, "🚫 Sold out!", alert=True)

    uid          = cb.from_user.id
    per_u        = lst.get("per_user_limit", 1)
    per_u_bought = await get_user_market_purchase_count(lid, uid)
    if per_u > 0 and per_u_bought >= per_u:
        return await _safe_answer(
            cb,
            f"⚠️ You've already bought the max "
            f"({per_u} cop{'y' if per_u == 1 else 'ies'}) of this character.",
            alert=True,
        )

    price = lst["price"]
    bal   = await get_balance(uid)
    if bal < price:
        return await _safe_answer(
            cb,
            f"❌ Insufficient kakera!\n"
            f"You have {_fmt(bal)} 🌸  ·  Need {_fmt(price)} 🌸",
            alert=True,
        )

    card = (
        f"🛒 <b>Confirm Purchase</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>{he(lst['char_name'])}</b>\n"
        f"📖 <i>{he(lst['anime'])}</i>\n"
        f"✨ {_rarity_label(lst.get('rarity', ''))}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Price      <code>{_fmt(price)} kakera</code>\n"
        f"💳 Balance    <code>{_fmt(bal)} kakera</code>\n"
        f"📉 After Buy  <code>{_fmt(bal - price)} kakera</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Confirming will add this character to your harem.</i>"
    )
    markup = _confirm_kb(lid, price, back_page, back_rar)
    await cb.answer()
    await _try_edit(cb, card, markup,
                    img=lst.get("img_url", ""), vid=lst.get("video_url", ""))


# ═════════════════════════════════════════════════════════════════════════════
#  CALLBACK — buy confirm   mkt_buy_confirm:<lid>:<back_page>:<back_rar>
# ═════════════════════════════════════════════════════════════════════════════

@app.on_callback_query(filters.regex(r"^mkt_buy_confirm:([^:]+):(\d+):(.*)$"))
async def cb_mkt_buy_confirm(client, cb: CallbackQuery) -> None:
    lid       = cb.matches[0].group(1)
    back_page = int(cb.matches[0].group(2))
    back_rar  = cb.matches[0].group(3) or ""
    uid       = cb.from_user.id

    # Anti-spam
    now  = time.time()
    last = _last_buy.get(uid, 0)
    if now - last < BUY_COOLDOWN_SECS:
        return await _safe_answer(
            cb, f"⏳ Wait {int(BUY_COOLDOWN_SECS - (now - last))}s before buying again.",
            alert=True,
        )

    buy_lock = _get_buy_lock(uid)
    if buy_lock.locked():
        return await _safe_answer(cb, "⏳ Purchase already in progress…", alert=True)

    async with buy_lock:
        # Re-fetch inside lock
        lst = await get_market_listing(lid)
        if not lst:
            return await _safe_answer(cb, "❌ Listing not found.", alert=True)
        if lst.get("status") != "active" or lst.get("stock_remaining", 0) <= 0:
            return await _safe_answer(cb, "🚫 Sold out or listing removed!", alert=True)

        per_u        = lst.get("per_user_limit", 1)
        per_u_bought = await get_user_market_purchase_count(lid, uid)
        if per_u > 0 and per_u_bought >= per_u:
            return await _safe_answer(
                cb, "⚠️ You've already reached your purchase limit.", alert=True
            )

        price = lst["price"]
        bal   = await get_balance(uid)
        if bal < price:
            return await _safe_answer(
                cb,
                f"❌ Insufficient kakera!\n"
                f"Have: {_fmt(bal)} 🌸  ·  Need: {_fmt(price)} 🌸",
                alert=True,
            )

        # 1. Atomically decrement stock
        updated_lst = await atomic_market_buy(lid)
        if updated_lst is None:
            return await _safe_answer(
                cb, "🚫 Sold out — someone just grabbed the last copy.", alert=True
            )

        # 2. Atomically deduct balance
        ok = await deduct_balance(uid, price)
        if not ok:
            # Roll back stock
            await update_market_listing(lid, {
                "$inc": {"stock_sold": -1, "stock_remaining": 1},
                "$set": {"status": "active"},
            })
            return await _safe_answer(
                cb, "❌ Balance deduction failed. Purchase cancelled.", alert=True
            )

        # 3. Ensure user document exists
        await get_or_create_user(
            uid,
            cb.from_user.username   or "",
            cb.from_user.first_name or "",
            getattr(cb.from_user, "last_name", "") or "",
        )

        # 4. Add to harem
        char_doc = {
            "id":        updated_lst["char_id"],
            "name":      updated_lst["char_name"],
            "anime":     updated_lst["anime"],
            "rarity":    updated_lst["rarity"],
            "img_url":   updated_lst.get("img_url", ""),
            "video_url": updated_lst.get("video_url", ""),
        }
        instance_id = await add_to_harem(uid, char_doc)

        # 5. Award XP (rarity XP + market bonus, both mode-scaled)
        rarity_key          = updated_lst.get("rarity", "common")
        xp_gain             = get_xp_reward(rarity_key) + MARKET_BUY_XP
        _, new_level, levelled_up = await add_xp(uid, xp_gain)

        # 6. Log purchase
        await log_market_purchase({
            "purchase_id":  _new_pid(),
            "listing_id":   lid,
            "buyer_id":     uid,
            "char_id":      updated_lst["char_id"],
            "char_name":    updated_lst["char_name"],
            "instance_id":  instance_id,
            "price":        price,
            "purchased_at": datetime.utcnow(),
        })
        _last_buy[uid] = time.time()

    # Build result card
    remaining    = updated_lst.get("stock_remaining", 0)
    rarity_label = _rarity_label(rarity_key)
    new_bal      = bal - price

    soldout_line = (
        "\n🔴 <b>This listing is now SOLD OUT!</b>"
        if updated_lst.get("status") == "soldout" else ""
    )
    level_line = (
        f"\n🎉 <b>Level Up!</b>  You are now <b>Level {new_level}</b>!"
        if levelled_up else ""
    )

    result_card = (
        f"✅ <b>Purchase Successful!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>{he(updated_lst['char_name'])}</b>\n"
        f"📖 <i>{he(updated_lst['anime'])}</i>\n"
        f"✨ {rarity_label}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Paid          <code>{_fmt(price)} kakera</code>\n"
        f"💳 New Balance   <code>{_fmt(new_bal)} kakera</code>\n"
        f"⚡ XP Gained     <code>+{xp_gain}</code>\n"
        f"🆔 Instance      <code>{instance_id}</code>\n"
        f"📦 Stock Left    <code>{remaining}</code>"
        f"{soldout_line}"
        f"{level_line}"
    )

    log.info("MARKET BUY  lid=%s  buyer=%d  char=%s  price=%d  iid=%s  xp+%d",
             lid, uid, updated_lst["char_id"], price, instance_id, xp_gain)

    await cb.answer("✅ Purchased!", show_alert=False)
    await _try_edit(
        cb, result_card, _post_buy_kb(back_page),
        img=updated_lst.get("img_url", ""),
        vid=updated_lst.get("video_url", ""),
    )

    # DM the buyer
    dm_text = (
        f"🛍️ <b>Market Purchase</b>\n\n"
        f"<blockquote>"
        f"👤 <b>{he(updated_lst['char_name'])}</b>\n"
        f"📖 {he(updated_lst['anime'])}\n"
        f"✨ {rarity_label}\n"
        f"🆔 Instance: <code>{instance_id}</code>\n"
        f"💰 Paid: <code>{_fmt(price)} kakera</code>"
        f"</blockquote>\n\n"
        f"Use /harem to view your collection! 🌸"
    )
    img = updated_lst.get("img_url", "")
    vid = updated_lst.get("video_url", "")
    try:
        if vid:   await client.send_video(uid, vid, caption=dm_text, parse_mode=enums.ParseMode.HTML)
        elif img: await client.send_photo(uid, img, caption=dm_text, parse_mode=enums.ParseMode.HTML)
        else:     await client.send_message(uid, dm_text, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        log.warning("Market DM failed uid=%d: %s", uid, e)

    if LOG_CHANNEL_ID:
        try:
            await client.send_message(
                LOG_CHANNEL_ID,
                f"🛒 <b>Market Purchase</b>\n"
                f"<code>{lid}</code>  ·  {he(updated_lst['char_name'])}  ·  {rarity_label}\n"
                f"Buyer: <code>{uid}</code>  ·  "
                f"Paid: <code>{_fmt(price)} 🌸</code>  ·  "
                f"Stock left: <code>{remaining}</code>",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass


# ═════════════════════════════════════════════════════════════════════════════
#  CALLBACK — inline /mybuys   mkt_mybuys
# ═════════════════════════════════════════════════════════════════════════════

@app.on_callback_query(filters.regex(r"^mkt_mybuys$"))
async def cb_mkt_mybuys(_, cb: CallbackQuery) -> None:
    uid     = cb.from_user.id
    history = await get_user_market_history(uid, limit=10)
    await cb.answer()

    if not history:
        return await _safe_answer(cb, "No market purchases yet.", alert=True)

    lines = ["🛍️ <b>Your Recent Purchases</b>\n"]
    for p in history:
        dt     = p.get("purchased_at")
        dt_str = dt.strftime("%m-%d %H:%M") if isinstance(dt, datetime) else "?"
        lines.append(
            f"• <b>{he(p.get('char_name', '?'))}</b>  "
            f"<code>{_fmt(p.get('price', 0))} 🌸</code>  "
            f"<code>{dt_str}</code>"
        )
    try:
        await cb.message.edit_text(
            "\n".join(lines),
            reply_markup=IKM([[IKB("◀️ Back to Market", callback_data="mkt_page:1:")]]),
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception:
        pass


# ═════════════════════════════════════════════════════════════════════════════
#  CALLBACK — noop (label-only buttons)
# ═════════════════════════════════════════════════════════════════════════════

@app.on_callback_query(filters.regex(r"^noop$"))
async def cb_noop(_, cb: CallbackQuery) -> None:
    await cb.answer()
