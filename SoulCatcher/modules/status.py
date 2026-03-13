"""SoulCatcher/modules/status.py

Command:
  /status  —  full player stats card (collection %, economy, rank, rarity breakdown).
"""
from __future__ import annotations
import os, logging
from pyrogram import filters, enums
from pyrogram.types import Message
from .. import app
from ..database import (
    get_or_create_user, get_user,
    get_harem, get_harem_rarity_counts,
    count_user_rank, count_characters,
)
from ..rarity import get_rarity, get_rarity_order

log  = logging.getLogger("SoulCatcher.status")
HTML = enums.ParseMode.HTML
_DIV  = "━━━━━━━━━━━━━━━━━━━━"
_SDIV = "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄"

def _fmt(n) -> str:
    try:    return f"{int(n):,}"
    except: return str(n)

def _bar(pct: float, w: int = 12) -> str:
    filled = round(max(0.0, min(1.0, pct)) * w)
    return "█" * filled + "░" * (w - filled)

def _wealth(n: int) -> str:
    for thr, lbl in reversed([
        (0,"Lost Soul"),(1_000,"Traveler"),(5_000,"Merchant"),
        (20_000,"Guild Master"),(50_000,"Lord"),(150_000,"Duke"),
        (500_000,"Prince"),(1_000_000,"King"),(5_000_000,"Emperor"),(10_000_000,"Soul Lord"),
    ]):
        if n >= thr: return lbl
    return "Lost Soul"

def _esc(t: str) -> str:
    return str(t).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def _mention(name: str, uid: int) -> str:
    return f'<a href="tg://user?id={uid}"><b>{_esc(name)}</b></a>'


@app.on_message(filters.command("status"))
async def cmd_status(client, message: Message):
    user    = message.from_user
    loading = await message.reply_text("🔍 <i>Loading your status…</i>", parse_mode=HTML)
    try:
        await message.react("⚡")
    except Exception:
        pass

    try:
        await get_or_create_user(
            user.id, user.username or "",
            user.first_name or "", getattr(user, "last_name", "") or "",
        )
        doc = await get_user(user.id)
        if not doc:
            return await loading.edit_text("❌ <b>Not registered.</b>", parse_mode=HTML)

        _, total      = await get_harem(user.id, page=1, per_page=1)
        total_db      = await count_characters()
        comp          = (total / total_db * 100) if total_db else 0
        balance       = doc.get("balance", 0)
        bank          = doc.get("saved_amount", 0)
        loan          = doc.get("loan_amount", 0)
        rank          = await count_user_rank(user.id)
        rarity_counts = await get_harem_rarity_counts(user.id)

        r_lines = []
        for r_name in get_rarity_order():
            cnt = rarity_counts.get(r_name, 0)
            if cnt:
                tier = get_rarity(r_name)
                em   = tier.emoji if tier else "✦"
                dn   = _esc(tier.display_name) if tier else _esc(r_name)
                r_lines.append(f"  {em} <b>{dn}</b>  <code>{_fmt(cnt)}</code>")

        caption = (
            f"✨ <b>PLAYER STATUS</b> ✨\n"
            f"<code>{_DIV}</code>\n"
            f"👤 {_mention(user.first_name, user.id)}\n"
            f"🆔 <code>{user.id}</code>\n"
            f"<code>{_DIV}</code>\n"
            f"📦 <b>Collection</b>\n"
            f"  <code>{_fmt(total)}</code> / <code>{_fmt(total_db)}</code>  "
            f"<code>{_bar(comp / 100)}</code>  <b>{comp:.1f}%</b>\n"
            f"<code>{_DIV}</code>\n"
            f"💰 <b>Economy</b>\n"
            f"  🌸 Kakera  <code>{_fmt(balance)}</code>  <i>({_esc(_wealth(balance))})</i>\n"
            f"  🏦 Bank    <code>{_fmt(bank)}</code>\n"
            f"  💳 Loan    <code>{_fmt(loan)}</code>\n"
            f"<code>{_DIV}</code>\n"
            f"🏆 Global Rank  <b>#{rank}</b>\n"
            f"<code>{_DIV}</code>\n"
            f"🎭 <b>Rarity Breakdown</b>\n"
            + ("\n".join(r_lines) if r_lines else "  <i>No characters yet</i>") +
            f"\n<code>{_DIV}</code>"
        )

        await loading.delete()

        photo_path = None
        try:
            async for p in client.get_chat_photos(user.id, limit=1):
                photo_path = await client.download_media(p.file_id)
                break
        except Exception:
            pass

        if photo_path:
            await message.reply_photo(photo_path, caption=caption, parse_mode=HTML)
            try: os.remove(photo_path)
            except Exception: pass
        else:
            await message.reply_text(caption, parse_mode=HTML)

    except Exception as e:
        log.error("/status error uid=%s: %s", user.id, e)
        try:
            await loading.edit_text("❌ <b>Failed to load status.</b>", parse_mode=HTML)
        except Exception:
            pass
