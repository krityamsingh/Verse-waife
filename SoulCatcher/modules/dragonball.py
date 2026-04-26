"""SoulCatcher/modules/dragonball.py — Dragon Ball themed mini-game & character system."""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument

import SoulCatcher as _soul
from pyrogram import filters
from pyrogram.types import Message, InlineKeyboardMarkup as IKM, InlineKeyboardButton as IKB, CallbackQuery

from SoulCatcher.config import MONGO_URI, DB_NAME
from SoulCatcher.database import (
    get_or_create_user,
    get_balance,
    add_balance,
    deduct_balance,
    add_xp,
)

log = logging.getLogger("SoulCatcher.dragonball")

# ── DB setup ──────────────────────────────────────────────────────────────────

_db = None

async def init_db():
    global _db
    client = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=10_000)
    _db    = client[DB_NAME]
    await _db["db_dragon_balls"].create_index([("user_id", 1)], unique=True)
    await _db["db_wishes"].create_index([("user_id", 1), ("wished_at", -1)])
    await _db["db_tournament"].create_index([("user_id", 1)])
    log.info("✅ DragonBall DB ready")


def _col(name: str):
    return _db[name]


# ── Constants ─────────────────────────────────────────────────────────────────

DRAGON_BALLS   = ["⭐", "⭐⭐", "⭐⭐⭐", "⭐⭐⭐⭐", "⭐⭐⭐⭐⭐", "⭐⭐⭐⭐⭐⭐", "⭐⭐⭐⭐⭐⭐⭐"]
BALL_NAMES     = ["One-Star", "Two-Star", "Three-Star", "Four-Star", "Five-Star", "Six-Star", "Seven-Star"]
TOTAL_BALLS    = 7
WISH_COOLDOWN  = 7 * 24 * 3600  # 7 days

WISHES = {
    "kakera":   {"label": "💰 10,000 Kakera",         "desc": "Receive 10,000 kakera instantly."},
    "xp":       {"label": "⭐ 2,000 XP",              "desc": "Gain 2,000 XP toward your level."},
    "revive":   {"label": "♻️ Revive burned char",     "desc": "Recover a recently burned character."},
    "immunity": {"label": "🛡 1-week immunity",         "desc": "Cannot be gbanned for 7 days."},
    "reroll":   {"label": "🎲 Daily reroll ×2",        "desc": "Double kakera from your next daily."},
}

POWER_LEVELS = {
    "Earthling":     (0,    500),
    "Saiyan":        (501,  2000),
    "Super Saiyan":  (2001, 6000),
    "Super Saiyan 2":(6001, 12000),
    "Super Saiyan 3":(12001,25000),
    "Super Saiyan 4":(25001,50000),
    "Super Saiyan God":(50001,100000),
    "Ultra Instinct":(100001, 999999),
}

SIGNATURE_MOVES = [
    "Kamehameha", "Final Flash", "Special Beam Cannon", "Galick Gun",
    "Big Bang Attack", "Destructo Disc", "Solar Flare", "Spirit Bomb",
    "Dragon Fist", "Masenko", "Tri-Beam", "Hell Flash",
]

DB_CHARACTERS = [
    {"name": "Goku",       "rarity": "eternal",   "power": 9000,  "anime": "Dragon Ball Z"},
    {"name": "Vegeta",     "rarity": "infernal",  "power": 8500,  "anime": "Dragon Ball Z"},
    {"name": "Gohan",      "rarity": "cosmos",    "power": 7000,  "anime": "Dragon Ball Z"},
    {"name": "Frieza",     "rarity": "mythic",    "power": 8000,  "anime": "Dragon Ball Z"},
    {"name": "Cell",       "rarity": "mythic",    "power": 7500,  "anime": "Dragon Ball Z"},
    {"name": "Piccolo",    "rarity": "infernal",  "power": 5000,  "anime": "Dragon Ball Z"},
    {"name": "Trunks",     "rarity": "seasonal",  "power": 6000,  "anime": "Dragon Ball Z"},
    {"name": "Krillin",    "rarity": "rare",      "power": 1000,  "anime": "Dragon Ball Z"},
    {"name": "Beerus",     "rarity": "eternal",   "power": 9900,  "anime": "Dragon Ball Super"},
    {"name": "Whis",       "rarity": "eternal",   "power": 9999,  "anime": "Dragon Ball Super"},
    {"name": "Jiren",      "rarity": "mythic",    "power": 9500,  "anime": "Dragon Ball Super"},
    {"name": "Broly",      "rarity": "mythic",    "power": 9200,  "anime": "Dragon Ball Super"},
    {"name": "Hit",        "rarity": "infernal",  "power": 7000,  "anime": "Dragon Ball Super"},
    {"name": "Zamasu",     "rarity": "infernal",  "power": 6500,  "anime": "Dragon Ball Super"},
    {"name": "Kefla",      "rarity": "seasonal",  "power": 7200,  "anime": "Dragon Ball Super"},
    {"name": "Android 17", "rarity": "cosmos",    "power": 6000,  "anime": "Dragon Ball Super"},
    {"name": "Android 18", "rarity": "cosmos",    "power": 5800,  "anime": "Dragon Ball Z"},
    {"name": "Gotenks",    "rarity": "cosmos",    "power": 5500,  "anime": "Dragon Ball Z"},
    {"name": "Videl",      "rarity": "rare",      "power": 800,   "anime": "Dragon Ball Z"},
    {"name": "Bulma",      "rarity": "rare",      "power": 200,   "anime": "Dragon Ball"},
]


def _get_power_tier(power: int) -> str:
    for tier, (lo, hi) in POWER_LEVELS.items():
        if lo <= power <= hi:
            return tier
    return "Unknown"


# ── Dragon Ball Collection ────────────────────────────────────────────────────

async def _get_user_balls(uid: int) -> dict:
    doc = await _col("db_dragon_balls").find_one({"user_id": uid})
    if not doc:
        doc = {"user_id": uid, "balls": [], "total_collected": 0, "last_search": None}
        await _col("db_dragon_balls").insert_one(doc)
    return doc


async def _has_all_balls(uid: int) -> bool:
    doc = await _get_user_balls(uid)
    return len(set(doc.get("balls", []))) >= TOTAL_BALLS


@_soul.app.on_message(filters.command(["searchball", "sball", "findball"]))
async def search_ball(_, m: Message):
    uid    = m.from_user.id
    u      = m.from_user
    await get_or_create_user(uid, u.username or "", u.first_name or "", u.last_name or "")

    doc     = await _get_user_balls(uid)
    last_s  = doc.get("last_search")
    now     = datetime.utcnow()
    cooldown = 3600  # 1 hour

    if last_s:
        if isinstance(last_s, str):
            last_s = datetime.fromisoformat(last_s)
        elapsed = (now - last_s).total_seconds()
        if elapsed < cooldown:
            remaining = cooldown - elapsed
            h, r = divmod(int(remaining), 3600)
            mins = r // 60
            await m.reply(f"🔭 **Searching again in {h}h {mins}m...**")
            return

    if await _has_all_balls(uid):
        await m.reply("✨ You have ALL 7 Dragon Balls! Use `/wish` to summon Shenron!")
        return

    found_ball = None
    if random.random() < 0.45:
        owned  = set(doc.get("balls", []))
        needed = [i for i in range(1, 8) if i not in owned]
        if needed:
            found_ball = random.choice(needed)

    await _col("db_dragon_balls").update_one(
        {"user_id": uid},
        {"$set": {"last_search": now}},
    )

    if found_ball:
        await _col("db_dragon_balls").update_one(
            {"user_id": uid},
            {"$addToSet": {"balls": found_ball}, "$inc": {"total_collected": 1}},
        )
        ball_name  = BALL_NAMES[found_ball - 1]
        ball_stars = DRAGON_BALLS[found_ball - 1]
        owned_now  = set(doc.get("balls", [])) | {found_ball}
        progress   = f"{len(owned_now)}/7"

        await m.reply(
            f"🐉 **Dragon Ball Found!**\n\n"
            f"{ball_stars} **{ball_name} Ball** (#{found_ball})\n\n"
            f"📦 Collection: `{progress}`\n"
            + ("✨ You have all 7! Use `/wish` now!" if len(owned_now) >= 7 else "🔭 Keep searching!")
        )
    else:
        await m.reply(
            "🌌 *You searched but found nothing this time...*\n"
            "🔭 Try again in 1 hour!"
        )


@_soul.app.on_message(filters.command(["dragonballs", "myballs", "balls"]))
async def my_balls(_, m: Message):
    target = m.reply_to_message.from_user if m.reply_to_message else m.from_user
    doc    = await _get_user_balls(target.id)
    owned  = sorted(set(doc.get("balls", [])))

    ball_display = []
    for i in range(1, 8):
        if i in owned:
            ball_display.append(f"✅ {DRAGON_BALLS[i-1]} {BALL_NAMES[i-1]}")
        else:
            ball_display.append(f"❌ _{BALL_NAMES[i-1]}_")

    total = doc.get("total_collected", 0)
    ready = "✨ **ALL BALLS COLLECTED! Use `/wish`!**" if len(owned) >= 7 else f"📦 `{len(owned)}/7` collected"

    await m.reply(
        f"🐉 **{target.first_name}'s Dragon Balls**\n\n"
        + "\n".join(ball_display)
        + f"\n\n{ready}\n🎯 Total ever found: `{total}`"
    )


# ── Wish / Shenron ────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command("wish"))
async def wish_cmd(_, m: Message):
    uid = m.from_user.id
    doc = await _get_user_balls(uid)

    if not await _has_all_balls(uid):
        owned = len(set(doc.get("balls", [])))
        await m.reply(f"❌ You need all 7 Dragon Balls! You have `{owned}/7`.")
        return

    # Check cooldown
    last_wish_doc = await _col("db_wishes").find_one(
        {"user_id": uid}, sort=[("wished_at", -1)]
    )
    if last_wish_doc:
        elapsed = (datetime.utcnow() - last_wish_doc["wished_at"]).total_seconds()
        if elapsed < WISH_COOLDOWN:
            days  = int((WISH_COOLDOWN - elapsed) // 86400)
            hours = int(((WISH_COOLDOWN - elapsed) % 86400) // 3600)
            await m.reply(f"⏳ Dragon Balls recharging for **{days}d {hours}h**.")
            return

    buttons = IKM([
        [IKB(v["label"], callback_data=f"wish:{uid}:{k}")] for k, v in WISHES.items()
    ])

    await m.reply(
        "🐉 **SHENRON AWAKENS!** 🐉\n\n"
        "*'I will grant you one wish...'*\n\n"
        "Choose your wish:",
        reply_markup=buttons,
    )


@_soul.app.on_callback_query(filters.regex(r"^wish:(\d+):(\w+)$"))
async def wish_cb(client, cq: CallbackQuery):
    _, uid, wish_key = cq.data.split(":")
    uid = int(uid)

    if cq.from_user.id != uid:
        await cq.answer("This isn't your wish!", show_alert=True)
        return

    wish = WISHES.get(wish_key)
    if not wish:
        await cq.answer("Unknown wish.", show_alert=True)
        return

    # Consume balls
    await _col("db_dragon_balls").update_one(
        {"user_id": uid},
        {"$set": {"balls": []}},
    )

    # Grant wish
    result = ""
    if wish_key == "kakera":
        await add_balance(uid, 10_000)
        result = "💰 **10,000 kakera** added to your balance!"
    elif wish_key == "xp":
        await add_xp(uid, 2_000)
        result = "⭐ **2,000 XP** gained!"
    elif wish_key == "revive":
        result = "♻️ Your most recently burned character has been noted for manual review by admins."
    elif wish_key == "immunity":
        result = "🛡 **1-week immunity** granted (enforced by admins)."
    elif wish_key == "reroll":
        result = "🎲 Your next `/daily` will yield **double** kakera!"

    await _col("db_wishes").insert_one({
        "user_id":   uid,
        "wish":      wish_key,
        "wished_at": datetime.utcnow(),
    })

    await cq.message.edit_text(
        f"🌟 **Wish Granted!**\n\n{result}\n\n"
        "*'Until we meet again...'* — Shenron 🐉\n\n"
        "_Dragon Balls will return in 7 days._"
    )


# ── Power Level & Battle ──────────────────────────────────────────────────────

async def _get_or_create_fighter(uid: int, name: str) -> dict:
    doc = await _col("db_tournament").find_one({"user_id": uid})
    if not doc:
        power = random.randint(800, 3000)
        move  = random.choice(SIGNATURE_MOVES)
        char  = random.choice(DB_CHARACTERS)
        doc   = {
            "user_id":        uid,
            "display_name":   name,
            "character":      char["name"],
            "power_level":    power,
            "signature_move": move,
            "wins":           0,
            "losses":         0,
            "draws":          0,
            "total_battles":  0,
            "created_at":     datetime.utcnow(),
        }
        await _col("db_tournament").insert_one(doc)
    return doc


@_soul.app.on_message(filters.command(["powerlevel", "pl", "power"]))
async def power_level_cmd(_, m: Message):
    target = m.reply_to_message.from_user if m.reply_to_message else m.from_user
    fighter = await _get_or_create_fighter(target.id, target.first_name)
    pl      = fighter["power_level"]
    tier    = _get_power_tier(pl)
    char    = fighter["character"]
    move    = fighter["signature_move"]
    w, l, d = fighter["wins"], fighter["losses"], fighter["draws"]
    pct     = int(w / max(w + l + d, 1) * 100)

    stars = "⭐" * min(int(pl / 1000), 10)
    await m.reply(
        f"💪 **{target.first_name}'s Power Level**\n\n"
        f"🐉 Character: **{char}**\n"
        f"⚡ Power Level: `{pl:,}` {stars}\n"
        f"🏆 Tier: **{tier}**\n"
        f"🥊 Move: **{move}**\n\n"
        f"📊 W/L/D: `{w}/{l}/{d}` ({pct}% win rate)"
    )


@_soul.app.on_message(filters.command(["battle", "fight", "duel"]))
async def battle_cmd(_, m: Message):
    if not m.reply_to_message:
        await m.reply("↩️ Reply to a user to challenge them!")
        return

    challenger = m.from_user
    opponent   = m.reply_to_message.from_user

    if opponent.id == challenger.id:
        await m.reply("❌ Can't battle yourself!")
        return
    if opponent.is_bot:
        await m.reply("❌ Can't battle a bot.")
        return

    c_fighter = await _get_or_create_fighter(challenger.id, challenger.first_name)
    o_fighter = await _get_or_create_fighter(opponent.id, opponent.first_name)

    c_roll = c_fighter["power_level"] * random.uniform(0.8, 1.2)
    o_roll = o_fighter["power_level"] * random.uniform(0.8, 1.2)

    if c_roll > o_roll * 1.05:
        winner, loser = c_fighter, o_fighter
        w_uid, l_uid  = challenger.id, opponent.id
        w_name, l_name = challenger.first_name, opponent.first_name
        outcome = "wins"
    elif o_roll > c_roll * 1.05:
        winner, loser = o_fighter, c_fighter
        w_uid, l_uid  = opponent.id, challenger.id
        w_name, l_name = opponent.first_name, challenger.first_name
        outcome = "wins"
    else:
        winner = loser = None
        outcome = "draw"

    kakera_bet  = 120
    xp_win      = 80
    xp_loss     = 20

    # Update records
    if outcome == "wins":
        await _col("db_tournament").update_one({"user_id": w_uid}, {"$inc": {"wins": 1, "total_battles": 1}})
        await _col("db_tournament").update_one({"user_id": l_uid}, {"$inc": {"losses": 1, "total_battles": 1}})
        # Transfer kakera
        if await deduct_balance(l_uid, kakera_bet):
            await add_balance(w_uid, kakera_bet)
        await add_xp(w_uid, xp_win)
        await add_xp(l_uid, xp_loss)
        await add_balance(w_uid, 120)

        move_used = winner["signature_move"]
        await m.reply(
            f"⚡ **BATTLE!** ⚡\n\n"
            f"**{challenger.first_name}** ({c_fighter['character']} `{int(c_roll):,}`) VS "
            f"**{opponent.first_name}** ({o_fighter['character']} `{int(o_roll):,}`)\n\n"
            f"💥 **{w_name}** uses **{move_used}**!\n\n"
            f"🏆 **{w_name} wins!**\n"
            f"💰 +{kakera_bet} kakera | ⭐ +{xp_win} XP\n"
            f"😔 {l_name}: ⭐ +{xp_loss} XP"
        )
    else:
        for uid in (challenger.id, opponent.id):
            await _col("db_tournament").update_one({"user_id": uid}, {"$inc": {"draws": 1, "total_battles": 1}})
            await add_xp(uid, 30)
            await add_balance(uid, 30)

        await m.reply(
            f"⚡ **BATTLE — DRAW!** ⚡\n\n"
            f"**{challenger.first_name}** ({c_fighter['character']} `{int(c_roll):,}`) VS "
            f"**{opponent.first_name}** ({o_fighter['character']} `{int(o_roll):,}`)\n\n"
            f"🤝 Perfectly matched! Both earn ⭐ +30 XP and 💰 +30 kakera."
        )


# ── /dbtop — Tournament Leaderboard ──────────────────────────────────────────

@_soul.app.on_message(filters.command(["dbtop", "battletop", "tourney"]))
async def db_top_cmd(_, m: Message):
    fighters = (
        await _col("db_tournament")
        .find({})
        .sort("wins", -1)
        .limit(10)
        .to_list(10)
    )

    if not fighters:
        await m.reply("🏆 No battles recorded yet. Use `/battle` to fight!")
        return

    lines = []
    for i, f in enumerate(fighters, 1):
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"`{i}.`")
        uid   = f["user_id"]
        name  = f.get("display_name", str(uid))
        w     = f.get("wins", 0)
        pl    = f.get("power_level", 0)
        lines.append(
            f"{medal} [{name}](tg://user?id={uid})\n"
            f"     ⚡ PL: `{pl:,}` | 🏆 Wins: `{w}`"
        )

    await m.reply(
        "⚡ **Dragon Ball Tournament Leaderboard** ⚡\n\n" + "\n\n".join(lines),
        disable_web_page_preview=True,
    )


# ── /dbchar — Random DB Character Spawn ──────────────────────────────────────

@_soul.app.on_message(filters.command(["dbchar", "dbspawn"]) & filters.group)
async def db_char_spawn(_, m: Message):
    char = random.choice(DB_CHARACTERS)
    pl   = char["power"]
    tier = _get_power_tier(pl)

    from SoulCatcher.rarity import rarity_display
    r_str = rarity_display(char["rarity"])

    await m.reply(
        f"🐉 **Dragon Ball Character Appeared!**\n\n"
        f"👤 **{char['name']}**\n"
        f"📺 *{char['anime']}*\n"
        f"✨ {r_str}\n"
        f"⚡ Power Level: `{pl:,}`\n"
        f"🏆 Tier: **{tier}**\n\n"
        f"💡 Use `/battle` to challenge other players!"
    )
