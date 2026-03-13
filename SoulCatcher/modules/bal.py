"""SoulCatcher/modules/bal.py

Command:
  /bal  —  balance card for yourself or a replied-to user.
"""
from __future__ import annotations
import logging
from pyrogram import filters, enums
from pyrogram.types import Message
from .. import app
from ..database import get_or_create_user, get_user

log  = logging.getLogger("SoulCatcher.bal")
HTML = enums.ParseMode.HTML
_DIV  = "━━━━━━━━━━━━━━━━━━━━"
_SDIV = "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄"

def _fmt(n) -> str:
    try:    return f"{int(n):,}"
    except: return str(n)

def _esc(t: str) -> str:
    return str(t).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def _mention(name: str, uid: int) -> str:
    return f'<a href="tg://user?id={uid}"><b>{_esc(name)}</b></a>'


@app.on_message(filters.command("bal"))
async def cmd_bal(client, message: Message):
    target = (
        message.reply_to_message.from_user
        if message.reply_to_message
        else message.from_user
    )
    try:
        await get_or_create_user(
            target.id,
            target.username or "",
            target.first_name or "",
            getattr(target, "last_name", "") or "",
        )
        doc = await get_user(target.id)
        if not doc:
            return await message.reply_text("❌ <b>Not registered.</b>", parse_mode=HTML)

        bal  = doc.get("balance", 0)
        bank = doc.get("saved_amount", 0)
        loan = doc.get("loan_amount", 0)

        text = (
            f"🌸 <b>SOULCATCHER BALANCE</b>\n"
            f"<code>{_DIV}</code>\n"
            f"👤 {_mention(target.first_name, target.id)}\n"
            f"<code>{_SDIV}</code>\n"
            f"🌸 <b>Kakera</b>   <code>{_fmt(bal)}</code>\n"
            f"🏦 <b>Bank</b>     <code>{_fmt(bank)}</code>\n"
            f"💳 <b>Loan</b>     <code>{_fmt(loan)}</code>\n"
            f"<code>{_DIV}</code>"
        )

        custom = doc.get("custom_media")
        try:
            if custom:
                t, mid = custom.get("type"), custom.get("id")
                if t == "photo":
                    return await message.reply_photo(mid, caption=text, parse_mode=HTML)
                if t == "video":
                    return await message.reply_video(mid, caption=text, parse_mode=HTML)
                if t == "animation":
                    return await message.reply_animation(mid, caption=text, parse_mode=HTML)
            async for p in client.get_chat_photos(target.id, limit=1):
                return await message.reply_photo(p.file_id, caption=text, parse_mode=HTML)
        except Exception:
            pass

        await message.reply_text(text, parse_mode=HTML)

    except Exception as e:
        log.error("/bal error: %s", e)
        await message.reply_text("❌ <b>Failed to load balance.</b>", parse_mode=HTML)
