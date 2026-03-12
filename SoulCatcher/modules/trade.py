"""SoulCatcher/modules/trade.py
Command: /trade
Callbacks: trade:
"""

from __future__ import annotations
import uuid
import logging
from datetime import datetime
from html import escape as he

from pyrogram import filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup as IKM, InlineKeyboardButton as IKB

from .. import app
from ..rarity import get_rarity, can_trade
from ..database import (
    get_harem_char, transfer_harem_char,
    deduct_balance,
    create_trade, get_trade, update_trade,
    _col,
)

log = logging.getLogger("SoulCatcher.trade")

PM = enums.ParseMode.HTML   # use HTML everywhere — no markdown entity issues


def _fmt(n) -> str:
    try:
        return f"{int(n):,}"
    except Exception:
        return str(n)


def _mention(name: str, uid: int) -> str:
    return f'<a href="tg://user?id={uid}">{he(name)}</a>'


# ─────────────────────────────────────────────────────────────────────────────
# /trade
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("trade"))
async def cmd_trade(_, message: Message):
    if not message.reply_to_message:
        return await message.reply_text(
            "Reply to the user you want to trade with:\n"
            "<code>/trade &lt;your_iid&gt; &lt;their_iid&gt;</code>",
            parse_mode=PM,
        )

    args = message.command
    if len(args) < 3:
        return await message.reply_text(
            "Usage: <code>/trade &lt;your_iid&gt; &lt;their_iid&gt;</code>",
            parse_mode=PM,
        )

    proposer = message.from_user
    receiver = message.reply_to_message.from_user

    if receiver.is_bot or receiver.id == proposer.id:
        return await message.reply_text("❌ Can't trade with bots or yourself!", parse_mode=PM)

    my_iid    = args[1].upper()
    their_iid = args[2].upper()

    my_char    = await get_harem_char(proposer.id, my_iid)
    their_char = await get_harem_char(receiver.id, their_iid)

    if not my_char:
        return await message.reply_text(
            f"❌ <code>{he(my_iid)}</code> not found in your harem.",
            parse_mode=PM,
        )
    if not their_char:
        return await message.reply_text(
            f"❌ <code>{he(their_iid)}</code> not found in {he(receiver.first_name)}'s harem.",
            parse_mode=PM,
        )

    if not can_trade(my_char.get("rarity", "")):
        return await message.reply_text(
            f"❌ <b>{he(my_char['name'])}</b> cannot be traded!",
            parse_mode=PM,
        )
    if not can_trade(their_char.get("rarity", "")):
        return await message.reply_text(
            f"❌ <b>{he(their_char['name'])}</b> cannot be traded!",
            parse_mode=PM,
        )

    fee      = 500
    trade_id = str(uuid.uuid4())[:8].upper()

    await create_trade({
        "trade_id":      trade_id,
        "proposer_id":   proposer.id,
        "receiver_id":   receiver.id,
        "proposer_char": my_iid,
        "receiver_char": their_iid,
        "fee":           fee,
        "status":        "pending",
        "created_at":    datetime.utcnow(),
    })

    my_tier    = get_rarity(my_char.get("rarity", ""))
    their_tier = get_rarity(their_char.get("rarity", ""))

    kb = IKM([[
        IKB("✅ Accept",  callback_data=f"trade:accept:{trade_id}"),
        IKB("❌ Decline", callback_data=f"trade:decline:{trade_id}"),
    ]])

    await message.reply_text(
        f"🔄 <b>Trade Proposal</b> (<code>{trade_id}</code>)\n\n"
        f"<b>{he(proposer.first_name)}</b> offers:\n"
        f"  {my_tier.emoji if my_tier else '?'} <b>{he(my_char['name'])}</b> <code>{my_iid}</code>\n\n"
        f"For {_mention(receiver.first_name, receiver.id)}'s:\n"
        f"  {their_tier.emoji if their_tier else '?'} <b>{he(their_char['name'])}</b> <code>{their_iid}</code>\n\n"
        f"Fee: <code>{_fmt(fee)}</code> kakera each\n\n"
        f"{_mention(receiver.first_name, receiver.id)} — accept or decline?",
        reply_markup=kb,
        parse_mode=PM,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Callback
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^trade:"))
async def trade_cb(_, cb):
    await cb.answer()
    _, action, trade_id = cb.data.split(":")
    uid   = cb.from_user.id
    trade = await get_trade(trade_id)

    if not trade or trade["status"] != "pending":
        return await cb.message.edit_text("❌ Trade no longer active.", parse_mode=PM)

    if action == "decline":
        if uid not in (trade["proposer_id"], trade["receiver_id"]):
            return await cb.answer("Not your trade.", show_alert=True)
        await update_trade(trade_id, {"$set": {"status": "declined"}})
        return await cb.message.edit_text("❌ Trade declined.", parse_mode=PM)

    if action == "accept":
        if uid != trade["receiver_id"]:
            return await cb.answer("Only the receiver can accept.", show_alert=True)

        receiver_char = await get_harem_char(trade["receiver_id"], trade["receiver_char"])
        proposer_char = await get_harem_char(trade["proposer_id"], trade["proposer_char"])

        if not receiver_char or not proposer_char:
            return await cb.message.edit_text("❌ One or both characters were deleted.", parse_mode=PM)

        receiver_gets_rarity = get_rarity(proposer_char["rarity"])
        proposer_gets_rarity = get_rarity(receiver_char["rarity"])

        if receiver_gets_rarity and receiver_gets_rarity.max_per_user > 0:
            count = await _col("user_characters").count_documents({
                "user_id": trade["receiver_id"],
                "rarity":  proposer_char["rarity"],
            })
            if count >= receiver_gets_rarity.max_per_user:
                return await cb.answer(
                    f"❌ Receiver already has max {receiver_gets_rarity.max_per_user} "
                    f"{receiver_gets_rarity.display_name} characters!",
                    show_alert=True,
                )

        if proposer_gets_rarity and proposer_gets_rarity.max_per_user > 0:
            count = await _col("user_characters").count_documents({
                "user_id": trade["proposer_id"],
                "rarity":  receiver_char["rarity"],
            })
            if count >= proposer_gets_rarity.max_per_user:
                return await cb.answer(
                    f"❌ Proposer would exceed max {proposer_gets_rarity.max_per_user} "
                    f"{proposer_gets_rarity.display_name} characters!",
                    show_alert=True,
                )

        ok1 = await transfer_harem_char(trade["proposer_char"], trade["proposer_id"], trade["receiver_id"])
        ok2 = await transfer_harem_char(trade["receiver_char"], trade["receiver_id"], trade["proposer_id"])

        if ok1 and ok2:
            await deduct_balance(trade["proposer_id"], trade["fee"])
            await deduct_balance(trade["receiver_id"],  trade["fee"])
            await update_trade(trade_id, {"$set": {"status": "completed"}})
            log.info(
                "TRADE COMPLETED: %d <-> %d  (%s <-> %s)",
                trade["proposer_id"], trade["receiver_id"],
                trade["proposer_char"], trade["receiver_char"],
            )
            await cb.message.edit_text(
                f"✅ <b>Trade Complete!</b> Fee: <code>{_fmt(trade['fee'])}</code> kakera each.",
                parse_mode=PM,
            )
        else:
            await cb.message.edit_text("❌ Trade failed — characters may have moved.", parse_mode=PM)
            log.warning("TRADE FAILED: %s", trade_id)
