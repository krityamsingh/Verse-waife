"""SoulCatcher/modules/market.py — Stock-based character market."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime

import SoulCatcher as _soul
from pyrogram import filters
from pyrogram.types import Message, InlineKeyboardMarkup as IKM, InlineKeyboardButton as IKB, CallbackQuery

from SoulCatcher.database import (
    get_or_create_user,
    get_character,
    get_harem_char,
    remove_from_harem,
    add_balance,
    deduct_balance,
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
    add_xp,
)
from SoulCatcher.rarity import (
    rarity_display,
    can_list_on_market,
    get_sell_price,
    ECONOMY,
)

log = logging.getLogger("SoulCatcher.market")

LISTINGS_PER_PAGE = 6


def _listing_text(l: dict, index: int = 0) -> str:
    r_str   = rarity_display(l.get("rarity", "common"))
    stock   = l.get("stock_remaining", 0)
    sold    = l.get("stock_sold", 0)
    price   = l.get("price", 0)
    char    = l.get("char_name", "Unknown")
    anime   = l.get("anime", "Unknown")
    lim     = l.get("per_user_limit", 1)
    lid     = l.get("listing_id", "???")
    prefix  = f"`{index}.` " if index else ""
    status  = "🟢" if l.get("status") == "active" else "🔴"
    return (
        f"{prefix}{status} **{char}** — {r_str}\n"
        f"   📺 *{anime}* | 💰 `{price:,}` kakera\n"
        f"   📦 Stock: `{stock}` left | ✅ Sold: `{sold}` | 🔒 Max/user: `{lim}`\n"
        f"   🆔 `{lid}`"
    )


# ── /market ───────────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command(["market", "shop", "store"]))
async def market_cmd(_, m: Message):
    parts   = m.text.split()
    rarity  = parts[1].lower() if len(parts) > 1 else None
    page    = 1

    listings = await get_active_market_listings(rarity=rarity, skip=0, limit=LISTINGS_PER_PAGE)
    total    = await count_active_market_listings(rarity=rarity)

    if not listings:
        filter_str = f" for rarity **{rarity}**" if rarity else ""
        await m.reply(f"🏪 **Market** is empty{filter_str}.\nSellers can list characters with `/list`.")
        return

    pages = (total + LISTINGS_PER_PAGE - 1) // LISTINGS_PER_PAGE
    lines = [_listing_text(l, i + 1) for i, l in enumerate(listings)]

    header = f"🏪 **Market** | {total} listings | Page {page}/{pages}"
    if rarity:
        header += f" | Filter: **{rarity}**"

    buttons = []
    if pages > 1:
        buttons.append([IKB("Next ▶", callback_data=f"market:{rarity or 'all'}:2")])

    markup = IKM(buttons) if buttons else None
    await m.reply(header + "\n\n" + "\n\n".join(lines), reply_markup=markup)


@_soul.app.on_callback_query(filters.regex(r"^market:(\w+):(\d+)$"))
async def market_page_cb(_, cq: CallbackQuery):
    _, rarity_key, page_str = cq.data.split(":")
    page    = int(page_str)
    rarity  = None if rarity_key == "all" else rarity_key
    skip    = (page - 1) * LISTINGS_PER_PAGE

    listings = await get_active_market_listings(rarity=rarity, skip=skip, limit=LISTINGS_PER_PAGE)
    total    = await count_active_market_listings(rarity=rarity)

    if not listings:
        await cq.answer("No more listings.", show_alert=True)
        return

    pages = (total + LISTINGS_PER_PAGE - 1) // LISTINGS_PER_PAGE
    lines = [_listing_text(l, skip + i + 1) for i, l in enumerate(listings)]

    header = f"🏪 **Market** | {total} listings | Page {page}/{pages}"
    if rarity:
        header += f" | Filter: **{rarity}**"

    nav = []
    if page > 1:
        nav.append(IKB("◀ Prev", callback_data=f"market:{rarity_key}:{page - 1}"))
    if page < pages:
        nav.append(IKB("Next ▶", callback_data=f"market:{rarity_key}:{page + 1}"))

    markup = IKM([nav]) if nav else None
    await cq.message.edit_text(header + "\n\n" + "\n\n".join(lines), reply_markup=markup)
    await cq.answer()


# ── /list <instanceID> <price> <stock> [per_user_limit] ──────────────────────

@_soul.app.on_message(filters.command(["list", "addlisting", "sell"]))
async def list_cmd(_, m: Message):
    parts = m.text.split()
    if len(parts) < 3:
        await m.reply(
            "Usage: `/list <instanceID> <price> [stock] [per_user_limit]`\n"
            "Example: `/list A1B2 500 10 2`"
        )
        return

    instance_id    = parts[1].upper()
    price_str      = parts[2]
    stock_str      = parts[3] if len(parts) > 3 else "1"
    per_user_str   = parts[4] if len(parts) > 4 else "1"

    if not price_str.isdigit() or not stock_str.isdigit() or not per_user_str.isdigit():
        await m.reply("❌ Price, stock, and per_user_limit must be positive integers.")
        return

    price         = int(price_str)
    stock         = max(1, min(int(stock_str), 9999))
    per_user_limit = max(1, min(int(per_user_str), stock))
    uid           = m.from_user.id

    if price < 10:
        await m.reply("❌ Minimum listing price is **10** kakera.")
        return

    char = await get_harem_char(uid, instance_id)
    if not char:
        await m.reply("❌ Character not found in your harem.")
        return

    if not can_list_on_market(char["rarity"]):
        r_str = rarity_display(char["rarity"])
        await m.reply(f"❌ **{r_str}** characters cannot be listed on the market.")
        return

    listing_id = str(uuid.uuid4())[:8].upper()
    now        = datetime.utcnow()

    listing_doc = {
        "listing_id":     listing_id,
        "seller_id":      uid,
        "instance_id":    instance_id,
        "char_id":        char["char_id"],
        "char_name":      char["name"],
        "anime":          char.get("anime", "Unknown"),
        "rarity":         char["rarity"],
        "img_url":        char.get("img_url", ""),
        "price":          price,
        "stock_total":    stock,
        "stock_remaining": stock,
        "stock_sold":     0,
        "per_user_limit": per_user_limit,
        "status":         "active",
        "added_at":       now,
    }

    await create_market_listing(listing_doc)
    r_str = rarity_display(char["rarity"])

    await m.reply(
        f"✅ **Listed on market!**\n\n"
        f"🎴 **{char['name']}** — {r_str}\n"
        f"💰 Price: `{price:,}` kakera\n"
        f"📦 Stock: `{stock}` | Max/user: `{per_user_limit}`\n"
        f"🆔 Listing ID: `{listing_id}`\n\n"
        "⚠️ Note: Your character instance stays in your harem until purchased."
    )


# ── /buy <listingID> [qty] ────────────────────────────────────────────────────

@_soul.app.on_message(filters.command(["buy", "purchase"]))
async def buy_cmd(_, m: Message):
    parts = m.text.split()
    if len(parts) < 2:
        await m.reply("Usage: `/buy <listingID> [qty]`")
        return

    listing_id = parts[1].upper()
    qty        = max(1, int(parts[2])) if len(parts) > 2 and parts[2].isdigit() else 1
    uid        = m.from_user.id

    listing = await get_market_listing(listing_id)
    if not listing:
        await m.reply(f"❌ Listing `{listing_id}` not found.")
        return
    if listing["status"] != "active":
        await m.reply(f"❌ Listing `{listing_id}` is no longer active.")
        return
    if listing["seller_id"] == uid:
        await m.reply("❌ You can't buy your own listing.")
        return

    per_user_limit = listing.get("per_user_limit", 1)
    already_bought = await get_user_market_purchase_count(uid, listing_id)
    can_buy        = min(qty, per_user_limit - already_bought, listing["stock_remaining"])

    if can_buy <= 0:
        await m.reply(
            f"❌ You've reached the per-user limit for `{listing_id}`.\n"
            f"(Max {per_user_limit} per user)"
        )
        return

    total_cost = listing["price"] * can_buy
    success    = await deduct_balance(uid, total_cost)
    if not success:
        bal = await _soul.app.invoke  # just check
        from SoulCatcher.database import get_balance
        bal = await get_balance(uid)
        await m.reply(
            f"❌ Not enough kakera.\n"
            f"You have: `{bal:,}` | Need: `{total_cost:,}`"
        )
        return

    # Atomically claim stock qty times
    bought = 0
    for _ in range(can_buy):
        updated = await atomic_market_buy(listing_id)
        if updated:
            bought += 1
            # Log purchase
            await log_market_purchase({
                "listing_id":  listing_id,
                "buyer_id":    uid,
                "seller_id":   listing["seller_id"],
                "char_name":   listing["char_name"],
                "rarity":      listing["rarity"],
                "price":       listing["price"],
                "purchased_at": datetime.utcnow(),
            })
            # Pay seller
            await add_balance(listing["seller_id"], listing["price"])
        else:
            # Ran out of stock — refund remaining
            refund = (can_buy - bought) * listing["price"]
            if refund > 0:
                await add_balance(uid, refund)
            break

    if bought == 0:
        await add_balance(uid, total_cost)
        await m.reply("❌ Listing sold out before your purchase completed.")
        return

    # Grant XP
    xp_gain = ECONOMY["market_buy_xp"] * bought
    await add_xp(uid, xp_gain)

    r_str = rarity_display(listing["rarity"])
    await m.reply(
        f"🛒 **Purchased!**\n\n"
        f"🎴 **{listing['char_name']}** — {r_str}\n"
        f"🔢 Qty: `{bought}`\n"
        f"💰 Total paid: `{bought * listing['price']:,}` kakera\n"
        f"⭐ +{xp_gain} XP\n\n"
        "ℹ️ The character has been transferred to your harem."
    )


# ── /removelisting ────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command(["removelisting", "delist", "unlist"]))
async def remove_listing_cmd(_, m: Message):
    parts = m.text.split()
    if len(parts) < 2:
        await m.reply("Usage: `/removelisting <listingID>`")
        return

    listing_id = parts[1].upper()
    uid        = m.from_user.id
    listing    = await get_market_listing(listing_id)

    if not listing:
        await m.reply(f"❌ Listing `{listing_id}` not found.")
        return

    is_owner = listing["seller_id"] == uid
    is_admin = _soul.is_sudo(uid)

    if not is_owner and not is_admin:
        await m.reply("❌ You can only remove your own listings.")
        return

    if listing["status"] != "active":
        await m.reply(f"❌ Listing `{listing_id}` is not active.")
        return

    await update_market_listing(listing_id, {"$set": {"status": "removed", "removed_at": datetime.utcnow()}})
    await m.reply(f"✅ Listing `{listing_id}` (**{listing['char_name']}**) has been removed.")


# ── /marketstats ──────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command(["marketstats", "mstats"]))
async def market_stats_cmd(_, m: Message):
    stats = await market_aggregate_stats()
    await m.reply(
        f"📊 **Market Statistics**\n\n"
        f"📋 Total listings:  `{stats['total_listings']:,}`\n"
        f"🟢 Active:          `{stats['active']:,}`\n"
        f"🔴 Sold out:        `{stats['soldout']:,}`\n"
        f"🗑 Removed:         `{stats['removed']:,}`\n"
        f"🛒 Total purchases: `{stats['total_purchases']:,}`\n"
        f"💰 Kakera traded:   `{stats['kakera_spent']:,}`"
    )


# ── /topselling ───────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command(["topselling", "bestsellers"]))
async def top_selling_cmd(_, m: Message):
    listings = await top_market_listings(10)
    if not listings:
        await m.reply("No market data yet.")
        return

    lines = []
    for i, l in enumerate(listings, 1):
        r_str = rarity_display(l["rarity"])
        lines.append(
            f"`{i:>2}.` **{l['char_name']}** — {r_str} | "
            f"✅ {l.get('stock_sold', 0)} sold | 💰 {l.get('price', 0):,}"
        )

    await m.reply("🏆 **Top Selling Characters**\n\n" + "\n".join(lines))


# ── /mylistings ───────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command(["mylistings", "mymarket"]))
async def my_listings_cmd(_, m: Message):
    uid = m.from_user.id

    # Get all listings by this user
    all_active = await get_active_market_listings()
    mine = [l for l in all_active if l.get("seller_id") == uid]

    if not mine:
        await m.reply("📭 You have no active listings.\nUse `/list` to add one!")
        return

    lines = [_listing_text(l, i + 1) for i, l in enumerate(mine)]
    await m.reply("🏪 **Your Active Listings**\n\n" + "\n\n".join(lines))


# ── /mypurchases ──────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command(["mypurchases", "buyhistory"]))
async def my_purchases_cmd(_, m: Message):
    uid      = m.from_user.id
    history  = await get_user_market_history(uid, limit=10)

    if not history:
        await m.reply("📭 No purchase history yet.")
        return

    lines = []
    for h in history:
        r_str = rarity_display(h.get("rarity", "common"))
        date  = h.get("purchased_at", "?")
        if hasattr(date, "strftime"):
            date = date.strftime("%Y-%m-%d")
        lines.append(f"🎴 **{h['char_name']}** — {r_str} | 💰 `{h['price']:,}` | `{date}`")

    await m.reply("🛒 **Your Purchase History** (last 10)\n\n" + "\n".join(lines))
