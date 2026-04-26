"""SoulCatcher/modules/marriage.py — /propose, /marry, /divorce, /couple."""
from __future__ import annotations

import logging
import random
from datetime import datetime

import SoulCatcher as _soul
from pyrogram import filters
from pyrogram.types import Message, InlineKeyboardMarkup as IKM, InlineKeyboardButton as IKB, CallbackQuery

from SoulCatcher.database import (
    get_or_create_user,
    get_marriage,
    create_marriage,
    end_marriage,
    get_balance,
    deduct_balance,
    add_balance,
    update_user,
)
from SoulCatcher.rarity import ECONOMY

log = logging.getLogger("SoulCatcher.marriage")

# In-memory proposal sessions: proposer_id → {target_id, attempts}
_proposals: dict[int, dict] = {}


# ── /propose ──────────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command("propose"))
async def propose_cmd(_, m: Message):
    if not m.reply_to_message:
        await m.reply("↩️ Reply to someone to propose marriage!")
        return

    proposer = m.from_user
    target   = m.reply_to_message.from_user

    if target.id == proposer.id:
        await m.reply("❌ You can't propose to yourself!")
        return
    if target.is_bot:
        await m.reply("❌ You can't marry a bot.")
        return

    # Already married?
    if await get_marriage(proposer.id):
        await m.reply("💍 You're already married! Use `/divorce` first.")
        return
    if await get_marriage(target.id):
        await m.reply(f"💍 **{target.first_name}** is already married!")
        return

    session = _proposals.get(proposer.id, {"target_id": None, "attempts": 0})
    if session.get("target_id") == target.id:
        session["attempts"] += 1
    else:
        session = {"target_id": target.id, "attempts": 1}
    _proposals[proposer.id] = session

    attempts  = session["attempts"]
    guarantee = ECONOMY["propose_guarantee"]

    # Guaranteed after N attempts
    if attempts >= guarantee:
        success = True
        note    = "💫 *Your persistence paid off!*"
    else:
        success = random.random() < ECONOMY["propose_success_chance"]
        note    = ""

    if success:
        buttons = IKM([[
            IKB("💍 Accept",  callback_data=f"marry_accept:{proposer.id}:{target.id}"),
            IKB("💔 Decline", callback_data=f"marry_decline:{proposer.id}:{target.id}"),
        ]])
        await m.reply(
            f"💌 **{proposer.first_name}** is proposing to **{target.first_name}**!\n\n"
            f"💍 *Will you marry them?*\n{note}",
            reply_markup=buttons,
        )
    else:
        remaining = guarantee - attempts
        await m.reply(
            f"💔 **{target.first_name}** wasn't ready yet...\n"
            f"Try again! ({remaining} more attempt(s) guaranteed.)"
        )


@_soul.app.on_callback_query(filters.regex(r"^marry_accept:(\d+):(\d+)$"))
async def marry_accept_cb(_, cq: CallbackQuery):
    _, proposer_id, target_id = cq.data.split(":")
    proposer_id, target_id = int(proposer_id), int(target_id)

    if cq.from_user.id != target_id:
        await cq.answer("This proposal isn't for you!", show_alert=True)
        return

    # Double-check still single
    if await get_marriage(proposer_id):
        await cq.message.edit_text("💍 The proposer is already married now.")
        return
    if await get_marriage(target_id):
        await cq.message.edit_text("💍 You're already married!")
        return

    await create_marriage(proposer_id, target_id)
    _proposals.pop(proposer_id, None)

    await cq.message.edit_text(
        f"💒 **You're married!** 🎉\n\n"
        f"[User {proposer_id}](tg://user?id={proposer_id}) 💍 "
        f"[User {target_id}](tg://user?id={target_id})\n\n"
        "Congratulations to the happy couple! 🥂"
    )


@_soul.app.on_callback_query(filters.regex(r"^marry_decline:(\d+):(\d+)$"))
async def marry_decline_cb(_, cq: CallbackQuery):
    _, proposer_id, target_id = cq.data.split(":")
    if cq.from_user.id != int(target_id):
        await cq.answer("Not for you!", show_alert=True)
        return

    _proposals.pop(int(proposer_id), None)
    await cq.message.edit_text("💔 The proposal was declined.")


# ── /marry (accept by command) ────────────────────────────────────────────────

@_soul.app.on_message(filters.command("marry"))
async def marry_cmd(_, m: Message):
    if not m.reply_to_message:
        await m.reply("↩️ Reply to your proposer to accept marriage.")
        return

    target   = m.from_user
    proposer = m.reply_to_message.from_user

    # Check if there's a pending proposal from that user to this user
    session = _proposals.get(proposer.id)
    if not session or session.get("target_id") != target.id:
        await m.reply("❌ No pending proposal from that user.")
        return

    if await get_marriage(target.id) or await get_marriage(proposer.id):
        await m.reply("💍 One of you is already married!")
        return

    await create_marriage(proposer.id, target.id)
    _proposals.pop(proposer.id, None)

    await m.reply(
        f"💒 **Married!** 🎉\n\n"
        f"[{proposer.first_name}](tg://user?id={proposer.id}) 💍 "
        f"[{target.first_name}](tg://user?id={target.id})"
    )


# ── /divorce ──────────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command("divorce"))
async def divorce_cmd(_, m: Message):
    uid     = m.from_user.id
    marriage = await get_marriage(uid)

    if not marriage:
        await m.reply("💔 You're not married.")
        return

    buttons = IKM([[
        IKB("💔 Confirm Divorce", callback_data=f"divorce_confirm:{uid}"),
        IKB("❌ Cancel",          callback_data=f"divorce_cancel:{uid}"),
    ]])
    await m.reply("💔 Are you sure you want to divorce?", reply_markup=buttons)


@_soul.app.on_callback_query(filters.regex(r"^divorce_confirm:(\d+)$"))
async def divorce_confirm_cb(_, cq: CallbackQuery):
    uid = int(cq.data.split(":")[1])
    if cq.from_user.id != uid:
        await cq.answer("Not yours!", show_alert=True)
        return

    ended = await end_marriage(uid)
    if ended:
        await cq.message.edit_text("💔 You are now divorced. Take care.")
    else:
        await cq.answer("No active marriage found.", show_alert=True)


@_soul.app.on_callback_query(filters.regex(r"^divorce_cancel:(\d+)$"))
async def divorce_cancel_cb(_, cq: CallbackQuery):
    if cq.from_user.id != int(cq.data.split(":")[1]):
        await cq.answer("Not yours!", show_alert=True)
        return
    await cq.message.edit_text("✅ Divorce cancelled. 💕")


# ── /couple ───────────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command(["couple", "marriage", "partner"]))
async def couple_cmd(_, m: Message):
    target   = m.reply_to_message.from_user if m.reply_to_message else m.from_user
    marriage = await get_marriage(target.id)

    if not marriage:
        await m.reply(f"💔 **{target.first_name}** is not married.")
        return

    partner_id = marriage["user2"] if marriage["user1"] == target.id else marriage["user1"]
    married_at = marriage.get("married_at", "?")
    if hasattr(married_at, "strftime"):
        married_at = married_at.strftime("%Y-%m-%d")

    await m.reply(
        f"💒 **{target.first_name}'s Marriage**\n\n"
        f"💍 Partner: [User {partner_id}](tg://user?id={partner_id})\n"
        f"📅 Married since: `{married_at}`"
    )
