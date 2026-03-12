"""SoulCatcher/modules/pay.py
Commands: /pay  /cheque  /cashcheque
Callbacks: cash_<id>  void_<id>

Ported from reference pay.py and adapted to use this bot's database layer
(add_balance / deduct_balance / get_balance) instead of the Grabber add/deduct/show helpers.
"""

from __future__ import annotations
import logging
import random
import time
from datetime import datetime, timedelta
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont
import textwrap

from pyrogram import filters
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup as IKM,
    InlineKeyboardButton as IKB,
    CallbackQuery,
)

from .. import app
from ..database import add_balance, deduct_balance, get_balance, get_or_create_user

log = logging.getLogger("SoulCatcher.pay")

# ── State ─────────────────────────────────────────────────────────────────────
_last_payment: dict[int, float] = {}     # user_id → epoch of last /pay
_cheques:      dict[str, dict]  = {}     # cheque_id → cheque data

PAY_COOLDOWN = 300   # 5 minutes between payments
PAY_MINIMUM  = 10    # minimum transfer


def _fmt(n) -> str:
    try:
        return f"{int(n):,}"
    except Exception:
        return str(n)


# ─────────────────────────────────────────────────────────────────────────────
# Cheque image generator
# ─────────────────────────────────────────────────────────────────────────────

async def _make_cheque_image(
    sender_name: str, recipient_name: str, amount: int, reason: str | None = None
) -> BytesIO:
    img = Image.new("RGB", (800, 400), color=(240, 240, 240))
    d   = ImageDraw.Draw(img)

    d.rectangle([20, 20, 780, 380],  outline=(0, 0, 0), width=2)
    d.rectangle([30, 30, 770, 100],  fill=(220, 220, 255), outline=(0, 0, 0))

    try:
        font_lg = ImageFont.truetype("arialbd.ttf", 36)
        font_md = ImageFont.truetype("arial.ttf",   24)
        font_sm = ImageFont.truetype("arial.ttf",   18)
    except Exception:
        font_lg = font_md = font_sm = ImageFont.load_default()

    d.text((400, 60),  "TOKEN CHEQUE",              fill=(0, 0, 0),     font=font_lg, anchor="mm")
    d.text((50,  150), f"Pay to: {recipient_name}", fill=(0, 0, 0),     font=font_md)
    d.text((50,  200), f"Amount: Ŧ{_fmt(amount)}",  fill=(0, 0, 0),     font=font_md)
    d.text((50,  250), f"From: {sender_name}",       fill=(0, 0, 0),     font=font_md)

    if reason:
        wrapped = textwrap.fill(reason, width=40)
        d.text((50, 300), f"Memo: {wrapped}", fill=(0, 0, 0), font=font_sm)

    d.line([500, 350, 750, 350], fill=(0, 0, 0), width=1)
    d.text((750, 340), "SENDER", fill=(100, 100, 100), font=font_sm, anchor="ra")

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


# ─────────────────────────────────────────────────────────────────────────────
# /pay
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("pay"))
async def cmd_pay(client, message: Message):
    sender = message.from_user

    if not message.reply_to_message:
        return await message.reply_text(
            "⚠️ ʏᴏᴜ ɴᴇᴇᴅ ᴛᴏ ʀᴇᴘʟʏ ᴛᴏ ᴛʜᴇ ᴘᴇʀsᴏɴ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ sᴇɴᴅ ᴛᴏᴋᴇɴs ᴛᴏ."
        )

    recipient = message.reply_to_message.from_user

    if sender.id == recipient.id:
        return await message.reply_text("❌ ʏᴏᴜ ᴄᴀɴ'ᴛ ᴘᴀʏ ʏᴏᴜʀsᴇʟғ!")

    if recipient.is_bot:
        return await message.reply_text("🤖 ʏᴏᴜ ᴄᴀɴ'ᴛ ᴘᴀʏ ᴀ ʙᴏᴛ!")

    # Parse amount and optional reason
    try:
        args   = message.text.split()
        amount = int(args[1])
        if amount <= 0:
            raise ValueError
        reason = " ".join(args[2:]) if len(args) > 2 else None
    except (IndexError, ValueError):
        return await message.reply_text(
            "**ᴜsᴀɢᴇ:** `/pay <ᴀᴍᴏᴜɴᴛ> [ʀᴇᴀsᴏɴ]`\n**ᴇxᴀᴍᴘʟᴇ:** `/pay 100 ᴛʜᴀɴᴋs!`"
        )

    if amount < PAY_MINIMUM:
        return await message.reply_text(
            f"**ᴍɪɴɪᴍᴜᴍ ᴛʀᴀɴsғᴇʀ ᴀᴍᴏᴜɴᴛ ɪs Ŧ{PAY_MINIMUM} ᴋᴀᴋᴇʀᴀ**"
        )

    bal = await get_balance(sender.id)
    if bal < amount:
        return await message.reply_text(
            f"**ɪɴsᴜғғɪᴄɪᴇɴᴛ ʙᴀʟᴀɴᴄᴇ!** ʏᴏᴜ ᴏɴʟʏ ʜᴀᴠᴇ **Ŧ{_fmt(bal)} ᴋᴀᴋᴇʀᴀ**."
        )

    # Anti-spam cooldown
    last = _last_payment.get(sender.id, 0)
    wait = PAY_COOLDOWN - (time.time() - last)
    if wait > 0:
        m, s = int(wait // 60), int(wait % 60)
        return await message.reply_text(
            f"⏳ ᴘʟᴇᴀsᴇ ᴡᴀɪᴛ **{m}ᴍ {s}s** ʙᴇғᴏʀᴇ sᴇɴᴅɪɴɢ ᴀɴᴏᴛʜᴇʀ ᴘᴀʏᴍᴇɴᴛ."
        )

    # Process
    await deduct_balance(sender.id, amount)
    await add_balance(recipient.id, amount)
    await get_or_create_user(recipient.id, recipient.username or "", recipient.first_name or "")
    _last_payment[sender.id] = time.time()

    txn_id    = f"TXN-{random.randint(100000, 999999)}"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    new_sender_bal    = await get_balance(sender.id)
    new_recipient_bal = await get_balance(recipient.id)

    msg = (
        f"💸 **ᴘᴀʏᴍᴇɴᴛ sᴜᴄᴄᴇssғᴜʟ** 💸\n\n"
        f"• **ᴀᴍᴏᴜɴᴛ:** Ŧ{_fmt(amount)}\n"
        f"• **ғʀᴏᴍ:** {sender.mention}\n"
        f"• **ᴛᴏ:** {recipient.mention}\n"
        f"• **ɪᴅ:** `{txn_id}`\n"
        f"• **ᴛɪᴍᴇ:** `{timestamp}`\n"
    )
    if reason:
        msg += f"• **ɴᴏᴛᴇ:** `{reason}`\n"
    msg += (
        f"\n**ʙᴀʟᴀɴᴄᴇs:**\n"
        f"{sender.mention}: **Ŧ{_fmt(new_sender_bal)}**\n"
        f"{recipient.mention}: **Ŧ{_fmt(new_recipient_bal)}**"
    )

    log.info("PAY: %d → %d  amount=%d  txn=%s", sender.id, recipient.id, amount, txn_id)
    await message.reply_text(msg, disable_web_page_preview=True)


# ─────────────────────────────────────────────────────────────────────────────
# /cheque
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("cheque"))
async def cmd_cheque(client, message: Message):
    sender = message.from_user

    if not message.reply_to_message:
        return await message.reply_text(
            "💳 **ᴛᴏ ᴄʀᴇᴀᴛᴇ ᴀ ᴄʜᴇǫᴜᴇ:**\n\n"
            "ʀᴇᴘʟʏ ᴛᴏ ʀᴇᴄɪᴘɪᴇɴᴛ ᴡɪᴛʜ:\n"
            "`/cheque <ᴀᴍᴏᴜɴᴛ> [ʀᴇᴀsᴏɴ]`\n\n"
            "**ᴇxᴀᴍᴘʟᴇ:** `/cheque 500 ʙɪʀᴛʜᴅᴀʏ ɢɪғᴛ`"
        )

    recipient = message.reply_to_message.from_user

    try:
        args   = message.text.split()
        amount = int(args[1])
        if amount <= 0:
            raise ValueError
        reason = " ".join(args[2:]) if len(args) > 2 else None
    except (IndexError, ValueError):
        return await message.reply_text(
            "**ɪɴᴠᴀʟɪᴅ ғᴏʀᴍᴀᴛ.** ᴜsᴇ: `/cheque <ᴀᴍᴏᴜɴᴛ> [ʀᴇᴀsᴏɴ]`"
        )

    bal = await get_balance(sender.id)
    if bal < amount:
        return await message.reply_text(
            f"**ʏᴏᴜ ɴᴇᴇᴅ Ŧ{_fmt(amount)} ᴋᴀᴋᴇʀᴀ ᴛᴏ ᴄʀᴇᴀᴛᴇ ᴛʜɪs ᴄʜᴇǫᴜᴇ.**"
        )

    cheque_id = f"CHQ-{random.randint(100000, 999999)}"
    expires   = datetime.now() + timedelta(days=7)
    _cheques[cheque_id] = {
        "sender_id":    sender.id,
        "recipient_id": recipient.id,
        "amount":       amount,
        "reason":       reason,
        "created_at":   datetime.now(),
        "expires_at":   expires,
    }

    cheque_img = await _make_cheque_image(sender.first_name, recipient.first_name, amount, reason)

    caption = (
        f"🏦 **ᴛᴏᴋᴇɴ ᴄʜᴇǫᴜᴇ** 🏦\n\n"
        f"• **ᴀᴍᴏᴜɴᴛ:** Ŧ{_fmt(amount)}\n"
        f"• **ᴛᴏ:** {recipient.first_name}\n"
        f"• **ғʀᴏᴍ:** {sender.first_name}\n"
        f"• **ɪᴅ:** `{cheque_id}`\n"
        f"• **ᴇxᴘɪʀᴇs:** {expires.strftime('%Y-%m-%d %H:%M')}\n\n"
        f"ᴜsᴇ `/cashcheque {cheque_id}` ᴛᴏ ᴄʟᴀɪᴍ"
    )
    keyboard = IKM([
        [IKB("💵 ᴄᴀsʜ ᴄʜᴇǫᴜᴇ", callback_data=f"cash_{cheque_id}")],
        [IKB("❌ ᴠᴏɪᴅ ᴄʜᴇǫᴜᴇ", callback_data=f"void_{cheque_id}")],
    ])

    await message.reply_photo(photo=cheque_img, caption=caption, reply_markup=keyboard)
    log.info("CHEQUE: %d → %d  amount=%d  id=%s", sender.id, recipient.id, amount, cheque_id)


# ─────────────────────────────────────────────────────────────────────────────
# /cashcheque
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("cashcheque"))
async def cmd_cashcheque(client, message: Message):
    try:
        cheque_id = message.command[1]
    except IndexError:
        return await message.reply_text("**ᴜsᴀɢᴇ:** `/cashcheque <ᴄʜᴇǫᴜᴇ_ɪᴅ>`")

    cheque = _cheques.get(cheque_id)
    if not cheque:
        return await message.reply_text("**ɪɴᴠᴀʟɪᴅ ᴏʀ ᴇxᴘɪʀᴇᴅ ᴄʜᴇǫᴜᴇ ɪᴅ**")

    if message.from_user.id != cheque["recipient_id"]:
        return await message.reply_text("**ᴛʜɪs ᴄʜᴇǫᴜᴇ ɪs ɴᴏᴛ ɪssᴜᴇᴅ ᴛᴏ ʏᴏᴜ**")

    if datetime.now() > cheque["expires_at"]:
        return await message.reply_text("**ᴛʜɪs ᴄʜᴇǫᴜᴇ ʜᴀs ᴇxᴘɪʀᴇᴅ**")

    sender_bal = await get_balance(cheque["sender_id"])
    if sender_bal < cheque["amount"]:
        return await message.reply_text("**sᴇɴᴅᴇʀ ʜᴀs ɪɴsᴜғғɪᴄɪᴇɴᴛ ғᴜɴᴅs**")

    await deduct_balance(cheque["sender_id"], cheque["amount"])
    await add_balance(cheque["recipient_id"], cheque["amount"])

    sender_user    = await client.get_users(cheque["sender_id"])
    recipient_user = await client.get_users(cheque["recipient_id"])
    new_s_bal      = await get_balance(cheque["sender_id"])
    new_r_bal      = await get_balance(cheque["recipient_id"])

    msg = (
        f"💵 **ᴄʜᴇǫᴜᴇ ᴄᴀsʜᴇᴅ** 💵\n\n"
        f"• **ᴀᴍᴏᴜɴᴛ:** Ŧ{_fmt(cheque['amount'])}\n"
        f"• **ғʀᴏᴍ:** {sender_user.mention}\n"
        f"• **ᴛᴏ:** {recipient_user.mention}\n"
        f"• **ɪᴅ:** `{cheque_id}`\n\n"
        f"**ɴᴇᴡ ʙᴀʟᴀɴᴄᴇs:**\n"
        f"{sender_user.mention}: **Ŧ{_fmt(new_s_bal)}**\n"
        f"{recipient_user.mention}: **Ŧ{_fmt(new_r_bal)}**"
    )
    await message.reply_text(msg)

    try:
        await client.send_message(
            cheque["sender_id"],
            f"📤 ʏᴏᴜʀ ᴄʜᴇǫᴜᴇ `{cheque_id}` ғᴏʀ **Ŧ{_fmt(cheque['amount'])} ᴋᴀᴋᴇʀᴀ** ʜᴀs ʙᴇᴇɴ ᴄᴀsʜᴇᴅ",
        )
    except Exception:
        pass

    del _cheques[cheque_id]
    log.info("CASHCHEQUE: id=%s  sender=%d  recipient=%d  amount=%d",
             cheque_id, cheque["sender_id"], cheque["recipient_id"], cheque["amount"])


# ─────────────────────────────────────────────────────────────────────────────
# Callbacks: cash  void
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^cash_"))
async def cash_cheque_cb(client, cb: CallbackQuery):
    cheque_id = cb.data[5:]   # strip "cash_"
    cheque    = _cheques.get(cheque_id)

    if not cheque:
        return await cb.answer("ᴄʜᴇǫᴜᴇ ɴᴏ ʟᴏɴɢᴇʀ ᴠᴀʟɪᴅ", show_alert=True)

    if cb.from_user.id != cheque["recipient_id"]:
        return await cb.answer("ᴛʜɪs ᴄʜᴇǫᴜᴇ ɪsɴ'ᴛ ʏᴏᴜʀs ᴛᴏ ᴄᴀsʜ", show_alert=True)

    if datetime.now() > cheque["expires_at"]:
        return await cb.answer("ᴄʜᴇǫᴜᴇ ʜᴀs ᴇxᴘɪʀᴇᴅ", show_alert=True)

    sender_bal = await get_balance(cheque["sender_id"])
    if sender_bal < cheque["amount"]:
        return await cb.answer("sᴇɴᴅᴇʀ ʜᴀs ɪɴsᴜғғɪᴄɪᴇɴᴛ ғᴜɴᴅs", show_alert=True)

    await deduct_balance(cheque["sender_id"], cheque["amount"])
    await add_balance(cheque["recipient_id"], cheque["amount"])

    sender_user    = await client.get_users(cheque["sender_id"])
    recipient_user = await client.get_users(cheque["recipient_id"])

    try:
        await cb.message.edit_caption(
            f"💵 **ᴄʜᴇǫᴜᴇ ᴄᴀsʜᴇᴅ** 💵\n\n"
            f"• **ᴀᴍᴏᴜɴᴛ:** Ŧ{_fmt(cheque['amount'])}\n"
            f"• **ғʀᴏᴍ:** {sender_user.mention}\n"
            f"• **ᴛᴏ:** {recipient_user.mention}\n"
            f"• **ɪᴅ:** `{cheque_id}`"
        )
    except Exception:
        pass

    await cb.answer("ᴄʜᴇǫᴜᴇ ᴄᴀsʜᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ!", show_alert=True)

    try:
        await client.send_message(
            cheque["sender_id"],
            f"📤 ʏᴏᴜʀ ᴄʜᴇǫᴜᴇ `{cheque_id}` ғᴏʀ **Ŧ{_fmt(cheque['amount'])} ᴋᴀᴋᴇʀᴀ** ʜᴀs ʙᴇᴇɴ ᴄᴀsʜᴇᴅ",
        )
    except Exception:
        pass

    del _cheques[cheque_id]
    log.info("CASH_CB: id=%s  recipient=%d", cheque_id, cheque["recipient_id"])


@app.on_callback_query(filters.regex(r"^void_"))
async def void_cheque_cb(client, cb: CallbackQuery):
    cheque_id = cb.data[5:]   # strip "void_"
    cheque    = _cheques.get(cheque_id)

    if not cheque:
        return await cb.answer("ᴄʜᴇǫᴜᴇ ᴀʟʀᴇᴀᴅʏ ᴠᴏɪᴅᴇᴅ", show_alert=True)

    if cb.from_user.id != cheque["sender_id"]:
        return await cb.answer("ᴏɴʟʏ ᴛʜᴇ sᴇɴᴅᴇʀ ᴄᴀɴ ᴠᴏɪᴅ ᴛʜɪs", show_alert=True)

    del _cheques[cheque_id]
    await cb.answer("ᴄʜᴇǫᴜᴇ ᴠᴏɪᴅᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ", show_alert=True)
    try:
        await cb.message.edit_caption("❌ **ᴛʜɪs ᴄʜᴇǫᴜᴇ ʜᴀs ʙᴇᴇɴ ᴠᴏɪᴅᴇᴅ ʙʏ ᴛʜᴇ sᴇɴᴅᴇʀ**")
    except Exception:
        pass
    log.info("VOID_CB: id=%s  sender=%d", cheque_id, cheque["sender_id"])
