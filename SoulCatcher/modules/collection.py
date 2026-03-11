"""SoulCatcher/modules/collection.py — /harem /view /burn /setfav /sort /wish /wishlist /trade /gift /market /sell /buy"""
import uuid, math, random
from datetime import datetime
from pyrogram import filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from .. import app
from ..database import (
    get_or_create_user, get_user, get_harem, get_harem_char,
    remove_from_harem, transfer_harem_char, add_to_harem,
    add_balance, deduct_balance, get_balance, update_user,
    get_active_listings, create_listing, get_listing,
    update_listing, atomic_buy_listing,
    create_trade, get_trade, update_trade,
    get_harem_rarity_counts, add_wish, remove_wish, get_wishlist,
)
from ..rarity import rarity_display, get_rarity, get_sell_price, can_trade, can_gift, ECONOMY

HAREM_PER_PAGE = 8
_ABUSE = ["🎁 This isn't yours!","❌ Only the sender can do that.","🚫 Hands off!"]


# ── HAREM ─────────────────────────────────────────────────────────────────────

@app.on_message(filters.command(["harem","collection"]))
async def cmd_harem(_, message: Message):
    user = message.from_user
    await get_or_create_user(user.id, user.username or "", user.first_name or "")
    doc = await get_user(user.id) or {}
    await _harem_page(message, user.id, user.first_name, 1, doc.get("harem_sort","rarity"))

async def _harem_page(ctx, uid, name, page, sort_by, edit=False):
    chars, total = await get_harem(uid, page=page, per_page=HAREM_PER_PAGE, sort_by=sort_by)
    pages  = max(1, math.ceil(total/HAREM_PER_PAGE))
    kakera = await get_balance(uid)
    rc     = await get_harem_rarity_counts(uid)
    if total == 0:
        text = f"🌸 **{name}'s Collection** — _Empty! Claim some characters!_"
    else:
        summary = "  ".join(f"{get_rarity(r).emoji}`{c}`" for r,c in rc.items() if get_rarity(r))
        text = (f"🌸 **{name}'s Collection** ({total})\n💰 `{kakera:,}` kakera   {summary}\n\n")
        for i, c in enumerate(chars, (page-1)*HAREM_PER_PAGE+1):
            fav   = "⭐" if c.get("is_favorite") else ""
            text += f"`{i}.` {fav}{rarity_display(c['rarity'])} **{c['name']}** — _{c.get('anime','?')}_\n   🆔`{c['instance_id']}`\n"
        text += f"\nPage **{page}/{pages}** · Sort: `{sort_by}`"
    nav = []
    if page>1:     nav.append(InlineKeyboardButton("◀️", callback_data=f"hp:{uid}:{page-1}:{sort_by}"))
    if page<pages: nav.append(InlineKeyboardButton("▶️", callback_data=f"hp:{uid}:{page+1}:{sort_by}"))
    kb = InlineKeyboardMarkup([nav]) if nav else None
    if edit:
        try: await ctx.message.edit_text(text, reply_markup=kb)
        except Exception: pass
    else: await ctx.reply_text(text, reply_markup=kb)

@app.on_callback_query(filters.regex(r"^hp:"))
async def hpage_cb(_, cb):
    await cb.answer()
    _, uid, pg, sb = cb.data.split(":")
    doc  = await get_user(int(uid)) or {}
    await _harem_page(cb, int(uid), doc.get("first_name","User"), int(pg), sb, edit=True)

@app.on_message(filters.command("view"))
async def cmd_view(_, message: Message):
    args = message.command
    if len(args) < 2: return await message.reply_text("Usage: `/view <ID>`")
    char = await get_harem_char(message.from_user.id, args[1].upper())
    if not char: return await message.reply_text("❌ Not in your collection.")
    tier  = get_rarity(char["rarity"])
    price = get_sell_price(char["rarity"])
    text = (
        f"{'⭐ ' if char.get('is_favorite') else ''}**{char['name']}**\n"
        f"📖 _{char.get('anime','?')}_\n{tier.emoji if tier else '?'} **{tier.display_name if tier else char['rarity']}**\n"
        f"🆔 `{char['instance_id']}`\n📅 {char['obtained_at'].strftime('%Y-%m-%d')}\n"
        f"💰 ~{price:,} kakera | Trade:`{tier.trade_allowed if tier else '?'}` Gift:`{tier.gift_allowed if tier else '?'}`"
    )
    if char.get("video_url"):   await message.reply_video(char["video_url"], caption=text)
    elif char.get("img_url"):   await message.reply_photo(char["img_url"],   caption=text)
    else:                       await message.reply_text(text)

@app.on_message(filters.command("setfav"))
async def cmd_setfav(_, message: Message):
    args = message.command
    if len(args)<2: return await message.reply_text("Usage: `/setfav <ID>`")
    iid  = args[1].upper()
    char = await get_harem_char(message.from_user.id, iid)
    if not char: return await message.reply_text("❌ Not in your collection.")
    from ..database import _col
    await _col("user_characters").update_one({"instance_id":iid},{"$set":{"is_favorite":True}})
    await message.reply_text(f"⭐ **{char['name']}** is now your favourite!")

@app.on_message(filters.command("burn"))
async def cmd_burn(_, message: Message):
    args = message.command
    if len(args)<2: return await message.reply_text("Usage: `/burn <ID>`")
    iid  = args[1].upper()
    char = await get_harem_char(message.from_user.id, iid)
    if not char: return await message.reply_text("❌ Not in your collection.")
    price = get_sell_price(char["rarity"])
    await remove_from_harem(message.from_user.id, iid)
    await add_balance(message.from_user.id, price)
    tier  = get_rarity(char["rarity"])
    await message.reply_text(f"🔥 Burned {tier.emoji if tier else ''} **{char['name']}** → +**{price:,} kakera**!")

@app.on_message(filters.command("sort"))
async def cmd_sort(_, message: Message):
    valid = ["rarity","name","anime","recent"]
    args  = message.command
    if len(args)<2 or args[1].lower() not in valid:
        return await message.reply_text(f"Usage: `/sort <{'|'.join(valid)}>`")
    await update_user(message.from_user.id, {"$set":{"harem_sort":args[1].lower()}})
    await message.reply_text(f"✅ Sorted by **{args[1].lower()}**.")


# ── WISHLIST ──────────────────────────────────────────────────────────────────

@app.on_message(filters.command("wish"))
async def cmd_wish(_, message: Message):
    args = message.command
    if len(args)<2: return await message.reply_text("Usage: `/wish <CHAR_ID>`")
    from ..database import get_character
    char = await get_character(args[1])
    if not char: return await message.reply_text("❌ Character not found.")
    ok = await add_wish(message.from_user.id, args[1], char["name"], char["rarity"])
    await message.reply_text(f"💛 Added **{char['name']}** to wishlist!" if ok else "❌ Already on list or full (max 25).")

@app.on_message(filters.command("unwish"))
async def cmd_unwish(_, message: Message):
    args = message.command
    if len(args)<2: return await message.reply_text("Usage: `/unwish <CHAR_ID>`")
    ok = await remove_wish(message.from_user.id, args[1])
    await message.reply_text("✅ Removed." if ok else "❌ Not on wishlist.")

@app.on_message(filters.command("wishlist"))
async def cmd_wishlist(_, message: Message):
    items = await get_wishlist(message.from_user.id)
    if not items: return await message.reply_text("💛 Wishlist empty. Use `/wish <id>`.")
    text = f"💛 **Your Wishlist** ({len(items)}/25)\n\n"
    for w in items:
        text += f"• **{w['char_name']}** ({rarity_display(w['rarity'])}) `{w['char_id']}`\n"
    await message.reply_text(text)


# ── TRADE ─────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("trade"))
async def cmd_trade(_, message: Message):
    if not message.reply_to_message:
        return await message.reply_text("Reply to the target user, then `/trade <yourID> <theirID>`")
    args = message.command
    if len(args)<3: return await message.reply_text("Usage: `/trade <YOUR_ID> <THEIR_ID>`")
    sender = message.from_user; target = message.reply_to_message.from_user
    if sender.id==target.id or target.is_bot: return await message.reply_text("❌ Invalid target.")
    my_id, their_id = args[1].upper(), args[2].upper()
    my_char    = await get_harem_char(sender.id, my_id)
    their_char = await get_harem_char(target.id, their_id)
    if not my_char:    return await message.reply_text(f"❌ You don't own `{my_id}`.")
    if not their_char: return await message.reply_text(f"❌ {target.first_name} doesn't own `{their_id}`.")
    if not can_trade(my_char["rarity"]):    return await message.reply_text(f"❌ **{my_char['name']}** can't be traded.")
    if not can_trade(their_char["rarity"]): return await message.reply_text(f"❌ **{their_char['name']}** can't be traded.")
    fee      = max(10, int(ECONOMY["trade_fee_pct"]/100*get_sell_price(their_char["rarity"])))
    trade_id = str(uuid.uuid4())[:8].upper()
    await create_trade({"trade_id":trade_id,"proposer_id":sender.id,"proposer_char":my_id,
        "proposer_name":my_char["name"],"receiver_id":target.id,"receiver_char":their_id,
        "receiver_name":their_char["name"],"status":"pending","fee":fee,"created_at":datetime.utcnow()})
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Accept",  callback_data=f"trade:accept:{trade_id}"),
        InlineKeyboardButton("❌ Decline", callback_data=f"trade:decline:{trade_id}"),
    ]])
    await message.reply_text(
        f"🔄 **Trade Proposal** `{trade_id}`\n\n"
        f"**{sender.first_name}** offers: {rarity_display(my_char['rarity'])} **{my_char['name']}**\n"
        f"**{target.first_name}** gives: {rarity_display(their_char['rarity'])} **{their_char['name']}**\n\n"
        f"💸 Fee: **{fee}** kakera each",
        reply_markup=kb)

@app.on_callback_query(filters.regex(r"^trade:"))
async def trade_cb(_, cb):
    await cb.answer()
    _, action, trade_id = cb.data.split(":")
    uid   = cb.from_user.id
    trade = await get_trade(trade_id)
    if not trade or trade["status"]!="pending":
        return await cb.message.edit_text("❌ Trade no longer active.")
    if action=="decline":
        if uid not in (trade["proposer_id"],trade["receiver_id"]):
            return await cb.answer("Not your trade.", show_alert=True)
        await update_trade(trade_id,{"$set":{"status":"declined"}})
        return await cb.message.edit_text("❌ Trade declined.")
    if action=="accept":
        if uid!=trade["receiver_id"]: return await cb.answer("Only the receiver can accept.", show_alert=True)
        ok1 = await transfer_harem_char(trade["proposer_char"],trade["proposer_id"],trade["receiver_id"])
        ok2 = await transfer_harem_char(trade["receiver_char"],trade["receiver_id"],trade["proposer_id"])
        if ok1 and ok2:
            await deduct_balance(trade["proposer_id"],trade["fee"])
            await deduct_balance(trade["receiver_id"], trade["fee"])
            await update_trade(trade_id,{"$set":{"status":"completed"}})
            await cb.message.edit_text(f"✅ **Trade Complete!** Fee: {trade['fee']} kakera each.")
        else:
            await cb.message.edit_text("❌ Trade failed — characters may have moved.")


# ── GIFT (from sgift.py) ──────────────────────────────────────────────────────

@app.on_message(filters.command("gift"))
async def cmd_gift(_, message: Message):
    if not message.reply_to_message:
        return await message.reply_text("Reply to target then `/gift <ID>`")
    args   = message.command
    if len(args)<2: return await message.reply_text("Usage: `/gift <ID>`")
    sender = message.from_user; target = message.reply_to_message.from_user
    if sender.id==target.id or target.is_bot: return await message.reply_text("❌ Invalid target.")
    iid  = args[1].upper()
    char = await get_harem_char(sender.id, iid)
    if not char:          return await message.reply_text(f"❌ You don't own `{iid}`.")
    if not can_gift(char["rarity"]): return await message.reply_text(f"❌ **{char['name']}** can't be gifted.")
    await get_or_create_user(target.id, target.username or "", target.first_name or "")
    from ..database import _col
    await _col("user_characters").update_one({"instance_id":iid},{"$set":{"locked":True,"gift_temp_lock":True}})
    caption = (
        f"🎁 **Gift Confirmation**\n\n{sender.mention} → {target.mention}\n"
        f"• **{char['name']}**\n• _{char.get('anime','?')}_\n• {rarity_display(char['rarity'])}\n• `{iid}`\n\nConfirm?"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm", callback_data=f"gift:confirm:{sender.id}:{target.id}:{iid}")],
        [InlineKeyboardButton("❌ Cancel",  callback_data=f"gift:cancel:{sender.id}:{iid}")],
    ])
    if char.get("video_url"):   await message.reply_video(char["video_url"], caption=caption, reply_markup=kb)
    elif char.get("img_url"):   await message.reply_photo(char["img_url"],   caption=caption, reply_markup=kb)
    else:                       await message.reply_text(caption, reply_markup=kb)

@app.on_callback_query(filters.regex(r"^gift:"))
async def gift_cb(client, cb):
    parts = cb.data.split(":"); action = parts[1]; sender_id = int(parts[2])
    if cb.from_user.id != sender_id:
        return await cb.answer(random.choice(_ABUSE), show_alert=True)
    if action=="cancel":
        iid = parts[3]
        from ..database import _col
        await _col("user_characters").update_one({"instance_id":iid},{"$unset":{"locked":"","gift_temp_lock":""}})
        return await cb.message.edit_text("❌ Gift cancelled.")
    if action=="confirm":
        target_id, iid = int(parts[3]), parts[4]
        from ..database import _col
        lock = await _col("user_characters").find_one({"instance_id":iid,"user_id":sender_id,"gift_temp_lock":True})
        if not lock: return await cb.answer("⚠️ Already processed.", show_alert=True)
        await _col("user_characters").update_one({"instance_id":iid},{"$unset":{"gift_temp_lock":""}})
        ok = await transfer_harem_char(iid, sender_id, target_id)
        if ok:
            sender   = await client.get_users(sender_id)
            receiver = await client.get_users(target_id)
            char     = await get_harem_char(target_id, iid)
            await cb.message.edit_text(f"✅ **Gift sent!**\n{sender.mention} → {receiver.mention}: **{char['name'] if char else iid}** 🎁")
            if char:
                notify = f"🎁 **Gift received from {sender.mention}!**\n\n**{char['name']}** ({rarity_display(char['rarity'])})"
                try:
                    if char.get("video_url"):  await client.send_video(target_id, char["video_url"], caption=notify)
                    elif char.get("img_url"):  await client.send_photo(target_id, char["img_url"],   caption=notify)
                    else:                      await client.send_message(target_id, notify)
                except Exception: pass
        else:
            await cb.message.edit_text("❌ Transfer failed.")


# ── MARKET ────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("market"))
async def cmd_market(_, message: Message):
    args  = message.command; rarity = args[1].lower() if len(args)>1 else None
    ls    = await get_active_listings(rarity=rarity, limit=8)
    if not ls: return await message.reply_text("🏪 Market is empty.")
    text  = "🏪 **Market**\n\n"
    kb    = []
    for l in ls:
        text += f"🔖 `{l['listing_id']}` {rarity_display(l['rarity'])} **{l['char_name']}** — **{l['price']:,}** 🪙\n"
        kb.append([InlineKeyboardButton(f"Buy {l['char_name']} ({l['price']:,})", callback_data=f"mkt:{l['listing_id']}")])
    await message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))

@app.on_message(filters.command("sell"))
async def cmd_sell(_, message: Message):
    args = message.command
    if len(args)<3: return await message.reply_text("Usage: `/sell <ID> <PRICE>`")
    iid = args[1].upper()
    try: price=int(args[2]); assert price>0
    except Exception: return await message.reply_text("❌ Invalid price.")
    uid  = message.from_user.id
    char = await get_harem_char(uid, iid)
    if not char: return await message.reply_text("❌ Not in your collection.")
    tier = get_rarity(char["rarity"])
    if not tier or not tier.trade_allowed: return await message.reply_text(f"❌ **{char['name']}** can't be listed.")
    fee = ECONOMY["market_listing_fee"]
    if not await deduct_balance(uid, fee): return await message.reply_text(f"❌ Need `{fee}` kakera for listing fee.")
    lid = str(uuid.uuid4())[:8].upper()
    await remove_from_harem(uid, iid)
    await create_listing({"listing_id":lid,"seller_id":uid,"char_id":char["char_id"],
        "char_name":char["name"],"instance_id":iid,"anime":char.get("anime",""),
        "rarity":char["rarity"],"img_url":char.get("img_url",""),"video_url":char.get("video_url",""),
        "price":price,"status":"active","listed_at":datetime.utcnow(),"char_data":char})
    await message.reply_text(f"🏪 Listed {rarity_display(char['rarity'])} **{char['name']}** for **{price:,}** 🪙\nID: `{lid}`")

@app.on_message(filters.command("buy"))
async def cmd_buy(_, message: Message):
    args = message.command
    if len(args)<2: return await message.reply_text("Usage: `/buy <LISTING_ID>`")
    await _do_buy(message, message.from_user.id, args[1].upper())

@app.on_callback_query(filters.regex(r"^mkt:"))
async def mkt_cb(_, cb):
    await cb.answer()
    await _do_buy(cb, cb.from_user.id, cb.data.split(":")[1])

async def _do_buy(ctx, uid, lid):
    async def reply(t):
        if hasattr(ctx,"message"): await ctx.message.reply_text(t)
        else: await ctx.reply_text(t)
    l = await get_listing(lid)
    if not l or l["status"]!="active": return await reply("❌ Not found or sold.")
    if l["seller_id"]==uid: return await reply("❌ Can't buy your own listing.")
    if await get_balance(uid)<l["price"]: return await reply(f"❌ Need `{l['price']:,}` kakera.")
    result = await atomic_buy_listing(lid, uid)
    if not result: return await reply("❌ Already bought by someone else!")
    await deduct_balance(uid, l["price"])
    await add_balance(l["seller_id"], l["price"])
    iid = await add_to_harem(uid, l["char_data"])
    await reply(f"✅ Bought {rarity_display(l['rarity'])} **{l['char_name']}** for **{l['price']:,}** 🪙! ID:`{iid}`")
