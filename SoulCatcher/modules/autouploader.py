"""SoulCatcher/modules/dragonball.py — Full Dragon Ball Fighting Game."""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument

import SoulCatcher as _soul
from pyrogram import filters
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup as IKM,
    InlineKeyboardButton as IKB,
    CallbackQuery,
)

from SoulCatcher.config import MONGO_URI, DB_NAME
from SoulCatcher.database import (
    get_or_create_user,
    get_balance,
    add_balance,
    deduct_balance,
    add_xp,
    add_to_harem,
    get_random_character,
)

log = logging.getLogger("SoulCatcher.dragonball")

# ── DB ────────────────────────────────────────────────────────────────────────

_db = None


async def init_db():
    global _db
    client = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=10_000)
    _db = client[DB_NAME]
    await _db["db_dragon_balls"].create_index([("user_id", 1)], unique=True)
    await _db["db_wishes"].create_index([("user_id", 1), ("wished_at", -1)])
    await _db["db_fighters"].create_index([("user_id", 1)], unique=True)
    await _db["db_fights"].create_index([("fight_id", 1)], unique=True)
    await _db["db_upgrades"].create_index([("user_id", 1)], unique=True)
    log.info("✅ DragonBall DB ready")


def _col(name: str):
    return _db[name]


# ── Characters & Videos ───────────────────────────────────────────────────────

FIGHTERS = {
    "goku": {
        "name": "Goku",
        "emoji": "🟡",
        "team": "hero",
        "base_power": 9000,
        "signature": "Kamehameha",
        "videos": {
            "idle":      "https://litter.catbox.moe/so5rk7.mp4",
            "transform": "https://litter.catbox.moe/3ga3cf.mp4",
            "attack":    "https://litter.catbox.moe/ghw0y2.mp4",
            "win":       "https://litter.catbox.moe/zjukij.mp4",
            "lose":      "https://litter.catbox.moe/l999rw.mp4",
        },
        "transform_name": "Ultra Instinct",
        "transform_power": 1.6,
        "hp": 100,
        "desc": "The legendary Super Saiyan warrior",
    },
    "vegeta": {
        "name": "Vegeta",
        "emoji": "🔵",
        "team": "hero",
        "base_power": 8500,
        "signature": "Final Flash",
        "videos": {
            "idle":      "https://litter.catbox.moe/snqtio.mp4",
            "transform": "https://litter.catbox.moe/almlg0.mp4",
            "attack":    "https://litter.catbox.moe/lrsn2j.mp4",
            "win":       None,
            "lose":      "https://litter.catbox.moe/xpjjpx.mp4",
        },
        "transform_name": "Ultra Ego",
        "transform_power": 1.5,
        "hp": 95,
        "desc": "Prince of all Saiyans, rival to Goku",
    },
    "broly": {
        "name": "Broly",
        "emoji": "🟢",
        "team": "hero",
        "base_power": 9200,
        "signature": "Gigantic Roar",
        "videos": {
            "idle":      "https://litter.catbox.moe/i3pshj.mp4",
            "transform": "https://litter.catbox.moe/tqy9md.mp4",
            "attack":    "https://litter.catbox.moe/wr191h.mp4",
            "win":       None,
            "lose":      "https://litter.catbox.moe/wr191h.mp4",
        },
        "transform_name": "Legendary Super Saiyan",
        "transform_power": 1.7,
        "hp": 110,
        "desc": "The Legendary Super Saiyan, pure raw power",
    },
    "vegito": {
        "name": "Vegito",
        "emoji": "⚡",
        "team": "hero",
        "base_power": 9800,
        "signature": "Spirit Sword",
        "videos": {
            "idle":      "https://litter.catbox.moe/b9hty6.mp4",
            "transform": None,
            "attack":    "https://litter.catbox.moe/cqxu9f.mp4",
            "win":       None,
            "lose":      "https://litter.catbox.moe/9rk2cf.mp4",
        },
        "transform_name": "Super Saiyan Blue",
        "transform_power": 1.8,
        "hp": 105,
        "desc": "The fusion of Goku and Vegeta, unstoppable",
    },
    "goku_black": {
        "name": "Goku Black",
        "emoji": "😈",
        "team": "villain",
        "base_power": 9100,
        "signature": "Black Kamehameha",
        "videos": {
            "idle":      "https://litter.catbox.moe/itlrio.mp4",
            "transform": None,
            "attack":    "https://litter.catbox.moe/qfbkk2.mp4",
            "win":       "https://litter.catbox.moe/tyxxbd.mp4",
            "lose":      None,
        },
        "transform_name": "Super Saiyan Rosé",
        "transform_power": 1.55,
        "hp": 98,
        "desc": "Zamasu in Goku's body, corrupted god",
    },
    "cell": {
        "name": "Cell",
        "emoji": "🟢",
        "team": "villain",
        "base_power": 7500,
        "signature": "Solar Kamehameha",
        "videos": {
            "idle":      "https://litter.catbox.moe/m7j5xf.mp4",
            "transform": None,
            "attack":    "https://litter.catbox.moe/3mj3gc.mp4",
            "win":       None,
            "lose":      "https://litter.catbox.moe/l1d2fl.mp4",
        },
        "transform_name": "Perfect Form",
        "transform_power": 1.4,
        "hp": 90,
        "desc": "The perfect being, absorber of power",
    },
    # ── Text-only fighters (no videos yet) ───────────────────────────────────
    "gohan": {
        "name": "Gohan",
        "emoji": "🟣",
        "team": "hero",
        "base_power": 8800,
        "signature": "Masenko",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
        "transform_name": "Beast Mode",
        "transform_power": 1.65,
        "hp": 100,
        "desc": "Son of Goku, hidden power unleashed",
    },
    "trunks": {
        "name": "Future Trunks",
        "emoji": "🔵",
        "team": "hero",
        "base_power": 7800,
        "signature": "Burning Attack",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
        "transform_name": "Super Saiyan Rage",
        "transform_power": 1.5,
        "hp": 90,
        "desc": "Warrior from the future who slays gods",
    },
    "piccolo": {
        "name": "Piccolo",
        "emoji": "🟢",
        "team": "hero",
        "base_power": 6000,
        "signature": "Special Beam Cannon",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
        "transform_name": "Orange Piccolo",
        "transform_power": 1.4,
        "hp": 85,
        "desc": "The Namekian warrior, Gohan's mentor",
    },
    "krillin": {
        "name": "Krillin",
        "emoji": "⚪",
        "team": "hero",
        "base_power": 3000,
        "signature": "Destructo Disc",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
        "transform_name": "Unlocked Potential",
        "transform_power": 1.3,
        "hp": 70,
        "desc": "Earth's strongest human, surprisingly lethal",
    },
    "android17": {
        "name": "Android 17",
        "emoji": "🔵",
        "team": "hero",
        "base_power": 7000,
        "signature": "Power Blitz",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
        "transform_name": "Barrier Overload",
        "transform_power": 1.35,
        "hp": 88,
        "desc": "Infinite energy android, Tournament MVP",
    },
    "bardock": {
        "name": "Bardock",
        "emoji": "🟠",
        "team": "hero",
        "base_power": 5500,
        "signature": "Riot Javelin",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
        "transform_name": "Super Saiyan",
        "transform_power": 1.4,
        "hp": 80,
        "desc": "Low-class warrior, father of Goku",
    },
    "gogeta": {
        "name": "Gogeta",
        "emoji": "🔴",
        "team": "hero",
        "base_power": 9900,
        "signature": "True Kamehameha",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
        "transform_name": "Super Saiyan Blue",
        "transform_power": 1.9,
        "hp": 110,
        "desc": "The mightiest fusion, Goku and Vegeta as one",
    },
    "frieza": {
        "name": "Frieza",
        "emoji": "❄️",
        "team": "villain",
        "base_power": 8800,
        "signature": "Death Ball",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
        "transform_name": "Black Frieza",
        "transform_power": 1.6,
        "hp": 95,
        "desc": "The galactic emperor, eternal rival",
    },
    "jiren": {
        "name": "Jiren",
        "emoji": "🔴",
        "team": "villain",
        "base_power": 9500,
        "signature": "Power Impact",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
        "transform_name": "Limit Breaker",
        "transform_power": 1.7,
        "hp": 105,
        "desc": "The Pride Trooper who surpassed gods",
    },
    "beerus": {
        "name": "Beerus",
        "emoji": "💜",
        "team": "villain",
        "base_power": 9900,
        "signature": "Hakai",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
        "transform_name": "God of Destruction",
        "transform_power": 1.8,
        "hp": 115,
        "desc": "The God of Destruction, destroyer of worlds",
    },
    "majin_buu": {
        "name": "Majin Buu",
        "emoji": "🩷",
        "team": "villain",
        "base_power": 7800,
        "signature": "Chocolate Beam",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
        "transform_name": "Pure Evil Buu",
        "transform_power": 1.5,
        "hp": 120,
        "desc": "The magical monster, can absorb anything",
    },
    "zamasu": {
        "name": "Fused Zamasu",
        "emoji": "⚡",
        "team": "villain",
        "base_power": 8500,
        "signature": "Holy Wrath",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
        "transform_name": "Divine Overflow",
        "transform_power": 1.55,
        "hp": 100,
        "desc": "Immortal god of justice, half-corrupted",
    },
    "janemba": {
        "name": "Janemba",
        "emoji": "👹",
        "team": "villain",
        "base_power": 8200,
        "signature": "Hell Gate",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
        "transform_name": "Super Janemba",
        "transform_power": 1.5,
        "hp": 95,
        "desc": "The demon of pure evil energy",
    },
    "turles": {
        "name": "Turles",
        "emoji": "🌿",
        "team": "villain",
        "base_power": 5000,
        "signature": "Power Ball",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
        "transform_name": "Tree of Might Boost",
        "transform_power": 1.45,
        "hp": 78,
        "desc": "Goku's dark mirror, powered by evil fruit",
    },
    "raditz": {
        "name": "Raditz",
        "emoji": "⚫",
        "team": "villain",
        "base_power": 1500,
        "signature": "Double Sunday",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
        "transform_name": "Great Ape",
        "transform_power": 1.3,
        "hp": 65,
        "desc": "Goku's evil brother, first true villain",
    },
    "nappa": {
        "name": "Nappa",
        "emoji": "🟤",
        "team": "villain",
        "base_power": 4000,
        "signature": "Bomber DX",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
        "transform_name": "Full Power",
        "transform_power": 1.3,
        "hp": 85,
        "desc": "Vegeta's brutal elite soldier",
    },
    "android21": {
        "name": "Android 21",
        "emoji": "🩷",
        "team": "villain",
        "base_power": 8000,
        "signature": "Conquer the World",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
        "transform_name": "Majin Form",
        "transform_power": 1.55,
        "hp": 92,
        "desc": "The hungry researcher who absorbs power",
    },
    "baby_vegeta": {
        "name": "Baby Vegeta",
        "emoji": "👶",
        "team": "villain",
        "base_power": 7200,
        "signature": "Revenge Death Ball",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
        "transform_name": "Golden Great Ape",
        "transform_power": 1.5,
        "hp": 90,
        "desc": "Tuffle parasite controlling Vegeta's body",
    },
    "whis": {
        "name": "Whis",
        "emoji": "⚪",
        "team": "neutral",
        "base_power": 9999,
        "signature": "Staff Strike",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
        "transform_name": "Angel Mode",
        "transform_power": 2.0,
        "hp": 999,
        "desc": "The angelic attendant, beyond all gods",
    },
    "zeno": {
        "name": "Zeno",
        "emoji": "👑",
        "team": "neutral",
        "base_power": 99999,
        "signature": "Erase",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
        "transform_name": "True Form",
        "transform_power": 3.0,
        "hp": 9999,
        "desc": "The Omni-King, erases universes for fun",
    },
    "hit": {
        "name": "Hit",
        "emoji": "🕐",
        "team": "neutral",
        "base_power": 8000,
        "signature": "Time Skip",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
        "transform_name": "Pure Progress",
        "transform_power": 1.6,
        "hp": 90,
        "desc": "The legendary assassin who skips time",
    },
    "vegito_blue": {
        "name": "Vegito Blue",
        "emoji": "🔵",
        "team": "neutral",
        "base_power": 9950,
        "signature": "Final Kamehameha",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
        "transform_name": "SSB Kaioken",
        "transform_power": 2.0,
        "hp": 108,
        "desc": "The ultimate fusion at peak godly power",
    },
    "kefla": {
        "name": "Kefla",
        "emoji": "💚",
        "team": "neutral",
        "base_power": 8200,
        "signature": "Blaster Meteor",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
        "transform_name": "Super Saiyan 2",
        "transform_power": 1.5,
        "hp": 95,
        "desc": "Universe 6's female Saiyan fusion",
    },
    "toppo": {
        "name": "Toppo",
        "emoji": "🟠",
        "team": "neutral",
        "base_power": 8800,
        "signature": "Hakai Blast",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
        "transform_name": "God of Destruction Mode",
        "transform_power": 1.65,
        "hp": 100,
        "desc": "Pride Trooper leader who embraced destruction",
    },
    "grand_priest": {
        "name": "Grand Priest",
        "emoji": "✨",
        "team": "neutral",
        "base_power": 99990,
        "signature": "Divine Strike",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
        "transform_name": "True Divinity",
        "transform_power": 2.5,
        "hp": 5000,
        "desc": "Father of angels, second only to Zeno",
    },
}

# ── Dragon Balls ──────────────────────────────────────────────────────────────

DRAGON_BALLS  = ["⭐", "⭐⭐", "⭐⭐⭐", "⭐⭐⭐⭐", "⭐⭐⭐⭐⭐", "⭐⭐⭐⭐⭐⭐", "⭐⭐⭐⭐⭐⭐⭐"]
BALL_NAMES    = ["One-Star", "Two-Star", "Three-Star", "Four-Star", "Five-Star", "Six-Star", "Seven-Star"]
TOTAL_BALLS   = 7
WISH_COOLDOWN = 7 * 24 * 3600

WISHES = {
    "kakera":   {"label": "💰 10,000 Kakera",     "desc": "Receive 10,000 kakera instantly."},
    "xp":       {"label": "⭐ 2,000 XP",          "desc": "Gain 2,000 XP toward your level."},
    "power":    {"label": "⚡ Power Level +500",   "desc": "Boost your fighter's power level."},
    "immunity": {"label": "🛡 1-week immunity",    "desc": "Cannot be gbanned for 7 days."},
    "reroll":   {"label": "🎲 Daily reroll ×2",    "desc": "Double kakera from your next daily."},
}

# ── Power Upgrade Costs ───────────────────────────────────────────────────────
# Each tier costs more — upgrades are additive power boosts

UPGRADE_TIERS = [
    {"tier": 1, "boost": 500,   "cost": 1_000,   "label": "Tier I — Awakening"},
    {"tier": 2, "boost": 1500,  "cost": 5_000,   "label": "Tier II — Super Saiyan"},
    {"tier": 3, "boost": 4000,  "cost": 15_000,  "label": "Tier III — Super Saiyan 2"},
    {"tier": 4, "boost": 10000, "cost": 40_000,  "label": "Tier IV — Super Saiyan 3"},
    {"tier": 5, "boost": 25000, "cost": 100_000, "label": "Tier V — Super Saiyan God"},
    {"tier": 6, "boost": 60000, "cost": 300_000, "label": "Tier VI — Ultra Instinct"},
]

POWER_TIERS = [
    (0,     2000,   "Earthling"),
    (2001,  6000,   "Saiyan"),
    (6001,  12000,  "Super Saiyan"),
    (12001, 25000,  "Super Saiyan 2"),
    (25001, 50000,  "Super Saiyan 3"),
    (50001, 100000, "Super Saiyan God"),
    (100001,250000, "Ultra Instinct"),
    (250001,999999, "Omni-God"),
]


def _power_tier_name(pl: int) -> str:
    for lo, hi, name in POWER_TIERS:
        if lo <= pl <= hi:
            return name
    return "Beyond Mortal"


# ── Active fights tracker (in-memory) ─────────────────────────────────────────

_active_fights: dict[str, dict] = {}  # fight_id -> state


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _send_video_or_text(m: Message, url: str | None, caption: str):
    """Send video if URL available, else send text."""
    if url:
        try:
            await m.reply_video(url, caption=caption)
            return
        except Exception:
            pass
    await m.reply(caption)


async def _get_user_balls(uid: int) -> dict:
    doc = await _col("db_dragon_balls").find_one({"user_id": uid})
    if not doc:
        doc = {"user_id": uid, "balls": [], "total_collected": 0, "last_search": None}
        await _col("db_dragon_balls").insert_one(doc)
    return doc


async def _has_all_balls(uid: int) -> bool:
    doc = await _get_user_balls(uid)
    return len(set(doc.get("balls", []))) >= TOTAL_BALLS


async def _get_fighter(uid: int) -> dict | None:
    return await _col("db_fighters").find_one({"user_id": uid})


async def _get_upgrades(uid: int) -> dict:
    doc = await _col("db_upgrades").find_one({"user_id": uid})
    if not doc:
        doc = {"user_id": uid, "tier": 0, "bonus_power": 0, "total_spent": 0}
        await _col("db_upgrades").insert_one(doc)
    return doc


def _fighter_power(fighter: dict, upgrades: dict) -> int:
    key     = fighter.get("character_key", "goku")
    char    = FIGHTERS.get(key, FIGHTERS["goku"])
    base    = char["base_power"]
    bonus   = upgrades.get("bonus_power", 0)
    pl_bonus = fighter.get("pl_bonus", 0)
    return base + bonus + pl_bonus


def _build_fight_hp_bar(hp: int, max_hp: int = 100) -> str:
    filled = max(0, min(10, int(hp / max_hp * 10)))
    return "🟩" * filled + "⬛" * (10 - filled)


# ── Character Select ──────────────────────────────────────────────────────────

def _char_select_keyboard(page: int = 0, fight_id: str = "") -> IKM:
    keys     = list(FIGHTERS.keys())
    per_page = 8
    start    = page * per_page
    chunk    = keys[start:start + per_page]
    rows     = []

    # Two buttons per row
    for i in range(0, len(chunk), 2):
        row = []
        for key in chunk[i:i+2]:
            c = FIGHTERS[key]
            row.append(IKB(
                f"{c['emoji']} {c['name']}",
                callback_data=f"dbpick:{fight_id}:{key}"
            ))
        rows.append(row)

    # Navigation
    nav = []
    if page > 0:
        nav.append(IKB("◀️ Prev", callback_data=f"dbpage:{fight_id}:{page-1}"))
    if start + per_page < len(keys):
        nav.append(IKB("Next ▶️", callback_data=f"dbpage:{fight_id}:{page+1}"))
    if nav:
        rows.append(nav)

    return IKM(rows)


def _action_keyboard(fight_id: str, uid: int) -> IKM:
    return IKM([[
        IKB("⚔️ Attack",    callback_data=f"dbact:{fight_id}:{uid}:attack"),
        IKB("💥 Special",   callback_data=f"dbact:{fight_id}:{uid}:special"),
        IKB("🌀 Transform", callback_data=f"dbact:{fight_id}:{uid}:transform"),
    ]])


# ── /dbfight Command ──────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command(["dbfight", "dragonbattle", "dbattle"]))
async def dbfight_cmd(_, m: Message):
    if not m.reply_to_message:
        await m.reply(
            "⚔️ **Dragon Ball Fight!**\n\n"
            "Reply to someone to challenge them!\n"
            "Usage: `/dbfight` (reply to opponent)"
        )
        return

    challenger = m.from_user
    opponent   = m.reply_to_message.from_user

    if opponent.id == challenger.id:
        await m.reply("❌ You can't fight yourself!")
        return
    if opponent.is_bot:
        await m.reply("❌ Bots don't fight!")
        return

    await get_or_create_user(challenger.id, challenger.username or "", challenger.first_name or "", challenger.last_name or "")
    await get_or_create_user(opponent.id, opponent.username or "", opponent.first_name or "", opponent.last_name or "")

    fight_id = f"{challenger.id}_{opponent.id}_{int(datetime.utcnow().timestamp())}"

    _active_fights[fight_id] = {
        "challenger_id":   challenger.id,
        "challenger_name": challenger.first_name,
        "opponent_id":     opponent.id,
        "opponent_name":   opponent.first_name,
        "chat_id":         m.chat.id,
        "state":           "picking_challenger",
        "challenger_char": None,
        "opponent_char":   None,
        "challenger_hp":   100,
        "opponent_hp":     100,
        "challenger_transformed": False,
        "opponent_transformed":   False,
        "turn":            challenger.id,
        "round":           1,
        "created_at":      datetime.utcnow(),
    }

    await m.reply_video(
        FIGHTERS["goku"]["videos"]["idle"],
        caption=(
            f"🐉 **DRAGON BALL FIGHT CHALLENGE!** 🐉\n\n"
            f"⚔️ **{challenger.first_name}** challenges **{opponent.first_name}**!\n\n"
            f"**{challenger.first_name}** — pick your fighter:"
        ),
        reply_markup=_char_select_keyboard(page=0, fight_id=f"{fight_id}:c"),
    )


# ── Page navigation ───────────────────────────────────────────────────────────

@_soul.app.on_callback_query(filters.regex(r"^dbpage:(.+):(\d+)$"))
async def dbpage_cb(_, cq: CallbackQuery):
    parts   = cq.data.split(":")
    fight_id_raw = ":".join(parts[1:-1])
    page    = int(parts[-1])

    # Determine who is picking
    fight_id = fight_id_raw.rsplit(":", 1)[0] if ":" in fight_id_raw else fight_id_raw
    suffix   = fight_id_raw.rsplit(":", 1)[1] if ":" in fight_id_raw else "c"

    state = _active_fights.get(fight_id)
    if not state:
        await cq.answer("Fight expired!", show_alert=True)
        return

    picking_uid = state["challenger_id"] if suffix == "c" else state["opponent_id"]
    if cq.from_user.id != picking_uid:
        await cq.answer("It's not your turn to pick!", show_alert=True)
        return

    await cq.message.edit_reply_markup(
        reply_markup=_char_select_keyboard(page=page, fight_id=f"{fight_id}:{suffix}")
    )
    await cq.answer()


# ── Character picked ──────────────────────────────────────────────────────────

@_soul.app.on_callback_query(filters.regex(r"^dbpick:(.+):(\w+)$"))
async def dbpick_cb(_, cq: CallbackQuery):
    parts    = cq.data.split(":")
    char_key = parts[-1]
    fight_id_raw = ":".join(parts[1:-1])

    fight_id = fight_id_raw.rsplit(":", 1)[0] if ":" in fight_id_raw else fight_id_raw
    suffix   = fight_id_raw.rsplit(":", 1)[1] if ":" in fight_id_raw else "c"

    state = _active_fights.get(fight_id)
    if not state:
        await cq.answer("Fight expired!", show_alert=True)
        return

    char = FIGHTERS.get(char_key)
    if not char:
        await cq.answer("Unknown character!", show_alert=True)
        return

    if suffix == "c":
        # Challenger picking
        if cq.from_user.id != state["challenger_id"]:
            await cq.answer("You're not the challenger!", show_alert=True)
            return
        state["challenger_char"] = char_key
        state["challenger_hp"]   = char["hp"]
        state["state"]           = "picking_opponent"

        upgrades = await _get_upgrades(state["challenger_id"])
        c_power  = char["base_power"] + upgrades.get("bonus_power", 0)

        await cq.message.edit_caption(
            caption=(
                f"✅ **{state['challenger_name']}** chose **{char['emoji']} {char['name']}**!\n"
                f"⚡ Power: `{c_power:,}` | 💪 Signature: **{char['signature']}**\n\n"
                f"Now **{state['opponent_name']}** — pick your fighter:"
            ),
            reply_markup=_char_select_keyboard(page=0, fight_id=f"{fight_id}:o"),
        )
        await cq.answer(f"You chose {char['name']}!")

    else:
        # Opponent picking
        if cq.from_user.id != state["opponent_id"]:
            await cq.answer("You're not the opponent!", show_alert=True)
            return
        state["opponent_char"] = char_key
        state["opponent_hp"]   = char["hp"]
        state["state"]         = "fighting"

        c_char = FIGHTERS[state["challenger_char"]]
        o_char = FIGHTERS[char_key]

        c_upg  = await _get_upgrades(state["challenger_id"])
        o_upg  = await _get_upgrades(state["opponent_id"])
        c_pow  = c_char["base_power"] + c_upg.get("bonus_power", 0)
        o_pow  = o_char["base_power"] + o_upg.get("bonus_power", 0)

        c_bar  = _build_fight_hp_bar(state["challenger_hp"], c_char["hp"])
        o_bar  = _build_fight_hp_bar(state["opponent_hp"],   o_char["hp"])

        await cq.answer(f"You chose {o_char['name']}! Fight starts!")

        # Send idle videos for both fighters
        try:
            if c_char["videos"]["idle"]:
                await cq.message.reply_video(
                    c_char["videos"]["idle"],
                    caption=f"🟡 **{state['challenger_name']}** enters as **{c_char['name']}**!"
                )
            if o_char["videos"]["idle"]:
                await cq.message.reply_video(
                    o_char["videos"]["idle"],
                    caption=f"🔴 **{state['opponent_name']}** enters as **{o_char['name']}**!"
                )
        except Exception:
            pass

        await cq.message.edit_caption(
            caption=(
                f"⚡ **ROUND 1 — FIGHT!** ⚡\n\n"
                f"{c_char['emoji']} **{state['challenger_name']}** ({c_char['name']}) `{c_pow:,}`\n"
                f"❤️ {c_bar} `{state['challenger_hp']}/{c_char['hp']}`\n\n"
                f"{o_char['emoji']} **{state['opponent_name']}** ({o_char['name']}) `{o_pow:,}`\n"
                f"❤️ {o_bar} `{state['opponent_hp']}/{o_char['hp']}`\n\n"
                f"⚔️ **{state['challenger_name']}'s turn!** Choose your move:"
            ),
            reply_markup=_action_keyboard(fight_id, state["challenger_id"]),
        )


# ── Fight Action ──────────────────────────────────────────────────────────────

@_soul.app.on_callback_query(filters.regex(r"^dbact:([^:]+):(\d+):(attack|special|transform)$"))
async def dbact_cb(_, cq: CallbackQuery):
    parts    = cq.data.split(":")
    action   = parts[-1]
    uid      = int(parts[-2])
    fight_id = ":".join(parts[1:-2])

    if cq.from_user.id != uid:
        await cq.answer("It's not your turn!", show_alert=True)
        return

    state = _active_fights.get(fight_id)
    if not state or state["state"] != "fighting":
        await cq.answer("Fight not found or already ended!", show_alert=True)
        return

    if state["turn"] != uid:
        await cq.answer("It's not your turn!", show_alert=True)
        return

    # Determine attacker/defender
    is_challenger = (uid == state["challenger_id"])
    atk_key  = state["challenger_char"] if is_challenger else state["opponent_char"]
    def_key  = state["opponent_char"]   if is_challenger else state["challenger_char"]
    atk_char = FIGHTERS[atk_key]
    def_char = FIGHTERS[def_key]

    atk_name = state["challenger_name"] if is_challenger else state["opponent_name"]
    def_name = state["opponent_name"]   if is_challenger else state["challenger_name"]
    def_uid  = state["opponent_id"]     if is_challenger else state["challenger_id"]

    atk_upg  = await _get_upgrades(uid)
    def_upg  = await _get_upgrades(def_uid)
    atk_pow  = atk_char["base_power"] + atk_upg.get("bonus_power", 0)
    def_pow  = def_char["base_power"] + def_upg.get("bonus_power", 0)

    atk_transformed = state["challenger_transformed"] if is_challenger else state["opponent_transformed"]

    narrative  = ""
    video_url  = None
    damage     = 0
    miss       = False

    if action == "transform":
        if atk_transformed:
            await cq.answer("Already transformed!", show_alert=True)
            return

        # Transform
        if is_challenger:
            state["challenger_transformed"] = True
        else:
            state["opponent_transformed"] = True

        boost  = atk_char["transform_power"]
        atk_pow = int(atk_pow * boost)
        video_url  = atk_char["videos"]["transform"]
        narrative  = (
            f"🌟 **{atk_name}** TRANSFORMS into **{atk_char['transform_name']}**!\n"
            f"⚡ Power surges to `{atk_pow:,}`! ×{boost}"
        )
        # No damage on transform
        damage = 0

    elif action == "special":
        # Special move — big damage, can miss 20%
        if random.random() < 0.20:
            miss      = True
            narrative = f"💨 **{atk_name}** fires **{atk_char['signature']}** but **{def_name} DODGES!**"
        else:
            mult   = random.uniform(1.5, 2.5)
            if atk_transformed:
                mult += 0.5
            damage = int(atk_pow / def_pow * mult * random.uniform(18, 30))
            damage = max(5, min(damage, 45))
            video_url  = atk_char["videos"]["attack"]
            narrative  = (
                f"💥 **{atk_name}** unleashes **{atk_char['signature']}**!\n"
                f"🔥 Critical hit! `-{damage} HP` to {def_name}!"
            )

    else:
        # Normal attack — miss 15%
        if random.random() < 0.15:
            miss      = True
            narrative = f"💨 **{atk_name}** attacks but **{def_name} DODGES!**"
        else:
            mult   = random.uniform(0.8, 1.3)
            if atk_transformed:
                mult += 0.3
            damage = int(atk_pow / def_pow * mult * random.uniform(10, 20))
            damage = max(3, min(damage, 30))
            narrative = (
                f"⚔️ **{atk_name}** attacks **{def_name}**!\n"
                f"💥 `-{damage} HP`"
            )

    # Apply damage
    if is_challenger:
        state["opponent_hp"]    = max(0, state["opponent_hp"] - damage)
    else:
        state["challenger_hp"]  = max(0, state["challenger_hp"] - damage)

    # Send video if available
    if video_url:
        try:
            await cq.message.reply_video(video_url, caption=narrative)
        except Exception:
            pass
    elif narrative:
        await cq.answer(narrative[:200], show_alert=False)

    # Check if fight is over
    c_hp = state["challenger_hp"]
    o_hp = state["opponent_hp"]

    c_char_data = FIGHTERS[state["challenger_char"]]
    o_char_data = FIGHTERS[state["opponent_char"]]

    if c_hp <= 0 or o_hp <= 0:
        # Fight over
        state["state"] = "ended"

        if c_hp <= 0 and o_hp <= 0:
            outcome = "draw"
        elif c_hp <= 0:
            outcome = "opponent_wins"
        else:
            outcome = "challenger_wins"

        await _resolve_fight(cq, state, fight_id, outcome, c_char_data, o_char_data)
        del _active_fights[fight_id]
        return

    # Swap turn
    state["turn"]  = def_uid
    state["round"] += 1

    c_bar = _build_fight_hp_bar(c_hp, c_char_data["hp"])
    o_bar = _build_fight_hp_bar(o_hp, o_char_data["hp"])

    next_name = def_name
    next_uid  = def_uid

    # Mid-fight transformation trigger at low HP
    def_transformed = state["opponent_transformed"] if is_challenger else state["challenger_transformed"]
    if not def_transformed:
        def_hp    = o_hp if is_challenger else c_hp
        def_maxhp = o_char_data["hp"] if is_challenger else c_char_data["hp"]
        if def_hp < def_maxhp * 0.35 and random.random() < 0.6:
            # Auto-transform defender
            if is_challenger:
                state["opponent_transformed"] = True
            else:
                state["challenger_transformed"] = True
            t_video = def_char["videos"]["transform"]
            t_caption = (
                f"😤 **{def_name}** won't go down that easy!\n"
                f"🌟 TRANSFORMS into **{def_char['transform_name']}**!"
            )
            if t_video:
                try:
                    await cq.message.reply_video(t_video, caption=t_caption)
                except Exception:
                    await cq.message.reply(t_caption)
            else:
                await cq.message.reply(t_caption)

    await cq.message.edit_caption(
        caption=(
            f"⚡ **ROUND {state['round']}** ⚡\n\n"
            f"{c_char_data['emoji']} **{state['challenger_name']}** "
            f"{'✨' if state['challenger_transformed'] else ''}"
            f"({c_char_data['name']})\n"
            f"❤️ {c_bar} `{c_hp}/{c_char_data['hp']}`\n\n"
            f"{o_char_data['emoji']} **{state['opponent_name']}** "
            f"{'✨' if state['opponent_transformed'] else ''}"
            f"({o_char_data['name']})\n"
            f"❤️ {o_bar} `{o_hp}/{o_char_data['hp']}`\n\n"
            f"⚔️ **{next_name}'s turn!** Choose your move:"
        ),
        reply_markup=_action_keyboard(fight_id, next_uid),
    )
    await cq.answer()


# ── Resolve Fight ─────────────────────────────────────────────────────────────

async def _resolve_fight(cq: CallbackQuery, state: dict, fight_id: str, outcome: str, c_char, o_char):
    c_name = state["challenger_name"]
    o_name = state["opponent_name"]
    c_uid  = state["challenger_id"]
    o_uid  = state["opponent_id"]

    kakera_reward = 300
    xp_reward     = 150
    kakera_loss   = 150

    if outcome == "challenger_wins":
        winner_uid, loser_uid     = c_uid, o_uid
        winner_name, loser_name   = c_name, o_name
        winner_char, loser_char   = c_char, o_char
        win_video  = c_char["videos"]["win"]
        lose_video = o_char["videos"]["lose"]
    elif outcome == "opponent_wins":
        winner_uid, loser_uid     = o_uid, c_uid
        winner_name, loser_name   = o_name, c_name
        winner_char, loser_char   = o_char, c_char
        win_video  = o_char["videos"]["win"]
        lose_video = c_char["videos"]["lose"]
    else:
        # Draw
        for uid in (c_uid, o_uid):
            await add_xp(uid, 50)
            await add_balance(uid, 50)
        await cq.message.edit_caption(
            caption=(
                f"🤝 **DRAW!**\n\n"
                f"Both **{c_name}** and **{o_name}** fought to a standstill!\n"
                f"⭐ +50 XP | 💰 +50 kakera each"
            )
        )
        return

    # Winner rewards
    await add_balance(winner_uid, kakera_reward)
    await add_xp(winner_uid, xp_reward)
    await deduct_balance(loser_uid, kakera_loss)
    await add_xp(loser_uid, 30)

    # Winner gets a random character added to harem
    harem_char = await get_random_character()
    harem_added = False
    if harem_char:
        try:
            await add_to_harem(winner_uid, harem_char["id"])
            harem_added = True
        except Exception:
            pass

    # Update fight stats in DB
    await _col("db_fighters").update_one(
        {"user_id": winner_uid},
        {"$inc": {"wins": 1, "total_battles": 1}},
        upsert=True,
    )
    await _col("db_fighters").update_one(
        {"user_id": loser_uid},
        {"$inc": {"losses": 1, "total_battles": 1}},
        upsert=True,
    )

    # Send lose video
    if lose_video:
        try:
            await cq.message.reply_video(
                lose_video,
                caption=f"💀 **{loser_name}** has been defeated!"
            )
        except Exception:
            pass

    # Send win video
    if win_video:
        try:
            await cq.message.reply_video(
                win_video,
                caption=f"🏆 **{winner_name}** WINS!"
            )
        except Exception:
            pass

    harem_line = (
        f"🎁 **{winner_name}** captured **{harem_char['name']}** from the battle! Added to harem!\n"
        if harem_added and harem_char else ""
    )

    await cq.message.edit_caption(
        caption=(
            f"🏆 **FIGHT OVER!** 🏆\n\n"
            f"🥇 **{winner_name}** defeats **{loser_name}**!\n\n"
            f"💰 **{winner_name}**: +{kakera_reward} kakera | ⭐ +{xp_reward} XP\n"
            f"😔 **{loser_name}**: -{kakera_loss} kakera | ⭐ +30 XP\n\n"
            f"{harem_line}"
            f"🔄 Challenge again with `/dbfight`!"
        )
    )


# ── /dbupgrade ────────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command(["dbupgrade", "powerup", "plup"]))
async def dbupgrade_cmd(_, m: Message):
    uid = m.from_user.id
    upg = await _get_upgrades(uid)
    cur_tier = upg.get("tier", 0)

    if cur_tier >= len(UPGRADE_TIERS):
        pl_name = _power_tier_name(99999 + upg.get("bonus_power", 0))
        await m.reply(
            f"🌟 **MAX POWER REACHED!**\n\n"
            f"You've achieved **Ultra Instinct** tier!\n"
            f"Bonus Power: `+{upg.get('bonus_power', 0):,}`\n"
            f"Total Kakera Spent: `{upg.get('total_spent', 0):,}`"
        )
        return

    next_tier = UPGRADE_TIERS[cur_tier]
    bal       = await get_balance(uid)
    bonus     = upg.get("bonus_power", 0)

    # Show upgrade info + button
    buttons = IKM([[
        IKB(f"💰 Spend {next_tier['cost']:,} Kakera", callback_data=f"dbupg:{uid}:{cur_tier}"),
        IKB("❌ Cancel", callback_data=f"dbupg:{uid}:cancel"),
    ]])

    tiers_display = "\n".join(
        f"{'✅' if i < cur_tier else ('⏭️' if i == cur_tier else '🔒')} "
        f"**{t['label']}** — +{t['boost']:,} PL | `{t['cost']:,}` kakera"
        for i, t in enumerate(UPGRADE_TIERS)
    )

    await m.reply(
        f"⚡ **POWER UPGRADE SYSTEM** ⚡\n\n"
        f"💰 Your Balance: `{bal:,}` kakera\n"
        f"🔥 Current Bonus: `+{bonus:,}` power\n\n"
        f"**Upgrade Tiers:**\n{tiers_display}\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⬆️ **Next: {next_tier['label']}**\n"
        f"Cost: `{next_tier['cost']:,}` kakera\n"
        f"Reward: `+{next_tier['boost']:,}` power",
        reply_markup=buttons,
    )


@_soul.app.on_callback_query(filters.regex(r"^dbupg:(\d+):(\w+)$"))
async def dbupg_cb(_, cq: CallbackQuery):
    _, uid_str, tier_str = cq.data.split(":")
    uid = int(uid_str)

    if cq.from_user.id != uid:
        await cq.answer("Not your upgrade!", show_alert=True)
        return

    if tier_str == "cancel":
        await cq.message.edit_caption(caption="❌ Upgrade cancelled.")
        await cq.answer()
        return

    cur_tier = int(tier_str)
    upg      = await _get_upgrades(uid)

    if upg.get("tier", 0) != cur_tier:
        await cq.answer("Tier mismatch, re-run /dbupgrade", show_alert=True)
        return

    tier_data = UPGRADE_TIERS[cur_tier]
    cost      = tier_data["cost"]
    bal       = await get_balance(uid)

    if bal < cost:
        await cq.answer(
            f"❌ Need {cost:,} kakera, you have {bal:,}!", show_alert=True
        )
        return

    await deduct_balance(uid, cost)
    await _col("db_upgrades").update_one(
        {"user_id": uid},
        {"$inc": {"tier": 1, "bonus_power": tier_data["boost"], "total_spent": cost}},
        upsert=True,
    )

    upg = await _get_upgrades(uid)
    new_bonus = upg["bonus_power"]
    pl_name   = _power_tier_name(FIGHTERS["goku"]["base_power"] + new_bonus)

    await cq.message.edit_caption(
        caption=(
            f"🌟 **POWER UP!** 🌟\n\n"
            f"✅ **{tier_data['label']}** unlocked!\n"
            f"⚡ Bonus Power: `+{new_bonus:,}`\n"
            f"🏆 Tier: **{pl_name}**\n\n"
            f"{'🔥 All tiers maxed! You are unstoppable!' if upg['tier'] >= len(UPGRADE_TIERS) else 'Keep upgrading with /dbupgrade!'}"
        )
    )
    await cq.answer(f"Power up! +{tier_data['boost']:,} power gained!")


# ── /powerlevel ───────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command(["powerlevel", "pl", "power"]))
async def power_level_cmd(_, m: Message):
    target  = m.reply_to_message.from_user if m.reply_to_message else m.from_user
    upg     = await _get_upgrades(target.id)
    stats   = await _col("db_fighters").find_one({"user_id": target.id}) or {}
    bonus   = upg.get("bonus_power", 0)
    tier    = upg.get("tier", 0)

    # Find their most used character
    fighter_key = stats.get("character_key", "goku")
    char        = FIGHTERS.get(fighter_key, FIGHTERS["goku"])
    total_pl    = char["base_power"] + bonus
    pl_name     = _power_tier_name(total_pl)
    stars       = "⭐" * min(tier + 1, 10)

    w = stats.get("wins", 0)
    l = stats.get("losses", 0)
    pct = int(w / max(w + l, 1) * 100)

    tier_label = UPGRADE_TIERS[tier - 1]["label"] if tier > 0 else "No upgrades yet"

    await m.reply(
        f"⚡ **{target.first_name}'s Power Level** ⚡\n\n"
        f"🐉 Fighter: **{char['emoji']} {char['name']}**\n"
        f"💪 Base PL: `{char['base_power']:,}`\n"
        f"⬆️ Bonus PL: `+{bonus:,}`\n"
        f"🔥 Total PL: `{total_pl:,}` {stars}\n"
        f"🏆 Tier: **{pl_name}**\n"
        f"📊 W/L: `{w}/{l}` ({pct}% win rate)\n"
        f"💎 Upgrade: **{tier_label}**\n\n"
        f"💡 Use `/dbupgrade` to power up!"
    )


# ── Dragon Ball Collection ────────────────────────────────────────────────────

@_soul.app.on_message(filters.command(["searchball", "sball", "findball"]))
async def search_ball(_, m: Message):
    uid  = m.from_user.id
    u    = m.from_user
    await get_or_create_user(uid, u.username or "", u.first_name or "", u.last_name or "")

    doc      = await _get_user_balls(uid)
    last_s   = doc.get("last_search")
    now      = datetime.utcnow()
    cooldown = 3600

    if last_s:
        if isinstance(last_s, str):
            last_s = datetime.fromisoformat(last_s)
        elapsed = (now - last_s).total_seconds()
        if elapsed < cooldown:
            remaining = cooldown - elapsed
            mins = int(remaining // 60)
            await m.reply(f"🔭 **Search again in `{mins}m`...**")
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
        owned_now = set(doc.get("balls", [])) | {found_ball}
        ball_name = BALL_NAMES[found_ball - 1]
        stars_str = DRAGON_BALLS[found_ball - 1]

        await m.reply(
            f"🐉 **Dragon Ball Found!**\n\n"
            f"{stars_str} **{ball_name} Ball** (#{found_ball})\n\n"
            f"📦 Collection: `{len(owned_now)}/7`\n"
            + ("✨ **You have all 7! Use `/wish` now!**" if len(owned_now) >= 7 else "🔭 Keep searching!")
        )
    else:
        await m.reply(
            "🌌 *You searched but found nothing...*\n"
            "🔭 Try again in 1 hour!"
        )


@_soul.app.on_message(filters.command(["dragonballs", "myballs", "balls"]))
async def my_balls(_, m: Message):
    target = m.reply_to_message.from_user if m.reply_to_message else m.from_user
    doc    = await _get_user_balls(target.id)
    owned  = sorted(set(doc.get("balls", [])))

    rows = []
    for i in range(1, 8):
        if i in owned:
            rows.append(f"✅ {DRAGON_BALLS[i-1]} {BALL_NAMES[i-1]}")
        else:
            rows.append(f"❌ _{BALL_NAMES[i-1]}_")

    total = doc.get("total_collected", 0)
    ready = "✨ **ALL COLLECTED! Use `/wish`!**" if len(owned) >= 7 else f"📦 `{len(owned)}/7` collected"

    await m.reply(
        f"🐉 **{target.first_name}'s Dragon Balls**\n\n"
        + "\n".join(rows)
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

    last_wish = await _col("db_wishes").find_one(
        {"user_id": uid}, sort=[("wished_at", -1)]
    )
    if last_wish:
        elapsed = (datetime.utcnow() - last_wish["wished_at"]).total_seconds()
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
async def wish_cb(_, cq: CallbackQuery):
    _, uid_str, wish_key = cq.data.split(":")
    uid = int(uid_str)

    if cq.from_user.id != uid:
        await cq.answer("This isn't your wish!", show_alert=True)
        return

    wish = WISHES.get(wish_key)
    if not wish:
        await cq.answer("Unknown wish.", show_alert=True)
        return

    await _col("db_dragon_balls").update_one({"user_id": uid}, {"$set": {"balls": []}})

    result = ""
    if wish_key == "kakera":
        await add_balance(uid, 10_000)
        result = "💰 **10,000 kakera** added to your balance!"
    elif wish_key == "xp":
        await add_xp(uid, 2_000)
        result = "⭐ **2,000 XP** gained!"
    elif wish_key == "power":
        await _col("db_upgrades").update_one(
            {"user_id": uid},
            {"$inc": {"bonus_power": 500}},
            upsert=True,
        )
        result = "⚡ **+500 Power Level** added to your fighter!"
    elif wish_key == "immunity":
        result = "🛡 **1-week immunity** granted!"
    elif wish_key == "reroll":
        result = "🎲 Your next `/daily` gives **double** kakera!"

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


# ── /dbchars — List all fighters ─────────────────────────────────────────────

@_soul.app.on_message(filters.command(["dbchars", "fighters", "roster"]))
async def dbchars_cmd(_, m: Message):
    heroes   = [(k, v) for k, v in FIGHTERS.items() if v["team"] == "hero"]
    villains = [(k, v) for k, v in FIGHTERS.items() if v["team"] == "villain"]
    neutral  = [(k, v) for k, v in FIGHTERS.items() if v["team"] == "neutral"]

    def fmt(items):
        return "\n".join(
            f"{v['emoji']} **{v['name']}** — `{v['base_power']:,}` PL | {v['signature']}"
            for _, v in items
        )

    await m.reply(
        f"🐉 **Dragon Ball Roster** 🐉\n\n"
        f"🦸 **HEROES** ({len(heroes)})\n{fmt(heroes)}\n\n"
        f"😈 **VILLAINS** ({len(villains)})\n{fmt(villains)}\n\n"
        f"⚖️ **NEUTRAL** ({len(neutral)})\n{fmt(neutral)}\n\n"
        f"💡 Use `/dbfight @user` to start a fight!\n"
        f"⚡ Use `/dbupgrade` to boost your power!"
    )


# ── /dbtop — Leaderboard ──────────────────────────────────────────────────────

@_soul.app.on_message(filters.command(["dbtop", "battletop", "fightlb"]))
async def dbtop_cmd(_, m: Message):
    fighters = (
        await _col("db_fighters")
        .find({})
        .sort("wins", -1)
        .limit(10)
        .to_list(10)
    )

    if not fighters:
        await m.reply("🏆 No battles yet. Use `/dbfight` to start!")
        return

    lines = []
    for i, f in enumerate(fighters, 1):
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"`{i}.`")
        uid   = f["user_id"]
        name  = f.get("display_name", str(uid))
        w     = f.get("wins", 0)
        l     = f.get("losses", 0)
        upg   = await _get_upgrades(uid)
        bonus = upg.get("bonus_power", 0)
        lines.append(
            f"{medal} [{name}](tg://user?id={uid})\n"
            f"   🏆 `{w}W/{l}L` | ⚡ Bonus PL: `+{bonus:,}`"
        )

    await m.reply(
        "⚡ **Dragon Ball Fight Leaderboard** ⚡\n\n" + "\n\n".join(lines),
        disable_web_page_preview=True,
    )
