"""SoulCatcher/modules/status.py

Command:
  /status  —  full player stats card (collection %, economy, rank, rarity breakdown)
              optionally shows the user's Telegram profile photo as a header image.
"""
from __future__ import annotations

import os
import logging

from pyrogram import filters
from pyrogram.types import Message

from .. import app
from ..database import (
    get_or_create_user, get_user,
    get_harem, get_harem_rarity_counts,
    count_user_rank, count_characters,
)
from ..rarity import get_rarity, get_rarity_order
from .profile_helpers import HTML, DIV, SDIV, fmt, bar, wealth, esc, mention

log = logging.getLogger("SoulCatcher.status")


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
            user.id,
            user.username or "",
            user.first_name or "",
            getattr(user, "last_name", "") or "",
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
                dn   = esc(tier.display_name) if tier else esc(r_name)
                r_lines.append(f"  {em} <b>{dn}</b>  <code>{fmt(cnt)}</code>")

        caption = (
            f"✨ <b>PLAYER STATUS</b> ✨\n"
            f"<code>{DIV}</code>\n"
            f"👤 {mention(user.first_name, user.id)}\n"
            f"🆔 <code>{user.id}</code>\n"
            f"<code>{DIV}</code>\n"
            f"📦 <b>Collection</b>\n"
            f"  <code>{fmt(total)}</code> / <code>{fmt(total_db)}</code>  "
            f"<code>{bar(comp / 100)}</code>  <b>{comp:.1f}%</b>\n"
            f"<code>{DIV}</code>\n"
            f"💰 <b>Economy</b>\n"
            f"  🌸 Kakera  <code>{fmt(balance)}</code>  <i>({esc(wealth(balance))})</i>\n"
            f"  🏦 Bank    <code>{fmt(bank)}</code>\n"
            f"  💳 Loan    <code>{fmt(loan)}</code>\n"
            f"<code>{DIV}</code>\n"
            f"🏆 Global Rank  <b>#{rank}</b>\n"
            f"<code>{DIV}</code>\n"
            f"🎭 <b>Rarity Breakdown</b>\n"
            + ("\n".join(r_lines) if r_lines else "  <i>No characters yet</i>") +
            f"\n<code>{DIV}</code>"
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
            try:
                os.remove(photo_path)
            except Exception:
                pass
        else:
            await message.reply_text(caption, parse_mode=HTML)

    except Exception as e:
        log.error("/status error uid=%s: %s", user.id, e)
        try:
            await loading.edit_text("❌ <b>Failed to load status.</b>", parse_mode=HTML)
        except Exception:
            pass
