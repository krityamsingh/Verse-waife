"""SoulCatcher/modules/market.py
Commands: /market_list (list on market)  /buy  /market
Note: Direct char selling for kakera is in sell.py.
      This file handles the player-to-player market listing system.

Split from collection.py.
"""

from __future__ import annotations
import uuid
import logging
from datetime import datetime

from pyrogram import enums, filters
from pyrogram.types import Message

from .. import app
from ..rarity import ECONOMY, get_rarity, can_trade
from ..database import (
    get_harem_char, remove_from_harem,
    add_balance, deduct_balance, get_balance,
    count_rarity_in_harem,
    create_listing, get_listing, get_active_listings, atomic_buy_listing,
    add_to_harem,
)

log = logging.getLogger("SoulCatcher.market")


def _fmt(n) -> str:
    try:
        return f"{int(n):,}"
    except Exception:
        return str(n)


# ─────────────────────────────────────────────────────────────────────────────
# /list — put a character on the market
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command(["list", "mlist"]))
async def cmd_list(_, message: Message):
    args = message.command
    if len(args) < 3:
        return await message.reply_text(
            "Usage: `/list <instance_id> <price>`\nExample: `/list A1B2C3 5000`"
        )

    uid = message.from_user.id
    iid = args[1].upper()
    try:
        price = int(args[2])
    except ValueError:
        return await message.reply_text("❌ Price must be a number.")
    if price < 1:
        return await message.reply_text("❌ Price must be at least 1 kakera.")

    char = await get_harem_char(uid, iid)
    if not char:
        return await message.reply_text("❌ Character not found in your harem.")
    if not can_trade(char.get("rarity", "")):
        return await message.reply_text("❌ This rarity cannot be listed on the market.")

    listing_fee = ECONOMY.get("market_listing_fee", 50)
    bal = await get_balance(uid)
    if bal < listing_fee:
        return await message.reply_text(
            f"❌ Listing fee is `{listing_fee}` kakera. You only have `{_fmt(bal)}`."
        )

    removed = await remove_from_harem(uid, iid)
    if not removed:
        return await message.reply_text("❌ Failed to list — character may have moved.")

    await deduct_balance(uid, listing_fee)
    lid = str(uuid.uuid4())[:8].upper()
    await create_listing({
        "listing_id":  lid,
        "seller_id":   uid,
        "instance_id": iid,
        "char_id":     char.get("char_id", ""),
        "name":        char["name"],
        "anime":       char.get("anime", ""),
        "rarity":      char.get("rarity", ""),
        "img_url":     char.get("img_url", ""),
        "video_url":   char.get("video_url", ""),
        "price":       price,
        "status":      "active",
        "listed_at":   datetime.utcnow(),
    })

    tier       = get_rarity(char.get("rarity", ""))
    rarity_str = f"{tier.emoji} {tier.display_name}" if tier else char.get("rarity", "?")

    await message.reply_text(
        f"✅ **Listed on Market!**\n\n"
        f"🆔 Listing: `{lid}`\n"
        f"👤 **{char['name']}**\n"
        f"{rarity_str}\n"
        f"💰 Price: `{_fmt(price)}` kakera\n"
        f"📋 Listing fee: `{listing_fee}` kakera"
    )
    log.info("LIST: uid=%d listed iid=%s for %d kakera (lid=%s)", uid, iid, price, lid)


# ─────────────────────────────────────────────────────────────────────────────
# /buy
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("buy"))
async def cmd_buy(_, message: Message):
    args = message.command
    if len(args) < 2:
        return await message.reply_text("Usage: `/buy <listing_id>`", parse_mode=enums.ParseMode.MARKDOWN)

    uid = message.from_user.id
    lid = args[1].upper()

    listing = await get_listing(lid)
    if not listing or listing["status"] != "active":
        return await message.reply_text("❌ Listing not found or already sold.")
    if listing["seller_id"] == uid:
        return await message.reply_text("❌ You can't buy your own listing!")

    price = listing["price"]
    bal   = await get_balance(uid)
    if bal < price:
        return await message.reply_text(
            f"❌ Not enough kakera! Need `{_fmt(price)}`, you have `{_fmt(bal)}`."
        )

    tier = get_rarity(listing.get("rarity", ""))
    if tier and tier.max_per_user > 0:
        count = await count_rarity_in_harem(uid, listing["rarity"])
        if count >= tier.max_per_user:
            return await message.reply_text(
                f"❌ You already have the max ({tier.max_per_user}) "
                f"**{tier.display_name}** characters!"
            )

    sold = await atomic_buy_listing(lid, uid)
    if not sold:
        return await message.reply_text("❌ Listing already sold.")

    await deduct_balance(uid, price)
    await add_balance(listing["seller_id"], price)

    char_doc = {
        "id":        listing.get("char_id", ""),
        "name":      listing["name"],
        "anime":     listing.get("anime", ""),
        "rarity":    listing.get("rarity", "common"),
        "img_url":   listing.get("img_url", ""),
        "video_url": listing.get("video_url", ""),
    }
    await add_to_harem(uid, char_doc)

    rarity_str = f"{tier.emoji} {tier.display_name}" if tier else listing.get("rarity", "?")
    await message.reply_text(
        f"✅ **Purchased!**\n\n"
        f"👤 **{listing['name']}**\n"
        f"{rarity_str}\n"
        f"💰 Paid: `{_fmt(price)}` kakera"
    )
    log.info("BUY: uid=%d bought lid=%s for %d kakera", uid, lid, price)


# ─────────────────────────────────────────────────────────────────────────────
# /market — browse active listings
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("market"))
async def cmd_market(_, message: Message):
    args           = message.command
    rarity_filter  = args[1].lower() if len(args) > 1 else None
    listings       = await get_active_listings(rarity=rarity_filter, limit=10)

    if not listings:
        label = f" for **{rarity_filter}**" if rarity_filter else ""
        return await message.reply_text(f"🛒 No active listings{label}.")

    header = "🛒 **Market Listings**"
    if rarity_filter:
        header += f" — {rarity_filter}"
    lines = [header + "\n"]

    for listing in listings:
        tier  = get_rarity(listing.get("rarity", ""))
        emoji = tier.emoji if tier else "❓"
        lines.append(
            f"{emoji} **{listing['name']}** `{listing['listing_id']}`\n"
            f"  _{listing.get('anime', '?')}_ | 💰 `{_fmt(listing['price'])}` kakera"
        )

    lines.append("\nBuy with: `/buy <listing_id>`")
    await message.reply_text("\n".join(lines))
