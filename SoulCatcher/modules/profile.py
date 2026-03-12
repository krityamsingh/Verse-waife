"""SoulCatcher/modules/profile.py

Commands:
  /status       — full stats card
  /bal          — balance card
  /profile      — profile card with rarity breakdown
  /richest      — top 10 richest
  /rarityinfo   — rarity table
  /event        — current game mode

Note: /rank /top /toprarity have been removed from this file.
      They live in tops.py (/ktop /ctop) and are no longer duplicated here.
"""

import os
import logging
from datetime import datetime

from pyrogram import filters, enums
from pyrogram.types import Message

from .. import app
from ..database import (
    _col,
    get_or_create_user, get_user,
    get_harem, get_harem_rarity_counts,
    count_user_rank, top_richest,
    count_characters,
)
from ..rarity import get_rarity, get_rarity_order, RARITIES

log  = logging.getLogger("SoulCatcher.profile")
HTML = enums.ParseMode.HTML
MD   = enums.ParseMode.MARKDOWN

# ── Helpers ────────────────────────────────────────────────────────────────────

def _fmt(n) -> str:
    try:    return f"{int(n):,}"
    except: return str(n)


def _bar(pct: float, w: int = 12) -> str:
    filled = round(max(0.0, min(1.0, pct)) * w)
    return "█" * filled + "░" * (w - filled)


def _wealth(n: int) -> str:
    for thr, lbl in reversed([
        (0,          "Lost Soul"),
        (1_000,      "Traveler"),
        (5_000,      "Merchant"),
        (20_000,     "Guild Master"),
        (50_000,     "Lord"),
        (150_000,    "Duke"),
        (500_000,    "Prince"),
        (1_000_000,  "King"),
        (5_000_000,  "Emperor"),
        (10_000_000, "Soul Lord"),
    ]):
        if n >= thr:
            return lbl
    return "Lost Soul"


def _esc(text: str) -> str:
    """Escape HTML special characters."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _mention(name: str, uid: int) -> str:
    return f'<a href="tg://user?id={uid}"><b>{_esc(name)}</b></a>'


_DIV   = "━━━━━━━━━━━━━━━━━━━━"
_SDIV  = "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄"
_MEDALS = ["🥇", "🥈", "🥉"] + ["🏅"] * 7


# ── /status ────────────────────────────────────────────────────────────────────

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


# ── /bal ───────────────────────────────────────────────────────────────────────

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


# ── /profile ───────────────────────────────────────────────────────────────────

@app.on_message(filters.command("profile"))
async def cmd_profile(client, message: Message):
    user = message.from_user
    try:
        await get_or_create_user(
            user.id,
            user.username or "",
            user.first_name or "",
            getattr(user, "last_name", "") or "",
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


# ── /richest ───────────────────────────────────────────────────────────────────

@app.on_message(filters.command("richest"))
async def cmd_richest(_, message: Message):
    wait = await message.reply_text("⏳ <i>Loading richest players…</i>", parse_mode=HTML)
    try:
        results = await top_richest(10)
        results = [r for r in results if r.get("balance", 0) > 0]
        if not results:
            return await wait.edit_text("📊 <i>No wealthy players yet.</i>", parse_mode=HTML)

        lines = [
            f"💰 <b>TOP 10 RICHEST PLAYERS</b>\n"
            f"<code>{_DIV}</code>\n"
        ]
        for i, r in enumerate(results):
            uid   = r.get("user_id", 0)
            name  = _esc(r.get("first_name") or r.get("username") or f"User {uid}")
            bal   = r.get("balance", 0)
            medal = _MEDALS[i] if i < len(_MEDALS) else f"{i+1}."
            if i == 0:
                lines.append(
                    f"{medal} <a href=\"tg://user?id={uid}\"><b>{name}</b></a>\n"
                    f"  🌸 <b><code>{_fmt(bal)}</code></b> kakera"
                )
            else:
                lines.append(
                    f"{medal} <a href=\"tg://user?id={uid}\">{name}</a>  "
                    f"<code>{_fmt(bal)}</code> 🌸"
                )

        lines.append(f"\n<code>{_DIV}</code>")
        await wait.edit_text(
            "\n".join(lines), parse_mode=HTML, disable_web_page_preview=True
        )

    except Exception as e:
        log.error("/richest error: %s", e)
        await wait.edit_text("❌ <b>Failed to load leaderboard.</b>", parse_mode=HTML)


# ── /rarityinfo ────────────────────────────────────────────────────────────────

@app.on_message(filters.command("rarityinfo"))
async def cmd_rarityinfo(_, message: Message):
    try:
        args = message.command

        if len(args) > 1:
            # get_rarity_card returns Markdown — send with MD parse mode
            from ..rarity import get_rarity_card
            card = get_rarity_card(args[1].lower())
            return await message.reply_text(card, parse_mode=MD)

        # Full table — build in HTML
        lines = [
            f"🌸 <b>SOULCATCHER RARITY TABLE</b>\n"
            f"<code>{_DIV}</code>\n"
        ]
        for r in RARITIES.values():
            subs = "  ".join(
                f"{s.emoji} <code>{_esc(s.display_name)}</code>"
                for s in r.sub_rarities
            )
            lines.append(
                f"{r.emoji} <b>{_esc(r.display_name)}</b>  <i>(Tier {r.id})</i>\n"
                f"  Weight <code>{r.weight}</code>  "
                f"Kakera <code>{r.kakera_reward}</code>  "
                f"Claim <code>{r.claim_window_seconds}s</code>"
                + (f"\n  └ {subs}" if subs else "")
            )

        lines.append(f"\n<code>{_DIV}</code>")
        await message.reply_text("\n\n".join(lines), parse_mode=HTML)

    except Exception as e:
        log.error("/rarityinfo error: %s", e)
        await message.reply_text("❌ <b>Failed to load rarity info.</b>", parse_mode=HTML)


# ── /event ─────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("event"))
async def cmd_event(_, message: Message):
    try:
        import SoulCatcher.rarity as _mod
        mode = _mod.GAME_MODES.get(_mod.CURRENT_MODE, _mod.GAME_MODES["normal"])
        await message.reply_text(
            f"🎮 <b>CURRENT GAME MODE</b>\n"
            f"<code>{_SDIV}</code>\n"
            f"✦ <b>{_esc(mode['label'])}</b>\n"
            f"<code>{_SDIV}</code>\n"
            f"⚡ Spawn weight   <code>{mode['weight_mult']}×</code>\n"
            f"🌸 Kakera reward  <code>{mode['kakera_mult']}×</code>\n"
            f"<code>{_SDIV}</code>",
            parse_mode=HTML,
        )
    except Exception as e:
        log.error("/event error: %s", e)
        await message.reply_text("❌ <b>Failed to load event info.</b>", parse_mode=HTML)
