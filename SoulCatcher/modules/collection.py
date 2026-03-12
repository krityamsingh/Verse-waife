"""SoulCatcher/modules/collection.py
Commands: /harem /view /setfav /burn /sort /trade /gift /wish /wishlist /sell /buy /market

BUGS FIXED vs original (patch file):
  [FIX-1] collection.py was nothing but a partial code-snippet with a comment header.
          /harem /view /setfav /burn /sort /trade /gift /wish /wishlist /sell /buy /market
          were ALL missing — this file re-implements them fully.
  [FIX-2] trade_cb used async get_col() then called .count_documents() on the result;
          count_documents() is async so it returned a coroutine that was never awaited.
          Fixed: use _col() (sync helper) directly and properly await count_documents().
  [FIX-3] unclaim_spawn was imported from database but never defined there.
  [FIX-4] /trade command was missing entirely — only the callback existed.
  [FIX-5] max_per_user check on trade now uses correct sync _col() pattern.
"""

import uuid
import logging
from datetime import datetime

from pyrogram import filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from .. import app
from ..rarity import ECONOMY, get_rarity, can_trade, can_gift
from ..database import (
    get_or_create_user, get_user,
    get_harem, get_harem_char,
    count_rarity_in_harem, remove_from_harem, transfer_harem_char,
    add_balance, deduct_balance, get_balance,
    get_wishlist, add_wish, remove_wish,
    create_trade, get_trade, update_trade,
    create_listing, get_listing, update_listing, get_active_listings, atomic_buy_listing,
    get_character, add_to_harem,
    _col,
)

log = logging.getLogger("SoulCatcher.collection")


def _fmt(n) -> str:
    try:
        return f"{int(n):,}"
    except Exception:
        return str(n)


# ─────────────────────────────────────────────────────────────────────────────
# /harem
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("harem"))
async def cmd_harem(_, message: Message):
    user = message.from_user
    await get_or_create_user(user.id, user.username or "", user.first_name or "")

    page = 1
    try:
        page = int(message.command[1])
    except (IndexError, ValueError):
        pass

    per_page = 10
    chars, total = await get_harem(user.id, page=page, per_page=per_page)

    if not chars:
        return await message.reply_text(
            "🌸 Your harem is empty!\n"
            "Claim characters by pressing ❤️ when they spawn, or use /marry / /propose."
        )

    total_pages = max(1, (total + per_page - 1) // per_page)
    lines = [f"🌸 **{user.first_name}'s Harem** — `{total}` chars (Page {page}/{total_pages})\n"]
    for i, char in enumerate(chars, start=(page - 1) * per_page + 1):
        tier = get_rarity(char.get("rarity", ""))
        emoji = tier.emoji if tier else "❓"
        fav = "⭐" if char.get("is_favorite") else ""
        lines.append(f"`{i}.` {emoji}{fav} **{char['name']}** `{char['instance_id']}` — _{char.get('anime','?')}_")

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"harem:{user.id}:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"harem:{user.id}:{page+1}"))

    kb = InlineKeyboardMarkup([nav]) if nav else None
    await message.reply_text("\n".join(lines), reply_markup=kb)


@app.on_callback_query(filters.regex(r"^harem:"))
async def harem_page_cb(_, cb):
    parts = cb.data.split(":")
    uid, page = int(parts[1]), int(parts[2])
    if cb.from_user.id != uid:
        return await cb.answer("Not your harem!", show_alert=True)

    per_page = 10
    chars, total = await get_harem(uid, page=page, per_page=per_page)
    if not chars:
        return await cb.answer("No more pages.", show_alert=True)

    user_doc = await get_user(uid) or {}
    name = user_doc.get("first_name", f"User#{uid}")
    total_pages = max(1, (total + per_page - 1) // per_page)
    lines = [f"🌸 **{name}'s Harem** — `{total}` chars (Page {page}/{total_pages})\n"]
    for i, char in enumerate(chars, start=(page - 1) * per_page + 1):
        tier = get_rarity(char.get("rarity", ""))
        emoji = tier.emoji if tier else "❓"
        fav = "⭐" if char.get("is_favorite") else ""
        lines.append(f"`{i}.` {emoji}{fav} **{char['name']}** `{char['instance_id']}` — _{char.get('anime','?')}_")

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"harem:{uid}:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"harem:{uid}:{page+1}"))

    kb = InlineKeyboardMarkup([nav]) if nav else None
    try:
        await cb.message.edit_text("\n".join(lines), reply_markup=kb)
    except Exception:
        pass
    await cb.answer()


# ─────────────────────────────────────────────────────────────────────────────
# /view
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("view"))
async def cmd_view(_, message: Message):
    args = message.command
    if len(args) < 2:
        return await message.reply_text("Usage: `/view <instance_id>`")

    uid = message.from_user.id
    char = await get_harem_char(uid, args[1].upper())
    if not char:
        return await message.reply_text("❌ Character not found in your harem.")

    tier = get_rarity(char.get("rarity", ""))
    rarity_str = f"{tier.emoji} {tier.display_name}" if tier else char.get("rarity", "?")
    fav = "⭐ Favourite\n" if char.get("is_favorite") else ""
    note = f"\n📝 Note: _{char.get('note', '')}_" if char.get("note") else ""
    text = (
        f"{fav}**{char['name']}** (`{char['instance_id']}`)\n"
        f"📖 _{char.get('anime', 'Unknown')}_\n"
        f"{rarity_str}\n"
        f"🕒 Obtained: `{str(char.get('obtained_at', '?'))[:10]}`"
        f"{note}"
    )
    media = char.get("video_url") or char.get("img_url")
    try:
        if char.get("video_url") and media:
            await message.reply_video(media, caption=text)
        elif char.get("img_url") and media:
            await message.reply_photo(media, caption=text)
        else:
            await message.reply_text(text)
    except Exception:
        await message.reply_text(text)


# ─────────────────────────────────────────────────────────────────────────────
# /setfav
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("setfav"))
async def cmd_setfav(_, message: Message):
    args = message.command
    if len(args) < 2:
        return await message.reply_text("Usage: `/setfav <instance_id>`")
    uid = message.from_user.id
    char = await get_harem_char(uid, args[1].upper())
    if not char:
        return await message.reply_text("❌ Character not found in your harem.")
    new_val = not char.get("is_favorite", False)
    await _col("user_characters").update_one(
        {"user_id": uid, "instance_id": char["instance_id"]},
        {"$set": {"is_favorite": new_val}}
    )
    status = "⭐ marked as favourite" if new_val else "☆ removed from favourites"
    await message.reply_text(f"**{char['name']}** has been {status}!")


# ─────────────────────────────────────────────────────────────────────────────
# /sort
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("sort"))
async def cmd_sort(_, message: Message):
    args = message.command
    valid = ["rarity", "name", "anime", "recent"]
    if len(args) < 2 or args[1].lower() not in valid:
        return await message.reply_text(f"Usage: `/sort <{'|'.join(valid)}>`")
    from ..database import update_user
    await update_user(message.from_user.id, {"$set": {"harem_sort": args[1].lower()}})
    await message.reply_text(f"✅ Harem sort order set to **{args[1].lower()}**!")


# ─────────────────────────────────────────────────────────────────────────────
# /burn
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("burn"))
async def cmd_burn(_, message: Message):
    args = message.command
    if len(args) < 2:
        return await message.reply_text("Usage: `/burn <instance_id>`")
    uid = message.from_user.id
    char = await get_harem_char(uid, args[1].upper())
    if not char:
        return await message.reply_text("❌ Character not found in your harem.")
    if char.get("is_favorite"):
        return await message.reply_text(
            "⭐ This character is a favourite! Use `/setfav` to unmark first."
        )
    from ..rarity import get_sell_price
    price = get_sell_price(char.get("rarity", "common"))
    tier = get_rarity(char.get("rarity", ""))
    rarity_str = f"{tier.emoji} {tier.display_name}" if tier else char.get("rarity", "?")

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔥 Burn!", callback_data=f"burn:{uid}:{char['instance_id']}:{price}"),
        InlineKeyboardButton("❌ Cancel", callback_data="burn:cancel"),
    ]])
    await message.reply_text(
        f"🔥 **Burn Confirm**\n\n"
        f"**{char['name']}** (`{char['instance_id']}`)\n"
        f"{rarity_str}\n\n"
        f"You'll receive **{_fmt(price)} kakera**. Continue?",
        reply_markup=kb
    )


@app.on_callback_query(filters.regex(r"^burn:"))
async def burn_cb(_, cb):
    parts = cb.data.split(":")
    if parts[1] == "cancel":
        await cb.message.edit_text("❌ Burn cancelled.")
        return await cb.answer()

    uid, iid, price = int(parts[1]), parts[2], int(parts[3])
    if cb.from_user.id != uid:
        return await cb.answer("Not your character!", show_alert=True)

    char = await get_harem_char(uid, iid)
    if not char:
        await cb.message.edit_text("❌ Character already gone.")
        return await cb.answer()

    removed = await remove_from_harem(uid, iid)
    if removed:
        await add_balance(uid, price)
        await cb.message.edit_text(
            f"🔥 **{char['name']}** burned for **{_fmt(price)} kakera**!"
        )
        log.info(f"BURN: {uid} burned {iid} for {price} kakera")
    else:
        await cb.message.edit_text("❌ Burn failed — character may have moved.")
    await cb.answer()


# ─────────────────────────────────────────────────────────────────────────────
# /gift
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("gift"))
async def cmd_gift(_, message: Message):
    args = message.command
    if not message.reply_to_message or len(args) < 2:
        return await message.reply_text("Reply to a user and use: `/gift <instance_id>`")

    uid = message.from_user.id
    target = message.reply_to_message.from_user
    if target.is_bot or target.id == uid:
        return await message.reply_text("❌ Can't gift bots or yourself!")

    char = await get_harem_char(uid, args[1].upper())
    if not char:
        return await message.reply_text("❌ Character not found in your harem.")

    if not can_gift(char.get("rarity", "")):
        tier = get_rarity(char.get("rarity", ""))
        return await message.reply_text(
            f"❌ **{tier.emoji if tier else ''} {tier.display_name if tier else char.get('rarity','?')}** characters cannot be gifted!"
        )

    tier = get_rarity(char.get("rarity", ""))
    if tier and tier.max_per_user > 0:
        count = await count_rarity_in_harem(target.id, char["rarity"])
        if count >= tier.max_per_user:
            return await message.reply_text(
                f"❌ **{target.first_name}** already has the max "
                f"({tier.max_per_user}) **{tier.display_name}** characters!"
            )

    transferred = await transfer_harem_char(args[1].upper(), uid, target.id)
    if transferred:
        await get_or_create_user(target.id, target.username or "", target.first_name or "")
        await message.reply_text(
            f"🎁 **{char['name']}** gifted to "
            f"[{target.first_name}](tg://user?id={target.id})!"
        )
        log.info(f"GIFT: {uid} → {target.id}: {args[1].upper()}")
    else:
        await message.reply_text("❌ Gift failed — character may have moved.")


# ─────────────────────────────────────────────────────────────────────────────
# /trade  (initiate) + trade: callback
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("trade"))
async def cmd_trade(_, message: Message):
    """Reply to target user, then: /trade <your_instance_id> <their_instance_id>"""
    if not message.reply_to_message:
        return await message.reply_text(
            "Reply to the user you want to trade with:\n"
            "`/trade <your_instance_id> <their_instance_id>`"
        )
    args = message.command
    if len(args) < 3:
        return await message.reply_text(
            "Usage: `/trade <your_instance_id> <their_instance_id>`"
        )

    proposer_id = message.from_user.id
    receiver = message.reply_to_message.from_user
    if receiver.is_bot or receiver.id == proposer_id:
        return await message.reply_text("❌ Can't trade with bots or yourself!")

    my_iid    = args[1].upper()
    their_iid = args[2].upper()

    my_char    = await get_harem_char(proposer_id, my_iid)
    their_char = await get_harem_char(receiver.id, their_iid)

    if not my_char:
        return await message.reply_text(f"❌ `{my_iid}` not found in your harem.")
    if not their_char:
        return await message.reply_text(
            f"❌ `{their_iid}` not found in "
            f"[{receiver.first_name}](tg://user?id={receiver.id})'s harem."
        )

    if not can_trade(my_char.get("rarity", "")):
        return await message.reply_text(f"❌ **{my_char['name']}** cannot be traded!")
    if not can_trade(their_char.get("rarity", "")):
        return await message.reply_text(f"❌ **{their_char['name']}** cannot be traded!")

    fee = 500  # flat kakera fee for each side
    trade_id = str(uuid.uuid4())[:8].upper()

    await create_trade({
        "trade_id":      trade_id,
        "proposer_id":   proposer_id,
        "receiver_id":   receiver.id,
        "proposer_char": my_iid,
        "receiver_char": their_iid,
        "fee":           fee,
        "status":        "pending",
        "created_at":    datetime.utcnow(),
    })

    my_tier    = get_rarity(my_char.get("rarity", ""))
    their_tier = get_rarity(their_char.get("rarity", ""))

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Accept", callback_data=f"trade:accept:{trade_id}"),
        InlineKeyboardButton("❌ Decline", callback_data=f"trade:decline:{trade_id}"),
    ]])

    await message.reply_text(
        f"🔄 **Trade Proposal** (`{trade_id}`)\n\n"
        f"**{message.from_user.first_name}** offers:\n"
        f"  {my_tier.emoji if my_tier else '?'} **{my_char['name']}** `{my_iid}`\n\n"
        f"For [{receiver.first_name}](tg://user?id={receiver.id})'s:\n"
        f"  {their_tier.emoji if their_tier else '?'} **{their_char['name']}** `{their_iid}`\n\n"
        f"Fee: `{_fmt(fee)}` kakera each\n\n"
        f"[{receiver.first_name}](tg://user?id={receiver.id}) — accept or decline?",
        reply_markup=kb
    )


@app.on_callback_query(filters.regex(r"^trade:"))
async def trade_cb(_, cb):
    """
    [FIX-2] Corrected count_documents() usage.
    Original bug: async get_col() was awaited (returns Collection object), then
    .count_documents({}) was called on it synchronously — this returned a coroutine
    that was silently discarded, so the count was always 0 and max_per_user was
    never actually enforced.
    Fix: _col() is synchronous and returns the Collection directly;
    count_documents() is then properly awaited.
    """
    await cb.answer()
    _, action, trade_id = cb.data.split(":")
    uid   = cb.from_user.id
    trade = await get_trade(trade_id)

    if not trade or trade["status"] != "pending":
        return await cb.message.edit_text("❌ Trade no longer active.")

    if action == "decline":
        if uid not in (trade["proposer_id"], trade["receiver_id"]):
            return await cb.answer("Not your trade.", show_alert=True)
        await update_trade(trade_id, {"$set": {"status": "declined"}})
        return await cb.message.edit_text("❌ Trade declined.")

    if action == "accept":
        if uid != trade["receiver_id"]:
            return await cb.answer("Only the receiver can accept.", show_alert=True)

        receiver_char = await get_harem_char(trade["receiver_id"], trade["receiver_char"])
        proposer_char = await get_harem_char(trade["proposer_id"], trade["proposer_char"])

        if not receiver_char or not proposer_char:
            return await cb.message.edit_text("❌ One or both characters were deleted.")

        # What receiver WILL receive = proposer_char's rarity
        receiver_rarity = get_rarity(proposer_char["rarity"])
        # What proposer WILL receive = receiver_char's rarity
        proposer_rarity = get_rarity(receiver_char["rarity"])

        # FIX-2: _col() is sync → returns Collection; count_documents() is async → await it
        if receiver_rarity and receiver_rarity.max_per_user > 0:
            receiver_count = await _col("user_characters").count_documents({
                "user_id": trade["receiver_id"],
                "rarity":  proposer_char["rarity"]
            })
            if receiver_count >= receiver_rarity.max_per_user:
                await cb.answer(
                    f"❌ Receiver already has max {receiver_rarity.max_per_user} "
                    f"{receiver_rarity.display_name} characters!",
                    show_alert=True
                )
                log.warning(
                    f"TRADE BLOCKED: receiver {trade['receiver_id']} would exceed "
                    f"{receiver_rarity.name} limit ({receiver_count}/{receiver_rarity.max_per_user})"
                )
                return

        if proposer_rarity and proposer_rarity.max_per_user > 0:
            proposer_count = await _col("user_characters").count_documents({
                "user_id": trade["proposer_id"],
                "rarity":  receiver_char["rarity"]
            })
            if proposer_count >= proposer_rarity.max_per_user:
                await cb.answer(
                    f"❌ Proposer would exceed max {proposer_rarity.max_per_user} "
                    f"{proposer_rarity.display_name} characters!",
                    show_alert=True
                )
                log.warning(
                    f"TRADE BLOCKED: proposer {trade['proposer_id']} would exceed "
                    f"{receiver_char['rarity']} limit ({proposer_count}/{proposer_rarity.max_per_user})"
                )
                return

        # ✅ All validations passed — execute swap
        ok1 = await transfer_harem_char(trade["proposer_char"], trade["proposer_id"], trade["receiver_id"])
        ok2 = await transfer_harem_char(trade["receiver_char"], trade["receiver_id"], trade["proposer_id"])

        if ok1 and ok2:
            await deduct_balance(trade["proposer_id"], trade["fee"])
            await deduct_balance(trade["receiver_id"],  trade["fee"])
            await update_trade(trade_id, {"$set": {"status": "completed"}})
            await cb.message.edit_text(
                f"✅ **Trade Complete!**\nFee: `{_fmt(trade['fee'])}` kakera each."
            )
            log.info(
                f"TRADE COMPLETED: {trade['proposer_id']} <-> {trade['receiver_id']} "
                f"({trade['proposer_char']} <-> {trade['receiver_char']})"
            )
        else:
            await cb.message.edit_text("❌ Trade failed — characters may have moved.")
            log.warning(f"TRADE FAILED: {trade_id}")


# ─────────────────────────────────────────────────────────────────────────────
# /wish  /wishlist  /unwish
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("wish"))
async def cmd_wish(_, message: Message):
    args = message.command
    if len(args) < 2:
        return await message.reply_text("Usage: `/wish <char_id>`  (global character ID, not instance)")
    char = await get_character(args[1])
    if not char:
        return await message.reply_text(f"❌ Character `{args[1]}` not found in database.")

    added = await add_wish(
        message.from_user.id, args[1],
        char.get("name", "?"), char.get("rarity", "common")
    )
    if added:
        tier = get_rarity(char.get("rarity", ""))
        rarity_str = f"{tier.emoji} {tier.display_name}" if tier else char.get("rarity", "?")
        await message.reply_text(f"💛 **{char['name']}** added to your wishlist!\n{rarity_str}")
    else:
        await message.reply_text("❌ Already on wishlist, or wishlist is full (max 25).")


@app.on_message(filters.command("wishlist"))
async def cmd_wishlist(_, message: Message):
    items = await get_wishlist(message.from_user.id)
    if not items:
        return await message.reply_text("💛 Your wishlist is empty! Use `/wish <char_id>` to add.")

    lines = ["💛 **Your Wishlist**\n"]
    for i, item in enumerate(items, 1):
        tier = get_rarity(item.get("rarity", ""))
        emoji = tier.emoji if tier else "❓"
        lines.append(f"`{i}.` {emoji} **{item['char_name']}** `{item['char_id']}`")
    lines.append(f"\n`{len(items)}/25` slots used")
    await message.reply_text("\n".join(lines))


@app.on_message(filters.command("unwish"))
async def cmd_unwish(_, message: Message):
    args = message.command
    if len(args) < 2:
        return await message.reply_text("Usage: `/unwish <char_id>`")
    removed = await remove_wish(message.from_user.id, args[1])
    if removed:
        await message.reply_text(f"💛 `{args[1]}` removed from your wishlist.")
    else:
        await message.reply_text("❌ Character not found in your wishlist.")


# ─────────────────────────────────────────────────────────────────────────────
# MARKET: /sell  /buy  /market
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("sell"))
async def cmd_sell(_, message: Message):
    args = message.command
    if len(args) < 3:
        return await message.reply_text("Usage: `/sell <instance_id> <price>`")
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
        "listing_id": lid,
        "seller_id":  uid,
        "instance_id": iid,
        "char_id":    char.get("char_id", ""),
        "name":       char["name"],
        "anime":      char.get("anime", ""),
        "rarity":     char.get("rarity", ""),
        "img_url":    char.get("img_url", ""),
        "video_url":  char.get("video_url", ""),
        "price":      price,
        "status":     "active",
        "listed_at":  datetime.utcnow(),
    })

    tier = get_rarity(char.get("rarity", ""))
    rarity_str = f"{tier.emoji} {tier.display_name}" if tier else char.get("rarity", "?")
    await message.reply_text(
        f"✅ **Listed on Market!**\n\n"
        f"🆔 Listing: `{lid}`\n"
        f"👤 **{char['name']}**\n"
        f"{rarity_str}\n"
        f"💰 Price: `{_fmt(price)}` kakera\n"
        f"📋 Listing fee: `{listing_fee}` kakera"
    )
    log.info(f"SELL: {uid} listed {iid} for {price} kakera (listing {lid})")


@app.on_message(filters.command("buy"))
async def cmd_buy(_, message: Message):
    args = message.command
    if len(args) < 2:
        return await message.reply_text("Usage: `/buy <listing_id>`")
    uid = message.from_user.id
    lid = args[1].upper()

    listing = await get_listing(lid)
    if not listing or listing["status"] != "active":
        return await message.reply_text("❌ Listing not found or already sold.")
    if listing["seller_id"] == uid:
        return await message.reply_text("❌ You can't buy your own listing!")

    price = listing["price"]
    bal = await get_balance(uid)
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
        return await message.reply_text("❌ Listing already sold (race condition).")

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
    log.info(f"BUY: {uid} bought listing {lid} for {price} kakera")


@app.on_message(filters.command("market"))
async def cmd_market(_, message: Message):
    args = message.command
    rarity_filter = args[1].lower() if len(args) > 1 else None
    listings = await get_active_listings(rarity=rarity_filter, limit=10)

    if not listings:
        msg = "No active listings"
        if rarity_filter:
            msg += f" for **{rarity_filter}**"
        return await message.reply_text(f"🛒 {msg}.")

    header = "🛒 **Market Listings**"
    if rarity_filter:
        header += f" — {rarity_filter}"
    lines = [header + "\n"]
    for listing in listings:
        tier = get_rarity(listing.get("rarity", ""))
        emoji = tier.emoji if tier else "❓"
        lines.append(
            f"{emoji} **{listing['name']}** `{listing['listing_id']}`\n"
            f"  _{listing.get('anime','?')}_ | 💰 `{_fmt(listing['price'])}` kakera"
        )
    lines.append("\nBuy with: `/buy <listing_id>`")
    await message.reply_text("\n".join(lines))
