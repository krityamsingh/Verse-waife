"""SoulCatcher/modules/topcollector.py

Commands:
  /topcollector       --  top 10 players by total characters owned
  /topc               --  alias
  /tc                 --  short alias
  /infotop <rank>     --  detailed info card for a specific leaderboard rank (sudo/owner only)
"""
from __future__ import annotations
import logging
from pyrogram import filters, enums
from pyrogram.types import Message
from .. import app, sudo_filter, owner_filter
from ..config import OWNER_IDS
from ..database import top_collectors, get_balance

log  = logging.getLogger("SoulCatcher.topcollector")
HTML = enums.ParseMode.HTML
_DIV    = "━━━━━━━━━━━━━━━━━━━━"
_MEDALS = ["🥇", "🥈", "🥉"] + ["🏅"] * 7

def _fmt(n) -> str:
    try:    return f"{int(n):,}"
    except: return str(n)

def _esc(t: str) -> str:
    return str(t).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")


# =============================================================================
#  /topcollector  /topc  /tc
# =============================================================================

@app.on_message(filters.command(["topcollector", "topc", "tc"]))
async def cmd_topcollector(_, message: Message):
    log.info("/topcollector triggered by uid=%s", message.from_user.id if message.from_user else "?")
    wait = await message.reply_text("⏳ <i>Loading top collectors…</i>", parse_mode=HTML)
    try:
        results = await top_collectors(10)
        if not results:
            return await wait.edit_text(
                "📊 <i>No collectors found yet. Start claiming characters!</i>",
                parse_mode=HTML,
            )

        lines = [f"🎴 <b>TOP 10 COLLECTORS</b>\n<code>{_DIV}</code>\n"]
        for i, r in enumerate(results):
            uid        = r.get("user_id", 0)
            char_count = r.get("char_count", 0)
            name       = _esc(r.get("first_name") or r.get("username") or f"User {uid}")
            medal      = _MEDALS[i] if i < len(_MEDALS) else f"{i+1}."
            if i == 0:
                lines.append(
                    f"{medal} <a href=\"tg://user?id={uid}\"><b>{name}</b></a>\n"
                    f"  🎴 <b><code>{_fmt(char_count)}</code></b> characters"
                )
            else:
                lines.append(
                    f"{medal} <a href=\"tg://user?id={uid}\">{name}</a>  "
                    f"<code>{_fmt(char_count)}</code> 🎴"
                )

        lines.append(f"\n<code>{_DIV}</code>")
        await wait.edit_text("\n".join(lines), parse_mode=HTML, disable_web_page_preview=True)

    except Exception as e:
        log.error("/topcollector error: %s", e, exc_info=True)
        await wait.edit_text("❌ <b>Failed to load collector leaderboard.</b>", parse_mode=HTML)


# =============================================================================
#  /infotop <rank>   —  sudo / owner only
# =============================================================================

@app.on_message(filters.command("infotop") & (sudo_filter | owner_filter))
async def cmd_infotop(client, message: Message):
    """
    Usage:  /infotop <rank>
    Shows detailed info (user ID, name, collection size, kakera balance)
    for the player at that position on the top-collector leaderboard.
    Restricted to sudo users and owners.
    """
    args = message.command

    # ── parse rank ────────────────────────────────────────────────────────────
    if len(args) < 2:
        return await message.reply_text(
            "ℹ️ <b>Usage:</b> <code>/infotop &lt;rank&gt;</code>\n"
            "Example: <code>/infotop 1</code>  (shows #1 collector)",
            parse_mode=HTML,
        )

    try:
        rank = int(args[1])
        if rank < 1:
            raise ValueError
    except ValueError:
        return await message.reply_text(
            "❌ Rank must be a positive number. Example: <code>/infotop 3</code>",
            parse_mode=HTML,
        )

    wait = await message.reply_text(
        f"⏳ <i>Fetching info for rank #{rank}…</i>", parse_mode=HTML
    )

    try:
        # fetch enough rows to reach the requested rank
        results = await top_collectors(rank)
        if not results or len(results) < rank:
            return await wait.edit_text(
                f"❌ Rank <b>#{rank}</b> not found — only "
                f"<b>{len(results)}</b> collector(s) exist right now.",
                parse_mode=HTML,
            )

        r    = results[rank - 1]
        uid  = r.get("user_id", 0)
        char_count = r.get("char_count", 0)

        # display name: try to resolve live from Telegram first
        name = r.get("first_name") or r.get("username") or f"User {uid}"
        username_str = ""
        try:
            tg_user = await client.get_users(uid)
            name = tg_user.first_name or name
            if tg_user.username:
                username_str = f"\n🔖 <b>Username:</b>  @{_esc(tg_user.username)}"
        except Exception:
            pass  # use DB name if Telegram lookup fails

        # kakera balance
        balance = await get_balance(uid)

        # medal label
        medal = _MEDALS[rank - 1] if rank - 1 < len(_MEDALS) else f"#{rank}"

        card = (
            f"{medal} <b>Rank #{rank} — Top Collector</b>\n"
            f"<code>{_DIV}</code>\n"
            f"👤 <b>Name:</b>  <a href=\"tg://user?id={uid}\">{_esc(name)}</a>"
            f"{username_str}\n"
            f"🆔 <b>User ID:</b>  <code>{uid}</code>\n"
            f"<code>{_DIV}</code>\n"
            f"🎴 <b>Collection:</b>  <code>{_fmt(char_count)}</code> characters\n"
            f"🌸 <b>Kakera Balance:</b>  <code>{_fmt(balance)}</code> kakera\n"
            f"<code>{_DIV}</code>"
        )

        await wait.edit_text(card, parse_mode=HTML, disable_web_page_preview=True)

    except Exception as e:
        log.error("/infotop error: %s", e, exc_info=True)
        await wait.edit_text("❌ <b>Failed to fetch top collector info.</b>", parse_mode=HTML)
