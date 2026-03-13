"""SoulCatcher/modules/profile.py

Command:
  /profile  —  personal profile card with rarity breakdown, streak, badges.
"""
from __future__ import annotations
import os, logging
from datetime import datetime
from pyrogram import filters, enums
from pyrogram.types import Message
from .. import app
from ..database import (
    get_or_create_user, get_user,
    get_harem, get_harem_rarity_counts,
    count_user_rank, count_characters,
)
from ..rarity import get_rarity, get_rarity_order

log  = logging.getLogger("SoulCatcher.profile")
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


@app.on_message(filters.command("profile"))
async def cmd_profile(client, message: Message):
    user = message.from_user
    try:
        await get_or_create_user(
            user.id, user.username or "",
            user.first_name or "", getattr(user, "last_name", "") or "",
        )
        doc = await get_user(user.id)
        if not doc:
            return await message.reply_text("❌ <b>Profile not found.</b>", parse_mode=HTML)

        _, total   = await get_harem(user.id, page=1, per_page=1)
        total_db   = await count_characters()
        comp       = (total / total_db * 100) if total_db else 0
        rarity_cnt = await get_harem_rarity_counts(user.id)
        rank       = await count_user_rank(user.id)
        kakera     = doc.get("balance", 0)
        streak     = doc.get("daily_streak", 0)
        badges     = doc.get("badges", [])
        joined     = doc.get("joined_at", datetime.utcnow())
        age_days   = (datetime.utcnow() - joined).days if hasattr(joined, "date") else 0

        r_lines = []
        for r_name in get_rarity_order():
            cnt = rarity_cnt.get(r_name, 0)
            if cnt:
                tier = get_rarity(r_name)
                em   = tier.emoji if tier else "✦"
                dn   = _esc(tier.display_name) if tier else _esc(r_name)
                r_lines.append(f"  {em} <b>{dn}</b>  <code>{_fmt(cnt)}</code>")

        uname_str = f"  @{_esc(user.username)}\n" if user.username else ""
        badge_str = f"\n🏅 <b>Badges</b>  {' '.join(_esc(b) for b in badges)}\n" if badges else ""

        text = (
            f"🌸 <b>{_esc(user.first_name)}</b>\n"
            f"{uname_str}"
            f"<code>{_DIV}</code>\n"
            f"📅 Joined <b>{age_days}d</b> ago  ·  🏆 Rank <b>#{rank}</b>\n"
            f"<code>{_SDIV}</code>\n"
            f"💰 <b>Kakera</b>   <code>{_fmt(kakera)}</code>  <i>({_esc(_wealth(kakera))})</i>\n"
            f"🔥 <b>Streak</b>   <code>{streak}</code> days\n"
            f"🎴 <b>Chars</b>    <code>{total}</code> / <code>{total_db}</code>  "
            f"<code>{_bar(comp / 100)}</code>  <b>{comp:.1f}%</b>\n"
            f"<code>{_DIV}</code>\n"
            f"🎭 <b>Rarity Breakdown</b>\n"
            + ("\n".join(r_lines) if r_lines else "  <i>None yet</i>") +
            f"\n{badge_str}"
            f"<code>{_DIV}</code>"
        )

        photo_path = None
        try:
            async for p in client.get_chat_photos(user.id, limit=1):
                photo_path = await client.download_media(p.file_id)
                break
        except Exception:
            pass

        if photo_path:
            await message.reply_photo(photo_path, caption=text, parse_mode=HTML)
            try: os.remove(photo_path)
            except Exception: pass
        else:
            await message.reply_text(text, parse_mode=HTML)

    except Exception as e:
        log.error("/profile error uid=%s: %s", user.id, e)
        await message.reply_text("❌ <b>Failed to load profile.</b>", parse_mode=HTML)
