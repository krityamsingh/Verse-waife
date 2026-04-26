"""SoulCatcher/modules/start.py — /start, /help, /about, /stats commands."""
from __future__ import annotations

import random
import SoulCatcher as _soul
from pyrogram import filters
from pyrogram.types import Message, InlineKeyboardMarkup as IKM, InlineKeyboardButton as IKB

from SoulCatcher.config import (
    BOT_NAME, BOT_VERSION, SUPPORT_GROUP, UPDATE_CHANNEL,
    START_IMAGE_URL, START_VIDEO_URLS, START_STICKER_ID,
)
from SoulCatcher.database import get_or_create_user, count_all_users, count_all_groups, count_characters


# ── /start ────────────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command("start") & filters.private)
async def start_private(_, m: Message):
    u = m.from_user
    await get_or_create_user(u.id, u.username or "", u.first_name or "", u.last_name or "")

    buttons = IKM([
        [
            IKB("📚 Help", callback_data="help_main"),
            IKB("🌸 Harem", callback_data="open_harem"),
        ],
        [
            IKB("🏪 Market", callback_data="open_market"),
            IKB("🏆 Top", callback_data="open_top"),
        ],
        [
            IKB("💬 Support", url=f"https://t.me/{SUPPORT_GROUP}") if SUPPORT_GROUP else IKB("📢 Updates", url=f"https://t.me/{UPDATE_CHANNEL}"),
        ],
    ])

    caption = (
        f"✨ **Welcome to {BOT_NAME}!** ✨\n\n"
        f"Hey, **{u.first_name}**! 🌸\n\n"
        "**Collect**, **trade**, and **battle** with anime souls.\n\n"
        "📌 **Quick Start:**\n"
        "• Characters spawn every 15 messages in groups\n"
        "• Type the character's **name** to claim them!\n"
        "• Earn **kakera** 💰 from daily rewards & spin\n\n"
        "Use `/help` for a full command list."
    )

    if START_VIDEO_URLS:
        url = random.choice(START_VIDEO_URLS)
        try:
            await m.reply_video(url, caption=caption, reply_markup=buttons)
            return
        except Exception:
            pass

    if START_IMAGE_URL:
        try:
            await m.reply_photo(START_IMAGE_URL, caption=caption, reply_markup=buttons)
            return
        except Exception:
            pass

    if START_STICKER_ID:
        try:
            await m.reply_sticker(START_STICKER_ID)
        except Exception:
            pass

    await m.reply(caption, reply_markup=buttons)


@_soul.app.on_message(filters.command("start") & filters.group)
async def start_group(_, m: Message):
    u = m.from_user
    await get_or_create_user(u.id, u.username or "", u.first_name or "", u.last_name or "")
    await m.reply(
        f"🌸 **{BOT_NAME}** is active here!\n"
        "Characters spawn every 15 messages. Type the name to claim!\n"
        f"📩 Start a DM with me for your profile & commands."
    )


# ── /help ─────────────────────────────────────────────────────────────────────

HELP_TEXT = """
📖 **SoulCatcher Commands**

**👤 Profile**
• `/profile` — Your stats & badges
• `/daily` — Claim daily kakera (streak bonus!)
• `/spin` — Spin for kakera (1h cooldown)
• `/balance` — Check your kakera balance
• `/pay <amount>` — Pay kakera (reply to user)
• `/level` — View your XP & level progress

**🎴 Collection**
• `/harem [page]` — Browse your characters
• `/view <ID>` — View a character card
• `/burn <ID>` — Sell character for kakera
• `/setfav <ID>` — Toggle favourite ⭐
• `/note <ID> <text>` — Set a note on a character
• `/sort <rarity|name|anime|recent>` — Sort harem

**🌟 Wishlist**
• `/wish <charID>` — Add to wishlist (max 25)
• `/wishlist` — View your wishlist
• `/unwish <charID>` — Remove from wishlist

**🔄 Trading**
• `/trade <myID> <theirID>` — Propose trade (reply)
• `/gift <ID>` — Gift character (reply)

**💒 Marriage**
• `/propose` — Propose marriage (reply)
• `/marry` — Accept a proposal (reply)
• `/divorce` — End marriage

**🏪 Market**
• `/market [rarity]` — Browse listings
• `/buy <listingID>` — Purchase a listing

**🎮 Mini-Games**
• `/quiz` — Start an anime character quiz
• `/drop` — Force a character spawn (group)

**📊 Stats**
• `/top` — Leaderboard
• `/search <name>` — Search characters
• `/rarities` — Rarity tier list
"""


@_soul.app.on_message(filters.command("help"))
async def help_cmd(_, m: Message):
    await m.reply(HELP_TEXT, disable_web_page_preview=True)


# ── /about & /stats ───────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command(["about", "botinfo"]))
async def about_cmd(_, m: Message):
    users  = await count_all_users()
    groups = await count_all_groups()
    chars  = await count_characters(enabled=True)
    await m.reply(
        f"🌸 **{BOT_NAME} v{BOT_VERSION}**\n\n"
        f"👤 Users: `{users:,}`\n"
        f"👥 Groups: `{groups:,}`\n"
        f"🎴 Characters: `{chars:,}`\n\n"
        "Built with ❤️ using **Pyrogram** & **MongoDB**"
    )


@_soul.app.on_message(filters.command("stats") & _soul.sudo_filter)
async def stats_cmd(_, m: Message):
    users  = await count_all_users()
    groups = await count_all_groups()
    chars  = await count_characters(enabled=False)
    active = await count_characters(enabled=True)
    await m.reply(
        f"📊 **Bot Statistics**\n\n"
        f"👤 Total Users:      `{users:,}`\n"
        f"👥 Total Groups:     `{groups:,}`\n"
        f"🎴 Active Chars:     `{active:,}`\n"
        f"📦 Total Chars:      `{chars:,}`\n"
        f"🤖 Bot Version:      `{BOT_VERSION}`"
    )
