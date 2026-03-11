"""SoulCatcher/modules/profile.py — /status /bal /profile /rank /top /toprarity /richest /rarityinfo /event"""
import os, random, math
from datetime import datetime
from pyrogram import filters
from pyrogram.types import Message
from .. import app
from ..database import (
    get_or_create_user, get_user, get_balance,
    get_harem, get_harem_rarity_counts,
    count_user_rank, top_collectors, top_by_rarity, top_richest,
    count_characters,
)
from ..rarity import get_rarity, get_rarity_order

def _fmt(n):
    try:    return f"{int(n):,}"
    except: return str(n)

def _bar(pct, w=10):
    fill = round(pct*w); return "█"*fill + "░"*(w-fill)

def _wealth(n):
    for thr, lbl in reversed([(0,"Beginner"),(1000,"Traveler"),(5000,"Merchant"),
        (20000,"Guild Master"),(50000,"Lord"),(150000,"Duke"),(500000,"Prince"),
        (1000000,"King"),(5000000,"Emperor"),(10000000,"Soul Lord")]):
        if n >= thr: return lbl
    return "Lost Soul"


@app.on_message(filters.command("status"))
async def cmd_status(client, message: Message):
    user = message.from_user
    try: await message.react("⚡")
    except Exception: pass
    loading = await message.reply_text("🔍 Loading...")
    await get_or_create_user(user.id, user.username or "", user.first_name or "")
    doc = await get_user(user.id)
    if not doc: return await loading.edit_text("❌ Not registered.")
    _, total = await get_harem(user.id, page=1, per_page=1)
    total_db = await count_characters()
    comp     = (total/total_db*100) if total_db else 0
    balance  = doc.get("balance", 0)
    bank     = doc.get("saved_amount", 0)
    loan     = doc.get("loan_amount",  0)
    rank     = await count_user_rank(user.id)
    rarity_counts = await get_harem_rarity_counts(user.id)
    r_lines = "\n".join(
        f"{get_rarity(r).emoji if get_rarity(r) else '?'} {r} → `{c}`"
        for r, c in sorted(rarity_counts.items())
    ) or "⚫ common → 0"
    caption = (
        f"✨ **Player Status** ✨\n───────────────────\n"
        f"👤 [{user.first_name}](tg://user?id={user.id})\n🆔 `{user.id}`\n"
        f"───────────────────\n📦 **Collection**\n"
        f"• Chars: `{_fmt(total)}/{_fmt(total_db)}` {_bar(comp/100)} `{comp:.1f}%`\n"
        f"───────────────────\n💰 **Economy**\n"
        f"• Kakera: `{_fmt(balance)}` ({_wealth(balance)})\n"
        f"• Bank: `{_fmt(bank)}` | Loan: `{_fmt(loan)}`\n"
        f"───────────────────\n🏆 Global Rank: **#{rank}**\n"
        f"───────────────────\n🎭 **Rarity Breakdown**\n{r_lines}\n───────────────────"
    )
    await loading.delete()
    photo_path = None
    try:
        async for p in client.get_chat_photos(user.id, limit=1):
            photo_path = await client.download_media(p.file_id); break
    except Exception: pass
    if photo_path:
        await message.reply_photo(photo_path, caption=caption)
        try: os.remove(photo_path)
        except Exception: pass
    else:
        await message.reply_text(caption)


@app.on_message(filters.command("bal"))
async def cmd_bal(client, message: Message):
    target = message.reply_to_message.from_user if message.reply_to_message else message.from_user
    await get_or_create_user(target.id, target.username or "", target.first_name or "")
    doc = await get_user(target.id)
    if not doc: return await message.reply_text("❌ Not registered.")
    text = (
        "🌸 **SoulCatcher Balance**\n━━━━━━━━━━━━━━━━━\n"
        f"❖ **User:** [{target.first_name}](tg://user?id={target.id})\n\n"
        f"💰 **Kakera:** `{_fmt(doc.get('balance',0))}`\n"
        f"🏦 **Bank:** `{_fmt(doc.get('saved_amount',0))}`\n"
        f"💸 **Loan:** `{_fmt(doc.get('loan_amount',0))}`\n"
        "━━━━━━━━━━━━━━━━━"
    )
    custom = doc.get("custom_media")
    try:
        if custom:
            t, mid = custom.get("type"), custom.get("id")
            if t == "photo":     return await message.reply_photo(mid, caption=text)
            if t == "video":     return await message.reply_video(mid, caption=text)
            if t == "animation": return await message.reply_animation(mid, caption=text)
        async for p in client.get_chat_photos(target.id, limit=1):
            return await message.reply_photo(p.file_id, caption=text)
    except Exception: pass
    await message.reply_text(text)


@app.on_message(filters.command("profile"))
async def cmd_profile(client, message: Message):
    user = message.from_user
    await get_or_create_user(user.id, user.username or "", user.first_name or "")
    doc = await get_user(user.id)
    if not doc: return await message.reply_text("❌ Profile not found.")
    _, total = await get_harem(user.id, page=1, per_page=1)
    total_db = await count_characters()
    comp     = (total/total_db*100) if total_db else 0
    rarity_cnt = await get_harem_rarity_counts(user.id)
    rank     = await count_user_rank(user.id)
    kakera   = doc.get("balance", 0)
    streak   = doc.get("daily_streak", 0)
    badges   = doc.get("badges", [])
    joined   = doc.get("joined_at", datetime.utcnow())
    age_days = (datetime.utcnow()-joined).days if hasattr(joined,"date") else 0
    r_lines  = []
    for r_name in get_rarity_order():
        cnt = rarity_cnt.get(r_name, 0)
        if cnt:
            t = get_rarity(r_name)
            r_lines.append(f"  {t.emoji} **{t.display_name}**: `{cnt}`")
    text = (
        f"🌸 **{user.first_name}**" + (f" (@{user.username})" if user.username else "")
        + f"\n📅 Joined {age_days}d ago | 🏆 Rank **#{rank}**"
        + f"\n\n💰 **Kakera:** `{_fmt(kakera)}` ({_wealth(kakera)})"
        + f"\n🔥 **Streak:** `{streak}` days"
        + f"\n🎴 **Chars:** `{total}/{total_db}` {_bar(comp/100)} `{comp:.1f}%`"
        + f"\n\n**Rarity Breakdown:**\n" + ("\n".join(r_lines) or "  _None yet_")
        + (f"\n\n🏅 **Badges:** " + " ".join(badges) if badges else "")
    )
    photo_path = None
    try:
        async for p in client.get_chat_photos(user.id, limit=1):
            photo_path = await client.download_media(p.file_id); break
    except Exception: pass
    if photo_path:
        await message.reply_photo(photo_path, caption=text)
        try: os.remove(photo_path)
        except Exception: pass
    else:
        await message.reply_text(text)


@app.on_message(filters.command("rank"))
async def cmd_rank(_, message: Message):
    rank = await count_user_rank(message.from_user.id)
    _, total = await get_harem(message.from_user.id, page=1, per_page=1)
    await message.reply_text(f"🏆 Your rank: **#{rank}** with **{total}** characters!")


@app.on_message(filters.command("top"))
async def cmd_top(client, message: Message):
    results = await top_collectors(10); medals = ["🥇","🥈","🥉"]+["🏅"]*7
    text = "🏆 **Top 10 Soul Collectors**\n\n"
    for i, r in enumerate(results):
        doc  = await get_user(r["_id"]) or {}
        text += f"{medals[i]} **{doc.get('first_name',f'User#{r[chr(95)]}')}** — `{r['count']}` chars\n"
    await message.reply_text(text)


@app.on_message(filters.command("toprarity"))
async def cmd_toprarity(client, message: Message):
    args = message.command
    if len(args) < 2: return await message.reply_text("Usage: `/toprarity <rarity_name>`")
    tier = get_rarity(args[1].lower())
    if not tier: return await message.reply_text("❌ Unknown rarity.")
    results = await top_by_rarity(args[1].lower(), 10)
    if not results: return await message.reply_text(f"No {tier.display_name} collectors yet.")
    text = f"{tier.emoji} **Top 10 {tier.display_name} Collectors**\n\n"
    for i, r in enumerate(results, 1):
        doc  = await get_user(r["_id"]) or {}
        text += f"`{i}.` **{doc.get('first_name',f'User#{r[chr(95)]}')}** — `{r['count']}`\n"
    await message.reply_text(text)


@app.on_message(filters.command("richest"))
async def cmd_richest(_, message: Message):
    results = await top_richest(10); medals = ["🥇","🥈","🥉"]+["🏅"]*7
    text = "💰 **Top 10 Richest**\n\n"
    for i, r in enumerate(results):
        text += f"{medals[i]} **{r.get('first_name',f'User#{r[\"user_id\"]}')}** — `{_fmt(r.get('balance',0))}` 🪙\n"
    await message.reply_text(text)


@app.on_message(filters.command("rarityinfo"))
async def cmd_rarityinfo(_, message: Message):
    from ..rarity import get_rarity_card, RARITIES, SUB_RARITIES
    args = message.command
    if len(args) > 1:
        return await message.reply_text(get_rarity_card(args[1].lower()))
    # Show all tiers in a clean table
    lines = []
    for r in RARITIES.values():
        subs = "  ".join(f"{s.emoji}`{s.display_name}`" for s in r.sub_rarities)
        lines.append(
            f"{r.emoji} **{r.display_name}** (Tier {r.id})\n"
            f"  Weight:`{r.weight}` Kakera:`{r.kakera_reward}` Claim:`{r.claim_window_seconds}s`"
            + (f"\n  └ Sub: {subs}" if subs else "")
        )
    await message.reply_text("🌸 **SoulCatcher Rarity Table**\n\n" + "\n\n".join(lines))


@app.on_message(filters.command("event"))
async def cmd_event(_, message: Message):
    import SoulCatcher.rarity as _mod
    mode = _mod.GAME_MODES.get(_mod.CURRENT_MODE, _mod.GAME_MODES["normal"])
    await message.reply_text(
        f"🎮 **Current Mode:** {mode['label']}\n\n"
        f"• Spawn weight: `{mode['weight_mult']}×`\n"
        f"• Kakera reward: `{mode['kakera_mult']}×`"
    )
