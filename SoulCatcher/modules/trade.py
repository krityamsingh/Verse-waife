"""SoulCatcher/modules/trade.py — /trade, /gift."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime

import SoulCatcher as _soul
from pyrogram import filters
from pyrogram.types import Message, InlineKeyboardMarkup as IKM, InlineKeyboardButton as IKB, CallbackQuery

from SoulCatcher.database import (
    get_harem_char,
    transfer_harem_char,
    create_trade,
    get_trade,
    update_trade,
    get_pending_trade,
    add_balance,
    update_user,
)
from SoulCatcher.rarity import can_trade, can_gift, rarity_display, ECONOMY

log = logging.getLogger("SoulCatcher.trade")


# ── /gift ─────────────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command("gift"))
async def gift_cmd(_, m: Message):
    if not m.reply_to_message:
        await m.reply("↩️ Reply to a user to gift them a character.")
        return

    parts = m.text.split()
    if len(parts) < 2:
        await m.reply("Usage: `/gift <instanceID>` (reply to recipient)")
        return

    instance_id = parts[1].upper()
    sender      = m.from_user
    target      = m.reply_to_message.from_user

    if target.id == sender.id:
        await m.reply("❌ You can't gift yourself!")
        return
    if target.is_bot:
        await m.reply("❌ You can't gift a bot.")
        return

    char = await get_harem_char(sender.id, instance_id)
    if not char:
        await m.reply("❌ Character not found in your harem.")
        return

    if not can_gift(char["rarity"]):
        r_str = rarity_display(char["rarity"])
        await m.reply(f"❌ **{r_str}** characters cannot be gifted.")
        return

    buttons = IKM([[
        IKB("✅ Confirm Gift", callback_data=f"gift_do:{sender.id}:{target.id}:{instance_id}"),
        IKB("❌ Cancel",       callback_data=f"gift_cancel:{sender.id}"),
    ]])

    r_str = rarity_display(char["rarity"])
    await m.reply(
        f"🎁 Gift **{char['name']}** ({r_str}) to **{target.first_name}**?",
        reply_markup=buttons,
    )


@_soul.app.on_callback_query(filters.regex(r"^gift_do:(\d+):(\d+):(\w+)$"))
async def gift_do_cb(_, cq: CallbackQuery):
    _, from_uid, to_uid, instance_id = cq.data.split(":")
    from_uid, to_uid = int(from_uid), int(to_uid)

    if cq.from_user.id != from_uid:
        await cq.answer("Not your gift!", show_alert=True)
        return

    char = await get_harem_char(from_uid, instance_id)
    if not char:
        await cq.answer("Character not found!", show_alert=True)
        return

    success = await transfer_harem_char(instance_id, from_uid, to_uid)
    if success:
        await update_user(from_uid, {"$inc": {"total_gifted": 1}})
        await cq.message.edit_text(
            f"🎁 **{char['name']}** gifted successfully!\n"
            f"✅ Transferred to user `{to_uid}`."
        )
    else:
        await cq.answer("Transfer failed. Try again.", show_alert=True)


@_soul.app.on_callback_query(filters.regex(r"^gift_cancel:(\d+)$"))
async def gift_cancel_cb(_, cq: CallbackQuery):
    if cq.from_user.id != int(cq.data.split(":")[1]):
        await cq.answer("Not yours!", show_alert=True)
        return
    await cq.message.edit_text("❌ Gift cancelled.")


# ── /trade ────────────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command("trade"))
async def trade_cmd(_, m: Message):
    if not m.reply_to_message:
        await m.reply("↩️ Reply to a user to propose a trade.")
        return

    parts = m.text.split()
    if len(parts) < 3:
        await m.reply("Usage: `/trade <yourID> <theirID>` (reply to trade partner)")
        return

    my_instance    = parts[1].upper()
    their_instance = parts[2].upper()
    sender         = m.from_user
    target         = m.reply_to_message.from_user

    if target.id == sender.id:
        await m.reply("❌ You can't trade with yourself!")
        return
    if target.is_bot:
        await m.reply("❌ You can't trade with a bot.")
        return

    # Validate both characters
    my_char    = await get_harem_char(sender.id, my_instance)
    their_char = await get_harem_char(target.id, their_instance)

    if not my_char:
        await m.reply(f"❌ `{my_instance}` not found in your harem.")
        return
    if not their_char:
        await m.reply(f"❌ `{their_instance}` not found in {target.first_name}'s harem.")
        return
    if not can_trade(my_char["rarity"]):
        await m.reply(f"❌ **{rarity_display(my_char['rarity'])}** characters cannot be traded.")
        return
    if not can_trade(their_char["rarity"]):
        await m.reply(f"❌ **{rarity_display(their_char['rarity'])}** characters cannot be traded.")
        return

    # Check for pending trade
    pending = await get_pending_trade(sender.id)
    if pending:
        await m.reply("❌ You already have a pending trade. Complete or cancel it first.")
        return

    trade_id = str(uuid.uuid4())[:8].upper()
    await create_trade({
        "trade_id":         trade_id,
        "from_uid":         sender.id,
        "to_uid":           target.id,
        "from_instance":    my_instance,
        "to_instance":      their_instance,
        "from_char_name":   my_char["name"],
        "to_char_name":     their_char["name"],
        "status":           "pending",
        "created_at":       datetime.utcnow(),
    })

    my_r    = rarity_display(my_char["rarity"])
    their_r = rarity_display(their_char["rarity"])

    buttons = IKM([[
        IKB("✅ Accept",  callback_data=f"trade_accept:{trade_id}:{target.id}"),
        IKB("❌ Decline", callback_data=f"trade_decline:{trade_id}:{target.id}"),
    ]])

    await m.reply(
        f"🔄 **Trade Proposal** `{trade_id}`\n\n"
        f"**{sender.first_name}** offers:\n"
        f"  {my_r} **{my_char['name']}** (`{my_instance}`)\n\n"
        f"**{target.first_name}** gives:\n"
        f"  {their_r} **{their_char['name']}** (`{their_instance}`)\n\n"
        f"👆 **{target.first_name}**, accept or decline?",
        reply_markup=buttons,
    )


@_soul.app.on_callback_query(filters.regex(r"^trade_accept:(\w+):(\d+)$"))
async def trade_accept_cb(_, cq: CallbackQuery):
    _, trade_id, to_uid = cq.data.split(":")
    to_uid = int(to_uid)

    if cq.from_user.id != to_uid:
        await cq.answer("This trade isn't for you!", show_alert=True)
        return

    trade = await get_trade(trade_id)
    if not trade or trade["status"] != "pending":
        await cq.answer("Trade no longer active.", show_alert=True)
        return

    # Execute swap
    ok1 = await transfer_harem_char(trade["from_instance"], trade["from_uid"], trade["to_uid"])
    ok2 = await transfer_harem_char(trade["to_instance"], trade["to_uid"], trade["from_uid"])

    if ok1 and ok2:
        await update_trade(trade_id, {"$set": {"status": "completed", "completed_at": datetime.utcnow()}})
        for uid in (trade["from_uid"], trade["to_uid"]):
            await update_user(uid, {"$inc": {"total_traded": 1}})
        await cq.message.edit_text(
            f"✅ **Trade `{trade_id}` completed!**\n\n"
            f"**{trade['from_char_name']}** ↔️ **{trade['to_char_name']}**\n"
            "Characters have been swapped!"
        )
    else:
        await update_trade(trade_id, {"$set": {"status": "failed"}})
        await cq.message.edit_text("❌ Trade failed. One or both characters may have moved.")


@_soul.app.on_callback_query(filters.regex(r"^trade_decline:(\w+):(\d+)$"))
async def trade_decline_cb(_, cq: CallbackQuery):
    _, trade_id, to_uid = cq.data.split(":")
    to_uid = int(to_uid)

    if cq.from_user.id != to_uid:
        await cq.answer("Not your trade!", show_alert=True)
        return

    trade = await get_trade(trade_id)
    if not trade or trade["status"] != "pending":
        await cq.answer("Trade no longer active.", show_alert=True)
        return

    await update_trade(trade_id, {"$set": {"status": "declined"}})
    await cq.message.edit_text(f"❌ Trade `{trade_id}` was declined.")
