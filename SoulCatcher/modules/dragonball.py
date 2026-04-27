"""SoulCatcher/modules/dragonball.py — Dragon Ball Fighting Game with Shop System.

Bot API 9.4 / Pyrofork Colored-Button Edition
──────────────────────────────────────────────
Colored inline buttons require a Pyrofork build whose TL layer exposes the
`style` field on KeyboardButtonCallback (typically Layer ≥ 166).

Runtime detection:
  • If `_PYROFORK_COLORS = True`  → raw ReplyInlineMarkup with coloured buttons
  • If `_PYROFORK_COLORS = False` → standard InlineKeyboardMarkup (safe fallback)

Both paths share identical callback-data strings, so no handler changes are
needed when toggling between the two.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import filters
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import SoulCatcher as _soul
from SoulCatcher.config import DB_NAME, MONGO_URI
from SoulCatcher.database import (
    add_balance,
    add_to_harem,
    add_xp,
    deduct_balance,
    get_balance,
    get_or_create_user,
    get_random_character,
)

log = logging.getLogger("SoulCatcher.dragonball")


# ══════════════════════════════════════════════════════════════════════════════
#  PYROFORK COLORED-BUTTON DETECTION
# ══════════════════════════════════════════════════════════════════════════════

_PYROFORK_COLORS: bool = False
_RawKBC: Any = None
_RawRow: Any = None
_RawMarkup: Any = None

# ══════════════════════════════════════════════════════════════════════════════
#  COLORED BUTTON DETECTION  —  3-tier probe
#
#  Tier 1 (best): Pyrofork ≥ 2.3.x  — style= on InlineKeyboardButton directly
#  Tier 2:        Pyrofork raw types — KeyboardButtonCallback(style=)
#  Tier 3:        Standard Pyrogram  — no colors, plain InlineKeyboardButton
# ══════════════════════════════════════════════════════════════════════════════

_COLOR_MODE: str = "none"   # "hl" | "raw" | "none"

# ── Tier 1: Pyrofork high-level style= on InlineKeyboardButton ───────────────
try:
    _probe = InlineKeyboardButton("p", callback_data="p", style=1)
    # If we got here the kwarg is accepted — Pyrofork HL colors available
    _COLOR_MODE = "hl"
    log.info("✅ Pyrofork colored buttons: ENABLED (high-level mode)")
except TypeError:
    pass

# ── Tier 2: Pyrofork raw KeyboardButtonCallback(style=) ──────────────────────
if _COLOR_MODE == "none":
    try:
        from pyrogram.raw.types import (            # type: ignore[attr-defined]
            KeyboardButtonCallback as _RawKBC,
            KeyboardButtonRow      as _RawRow,
            ReplyInlineMarkup      as _RawMarkup,
        )
        _RawKBC(text="p", data=b"p", style=1)      # probe
        _COLOR_MODE = "raw"
        log.info("✅ Pyrofork colored buttons: ENABLED (raw mode)")
    except Exception:
        pass

if _COLOR_MODE == "none":
    log.info("ℹ️  Pyrofork colored buttons: not available — using standard markup")

# ── Style constants ───────────────────────────────────────────────────────────
#   0 = default (gray)   1 = blue   2 = red/orange   3 = green
_CS_DEFAULT = 0
_CS_BLUE    = 1   # heroes, info
_CS_RED     = 2   # villains, cancel, danger
_CS_GREEN   = 3   # buy, confirm, win
_CS_ORANGE  = 2   # specials / transform (same slot as red on most clients)


# ── Button factory ────────────────────────────────────────────────────────────

def _make_btn(text: str, data: str, style: int = _CS_DEFAULT) -> InlineKeyboardButton:
    """Return the right button object for the active color mode."""
    if _COLOR_MODE == "hl":
        # Pyrofork adds `style` directly to the high-level class
        return InlineKeyboardButton(text, callback_data=data, style=style)
    if _COLOR_MODE == "raw":
        return _RawKBC(text=text, data=data.encode(), style=style)
    # Plain Pyrogram — no color support
    return InlineKeyboardButton(text, callback_data=data)


def _make_markup(rows: list[list]) -> InlineKeyboardMarkup | Any:
    """Wrap rows of buttons into the correct markup type."""
    if _COLOR_MODE == "raw":
        from pyrogram.raw.types import KeyboardButtonRow, ReplyInlineMarkup
        return ReplyInlineMarkup(rows=[KeyboardButtonRow(buttons=r) for r in rows])
    # Both "hl" and "none" use standard InlineKeyboardMarkup
    return InlineKeyboardMarkup(rows)


# ── Unified keyboard builder ──────────────────────────────────────────────────

class _KB:
    """Accumulates button rows and builds the correct markup type."""

    def __init__(self):
        self._rows: list[list] = []

    def row(self, *btns) -> "_KB":
        self._rows.append(list(btns))
        return self

    def build(self) -> InlineKeyboardMarkup | Any:
        return _make_markup(self._rows)

    @staticmethod
    def btn(text: str, data: str, style: int = _CS_DEFAULT):
        return _make_btn(text, data, style)


# ── Public aliases ────────────────────────────────────────────────────────────

def B(text: str, data: str, style: int = _CS_DEFAULT):
    """Create a button with optional color style."""
    return _make_btn(text, data, style)


def markup(*rows: list) -> InlineKeyboardMarkup | Any:
    """Build markup directly from rows of B() buttons."""
    kb = _KB()
    for row in rows:
        kb.row(*row)
    return kb.build()


# ══════════════════════════════════════════════════════════════════════════════
#  DATABASE
# ══════════════════════════════════════════════════════════════════════════════

_db = None


async def init_db():
    global _db
    client = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=10_000)
    _db    = client[DB_NAME]
    indexes = [
        ("db_dragon_balls",   [("user_id", 1)], True),
        ("db_wishes",         [("user_id", 1), ("wished_at", -1)], False),
        ("db_owned_fighters", [("user_id", 1)], True),
        ("db_fight_stats",    [("user_id", 1)], True),
        ("db_upgrades",       [("user_id", 1)], True),
    ]
    for cname, keys, unique in indexes:
        await _db[cname].create_index(keys, unique=unique, background=True)
    log.info("✅ DragonBall DB ready")


def _col(name: str):
    if _db is None:
        raise RuntimeError("DragonBall _db is None — init_db() was never awaited.")
    return _db[name]


# ══════════════════════════════════════════════════════════════════════════════
#  FIGHTER CATALOGUE
# ══════════════════════════════════════════════════════════════════════════════

FIGHTERS: dict[str, dict] = {
    # ── HEROES ────────────────────────────────────────────────────────────────
    "goku": {
        "name": "Goku", "emoji": "🟡", "team": "hero", "price": 0,
        "base_power": 9000, "hp": 100, "signature": "Kamehameha",
        "transform_name": "Ultra Instinct", "transform_power": 1.6,
        "desc": "The legendary Super Saiyan warrior",
        "videos": {
            "idle":      "https://litter.catbox.moe/so5rk7.mp4",
            "transform": "https://litter.catbox.moe/3ga3cf.mp4",
            "attack":    "https://litter.catbox.moe/ghw0y2.mp4",
            "win":       "https://litter.catbox.moe/zjukij.mp4",
            "lose":      "https://litter.catbox.moe/l999rw.mp4",
        },
    },
    "vegeta": {
        "name": "Vegeta", "emoji": "🔵", "team": "hero", "price": 3_000,
        "base_power": 8500, "hp": 95, "signature": "Final Flash",
        "transform_name": "Ultra Ego", "transform_power": 1.5,
        "desc": "Prince of all Saiyans, rival to Goku",
        "videos": {
            "idle":      "https://litter.catbox.moe/snqtio.mp4",
            "transform": "https://litter.catbox.moe/almlg0.mp4",
            "attack":    "https://litter.catbox.moe/lrsn2j.mp4",
            "win":       None,
            "lose":      "https://litter.catbox.moe/xpjjpx.mp4",
        },
    },
    "broly": {
        "name": "Broly", "emoji": "🟢", "team": "hero", "price": 5_000,
        "base_power": 9200, "hp": 110, "signature": "Gigantic Roar",
        "transform_name": "Legendary Super Saiyan", "transform_power": 1.7,
        "desc": "The Legendary Super Saiyan, pure raw power",
        "videos": {
            "idle":      "https://litter.catbox.moe/i3pshj.mp4",
            "transform": "https://litter.catbox.moe/tqy9md.mp4",
            "attack":    "https://litter.catbox.moe/wr191h.mp4",
            "win":       None,
            "lose":      "https://litter.catbox.moe/wr191h.mp4",
        },
    },
    "vegito": {
        "name": "Vegito", "emoji": "⚡", "team": "hero", "price": 8_000,
        "base_power": 9800, "hp": 105, "signature": "Spirit Sword",
        "transform_name": "Super Saiyan Blue", "transform_power": 1.8,
        "desc": "The fusion of Goku and Vegeta, unstoppable",
        "videos": {
            "idle":      "https://litter.catbox.moe/b9hty6.mp4",
            "transform": None,
            "attack":    "https://litter.catbox.moe/cqxu9f.mp4",
            "win":       None,
            "lose":      "https://litter.catbox.moe/9rk2cf.mp4",
        },
    },
    "gohan": {
        "name": "Gohan", "emoji": "🟣", "team": "hero", "price": 4_000,
        "base_power": 8800, "hp": 100, "signature": "Masenko",
        "transform_name": "Beast Mode", "transform_power": 1.65,
        "desc": "Son of Goku, hidden power unleashed",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
    },
    "trunks": {
        "name": "Future Trunks", "emoji": "🔵", "team": "hero", "price": 3_500,
        "base_power": 7800, "hp": 90, "signature": "Burning Attack",
        "transform_name": "Super Saiyan Rage", "transform_power": 1.5,
        "desc": "Warrior from the future who slays gods",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
    },
    "piccolo": {
        "name": "Piccolo", "emoji": "🟢", "team": "hero", "price": 2_000,
        "base_power": 6000, "hp": 85, "signature": "Special Beam Cannon",
        "transform_name": "Orange Piccolo", "transform_power": 1.4,
        "desc": "The Namekian warrior, Gohan's mentor",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
    },
    "krillin": {
        "name": "Krillin", "emoji": "⚪", "team": "hero", "price": 500,
        "base_power": 3000, "hp": 70, "signature": "Destructo Disc",
        "transform_name": "Unlocked Potential", "transform_power": 1.3,
        "desc": "Earth's strongest human, surprisingly lethal",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
    },
    "android17": {
        "name": "Android 17", "emoji": "🔵", "team": "hero", "price": 3_000,
        "base_power": 7000, "hp": 88, "signature": "Power Blitz",
        "transform_name": "Barrier Overload", "transform_power": 1.35,
        "desc": "Infinite energy android, Tournament MVP",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
    },
    "bardock": {
        "name": "Bardock", "emoji": "🟠", "team": "hero", "price": 2_500,
        "base_power": 5500, "hp": 80, "signature": "Riot Javelin",
        "transform_name": "Super Saiyan", "transform_power": 1.4,
        "desc": "Low-class warrior, father of Goku",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
    },
    "gogeta": {
        "name": "Gogeta", "emoji": "🔴", "team": "hero", "price": 9_000,
        "base_power": 9900, "hp": 110, "signature": "True Kamehameha",
        "transform_name": "Super Saiyan Blue", "transform_power": 1.9,
        "desc": "The mightiest fusion, Goku and Vegeta as one",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
    },
    # ── VILLAINS ──────────────────────────────────────────────────────────────
    "goku_black": {
        "name": "Goku Black", "emoji": "😈", "team": "villain", "price": 6_000,
        "base_power": 9100, "hp": 98, "signature": "Black Kamehameha",
        "transform_name": "Super Saiyan Rose", "transform_power": 1.55,
        "desc": "Zamasu in Goku's body, corrupted god",
        "videos": {
            "idle":      "https://litter.catbox.moe/itlrio.mp4",
            "transform": None,
            "attack":    "https://litter.catbox.moe/qfbkk2.mp4",
            "win":       "https://litter.catbox.moe/tyxxbd.mp4",
            "lose":      None,
        },
    },
    "cell": {
        "name": "Cell", "emoji": "🟢", "team": "villain", "price": 4_500,
        "base_power": 7500, "hp": 90, "signature": "Solar Kamehameha",
        "transform_name": "Perfect Form", "transform_power": 1.4,
        "desc": "The perfect being, absorber of power",
        "videos": {
            "idle":      "https://litter.catbox.moe/m7j5xf.mp4",
            "transform": None,
            "attack":    "https://litter.catbox.moe/3mj3gc.mp4",
            "win":       None,
            "lose":      "https://litter.catbox.moe/l1d2fl.mp4",
        },
    },
    "frieza": {
        "name": "Frieza", "emoji": "❄️", "team": "villain", "price": 5_500,
        "base_power": 8800, "hp": 95, "signature": "Death Ball",
        "transform_name": "Black Frieza", "transform_power": 1.6,
        "desc": "The galactic emperor, eternal rival",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
    },
    "jiren": {
        "name": "Jiren", "emoji": "🔴", "team": "villain", "price": 7_000,
        "base_power": 9500, "hp": 105, "signature": "Power Impact",
        "transform_name": "Limit Breaker", "transform_power": 1.7,
        "desc": "The Pride Trooper who surpassed gods",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
    },
    "beerus": {
        "name": "Beerus", "emoji": "💜", "team": "villain", "price": 10_000,
        "base_power": 9900, "hp": 115, "signature": "Hakai",
        "transform_name": "God of Destruction", "transform_power": 1.8,
        "desc": "The God of Destruction, destroyer of worlds",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
    },
    "majin_buu": {
        "name": "Majin Buu", "emoji": "🩷", "team": "villain", "price": 4_000,
        "base_power": 7800, "hp": 120, "signature": "Chocolate Beam",
        "transform_name": "Pure Evil Buu", "transform_power": 1.5,
        "desc": "The magical monster, can absorb anything",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
    },
    "zamasu": {
        "name": "Fused Zamasu", "emoji": "⚡", "team": "villain", "price": 6_500,
        "base_power": 8500, "hp": 100, "signature": "Holy Wrath",
        "transform_name": "Divine Overflow", "transform_power": 1.55,
        "desc": "Immortal god of justice, half-corrupted",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
    },
    "janemba": {
        "name": "Janemba", "emoji": "👹", "team": "villain", "price": 5_000,
        "base_power": 8200, "hp": 95, "signature": "Hell Gate",
        "transform_name": "Super Janemba", "transform_power": 1.5,
        "desc": "The demon of pure evil energy",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
    },
    "turles": {
        "name": "Turles", "emoji": "🌿", "team": "villain", "price": 2_000,
        "base_power": 5000, "hp": 78, "signature": "Power Ball",
        "transform_name": "Tree of Might Boost", "transform_power": 1.45,
        "desc": "Goku's dark mirror, powered by evil fruit",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
    },
    "raditz": {
        "name": "Raditz", "emoji": "⚫", "team": "villain", "price": 500,
        "base_power": 1500, "hp": 65, "signature": "Double Sunday",
        "transform_name": "Great Ape", "transform_power": 1.3,
        "desc": "Goku's evil brother, first true villain",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
    },
    "nappa": {
        "name": "Nappa", "emoji": "🟤", "team": "villain", "price": 1_000,
        "base_power": 4000, "hp": 85, "signature": "Bomber DX",
        "transform_name": "Full Power", "transform_power": 1.3,
        "desc": "Vegeta's brutal elite soldier",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
    },
    "android21": {
        "name": "Android 21", "emoji": "🩷", "team": "villain", "price": 5_500,
        "base_power": 8000, "hp": 92, "signature": "Conquer the World",
        "transform_name": "Majin Form", "transform_power": 1.55,
        "desc": "The hungry researcher who absorbs power",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
    },
    "baby_vegeta": {
        "name": "Baby Vegeta", "emoji": "👶", "team": "villain", "price": 4_500,
        "base_power": 7200, "hp": 90, "signature": "Revenge Death Ball",
        "transform_name": "Golden Great Ape", "transform_power": 1.5,
        "desc": "Tuffle parasite controlling Vegeta's body",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
    },
    # ── NEUTRAL ───────────────────────────────────────────────────────────────
    "hit": {
        "name": "Hit", "emoji": "🕐", "team": "neutral", "price": 5_000,
        "base_power": 8000, "hp": 90, "signature": "Time Skip",
        "transform_name": "Pure Progress", "transform_power": 1.6,
        "desc": "The legendary assassin who skips time",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
    },
    "kefla": {
        "name": "Kefla", "emoji": "💚", "team": "neutral", "price": 4_500,
        "base_power": 8200, "hp": 95, "signature": "Blaster Meteor",
        "transform_name": "Super Saiyan 2", "transform_power": 1.5,
        "desc": "Universe 6's female Saiyan fusion",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
    },
    "toppo": {
        "name": "Toppo", "emoji": "🟠", "team": "neutral", "price": 5_500,
        "base_power": 8800, "hp": 100, "signature": "Hakai Blast",
        "transform_name": "God of Destruction Mode", "transform_power": 1.65,
        "desc": "Pride Trooper leader who embraced destruction",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
    },
    "whis": {
        "name": "Whis", "emoji": "⚪", "team": "neutral", "price": 15_000,
        "base_power": 9999, "hp": 999, "signature": "Staff Strike",
        "transform_name": "Angel Mode", "transform_power": 2.0,
        "desc": "The angelic attendant, beyond all gods",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
    },
    "zeno": {
        "name": "Zeno", "emoji": "👑", "team": "neutral", "price": 50_000,
        "base_power": 99999, "hp": 9999, "signature": "Erase",
        "transform_name": "True Form", "transform_power": 3.0,
        "desc": "The Omni-King, erases universes for fun",
        "videos": {"idle": None, "transform": None, "attack": None, "win": None, "lose": None},
    },
}

FREE_FIGHTER = "goku"

# Team → display dot + button colour
TEAM_META: dict[str, tuple[str, str, int]] = {
    #             dot   label        btn_style
    "hero":    ("🔵", "🦸 Hero",    _CS_BLUE),
    "villain": ("🔴", "😈 Villain", _CS_RED),
    "neutral": ("⚪", "⚖️ Neutral", _CS_DEFAULT),
}


def _team_dot(team: str)  -> str: return TEAM_META.get(team, ("⚪", "", _CS_DEFAULT))[0]
def _team_label(team: str)-> str: return TEAM_META.get(team, ("⚪", "⚖️ Neutral", _CS_DEFAULT))[1]
def _team_style(team: str)-> int: return TEAM_META.get(team, ("⚪", "", _CS_DEFAULT))[2]


# ── Dragon Balls ──────────────────────────────────────────────────────────────

DRAGON_BALLS = [
    "⭐", "⭐⭐", "⭐⭐⭐", "⭐⭐⭐⭐",
    "⭐⭐⭐⭐⭐", "⭐⭐⭐⭐⭐⭐", "⭐⭐⭐⭐⭐⭐⭐",
]
BALL_NAMES = [
    "One-Star", "Two-Star", "Three-Star", "Four-Star",
    "Five-Star", "Six-Star", "Seven-Star",
]
TOTAL_BALLS   = 7
WISH_COOLDOWN = 7 * 24 * 3600   # seconds

WISHES: dict[str, dict] = {
    "kakera":   {"label": "💰  10,000 Kakera",      "style": _CS_ORANGE},
    "xp":       {"label": "⭐  2,000 XP",            "style": _CS_BLUE},
    "fighter":  {"label": "🎁  Free Random Fighter", "style": _CS_GREEN},
    "immunity": {"label": "🛡  1-Week Immunity",     "style": _CS_DEFAULT},
    "reroll":   {"label": "🎲  Daily Reroll ×2",     "style": _CS_ORANGE},
}

# ── Power Upgrade Tiers ───────────────────────────────────────────────────────

UPGRADE_TIERS = [
    {"tier": 1, "boost":  500,   "cost":   1_000, "label": "Tier I — Awakening"},
    {"tier": 2, "boost":  1500,  "cost":   5_000, "label": "Tier II — Super Saiyan"},
    {"tier": 3, "boost":  4000,  "cost":  15_000, "label": "Tier III — Super Saiyan 2"},
    {"tier": 4, "boost": 10000,  "cost":  40_000, "label": "Tier IV — Super Saiyan 3"},
    {"tier": 5, "boost": 25000,  "cost": 100_000, "label": "Tier V — Super Saiyan God"},
    {"tier": 6, "boost": 60000,  "cost": 300_000, "label": "Tier VI — Ultra Instinct"},
]

POWER_TIERS = [
    (0,       2_000,  "Earthling"),
    (2_001,   6_000,  "Saiyan"),
    (6_001,   12_000, "Super Saiyan"),
    (12_001,  25_000, "Super Saiyan 2"),
    (25_001,  50_000, "Super Saiyan 3"),
    (50_001, 100_000, "Super Saiyan God"),
    (100_001,250_000, "Ultra Instinct"),
    (250_001,999_999, "Omni-God"),
]


def _power_tier_name(pl: int) -> str:
    for lo, hi, name in POWER_TIERS:
        if lo <= pl <= hi:
            return name
    return "Beyond Mortal"


# ── Active fights (in-memory) ─────────────────────────────────────────────────

_active_fights: dict[str, dict] = {}


# ══════════════════════════════════════════════════════════════════════════════
#  KEYBOARD BUILDERS
# ══════════════════════════════════════════════════════════════════════════════

def _shop_keyboard(page: int = 0, uid: int = 0):
    keys     = list(FIGHTERS.keys())
    per_page = 6
    start    = page * per_page
    chunk    = keys[start:start + per_page]
    kb       = _KB()

    for i in range(0, len(chunk), 2):
        row = []
        for key in chunk[i:i + 2]:
            c         = FIGHTERS[key]
            dot       = _team_dot(c["team"])
            style     = _team_style(c["team"])
            price_str = "FREE" if c["price"] == 0 else f"{c['price']:,}💰"
            row.append(B(f"{dot} {c['name']} · {price_str}",
                         f"dbshop_view:{uid}:{key}:{page}", style))
        kb.row(*row)

    nav = []
    if page > 0:
        nav.append(B("◀️ Prev", f"dbshop_page:{uid}:{page - 1}", _CS_DEFAULT))
    if start + per_page < len(keys):
        nav.append(B("Next ▶️", f"dbshop_page:{uid}:{page + 1}", _CS_DEFAULT))
    if nav:
        kb.row(*nav)

    kb.row(
        B("🥋 My Fighters", f"dbshop_mine:{uid}", _CS_BLUE),
        B("⚡ Power Up",    f"dbshop_pwrup:{uid}", _CS_ORANGE),
    )
    return kb.build()


def _owned_pick_keyboard(owned: list[str], fight_id: str, role: str, page: int = 0):
    per_page = 6
    start    = page * per_page
    chunk    = owned[start:start + per_page]
    kb       = _KB()

    for i in range(0, len(chunk), 2):
        row = []
        for key in chunk[i:i + 2]:
            c     = FIGHTERS.get(key, {})
            dot   = _team_dot(c.get("team", "neutral"))
            style = _team_style(c.get("team", "neutral"))
            row.append(B(f"{dot} {c.get('name', key)}",
                         f"dbpick:{fight_id}:{role}:{key}", style))
        kb.row(*row)

    nav = []
    if page > 0:
        nav.append(B("◀️", f"dbpick_page:{fight_id}:{role}:{page - 1}"))
    if start + per_page < len(owned):
        nav.append(B("▶️", f"dbpick_page:{fight_id}:{role}:{page + 1}"))
    if nav:
        kb.row(*nav)

    return kb.build()


def _action_keyboard(fight_id: str, uid: int, transformed: bool):
    kb = _KB()
    kb.row(B("⚔️  ──  A T T A C K  ──",  f"dbact:{fight_id}:{uid}:attack",    _CS_RED))
    kb.row(B("💥  ──  SPECIAL MOVE  ──",  f"dbact:{fight_id}:{uid}:special",   _CS_ORANGE))
    if not transformed:
        kb.row(B("🌀  ══  T R A N S F O R M  ══", f"dbact:{fight_id}:{uid}:transform", _CS_BLUE))
    return kb.build()


# ── HP bar ────────────────────────────────────────────────────────────────────

def _hp_bar(hp: int, max_hp: int) -> str:
    filled = max(0, min(10, int(hp / max(max_hp, 1) * 10)))
    return "🟩" * filled + "⬛" * (10 - filled)


# ══════════════════════════════════════════════════════════════════════════════
#  SAFE SEND / EDIT HELPERS
# ══════════════════════════════════════════════════════════════════════════════

async def _safe_reply_video(msg: Message, url: str, caption: str, **kw) -> bool:
    if not url:
        return False
    try:
        await msg.reply_video(url, caption=caption, **kw)
        return True
    except Exception as exc:
        log.debug("reply_video failed (%s): %s", url, exc)
        return False


async def _safe_edit_caption(msg, caption: str, **kw) -> bool:
    try:
        await msg.edit_caption(caption=caption, **kw)
        return True
    except Exception as exc:
        log.debug("edit_caption failed: %s", exc)
        return False


async def _safe_edit_text(msg, text: str, **kw) -> bool:
    try:
        await msg.edit_text(text, **kw)
        return True
    except Exception as exc:
        log.debug("edit_text failed: %s", exc)
        return False


async def _update_board(msg, caption: str, reply_markup) -> None:
    """Try edit_caption → edit_text → reply (never silently drops the update)."""
    if await _safe_edit_caption(msg, caption, reply_markup=reply_markup):
        return
    if await _safe_edit_text(msg, caption, reply_markup=reply_markup):
        return
    try:
        await msg.reply(caption, reply_markup=reply_markup)
    except Exception as exc:
        log.warning("_update_board: all paths failed: %s", exc)


# ══════════════════════════════════════════════════════════════════════════════
#  DB HELPERS
# ══════════════════════════════════════════════════════════════════════════════

async def _get_owned(uid: int) -> list[str]:
    doc = await _col("db_owned_fighters").find_one({"user_id": uid})
    if not doc:
        await _col("db_owned_fighters").insert_one(
            {"user_id": uid, "fighters": [FREE_FIGHTER]}
        )
        return [FREE_FIGHTER]
    owned = list(doc.get("fighters", [FREE_FIGHTER]))
    if FREE_FIGHTER not in owned:
        owned.append(FREE_FIGHTER)
    return owned


async def _add_owned(uid: int, key: str) -> None:
    await _col("db_owned_fighters").update_one(
        {"user_id": uid}, {"$addToSet": {"fighters": key}}, upsert=True,
    )


async def _get_upgrades(uid: int) -> dict:
    doc = await _col("db_upgrades").find_one({"user_id": uid})
    if not doc:
        doc = {"user_id": uid, "tier": 0, "bonus_power": 0, "total_spent": 0}
        await _col("db_upgrades").insert_one(doc)
    return doc


async def _get_user_balls(uid: int) -> dict:
    doc = await _col("db_dragon_balls").find_one({"user_id": uid})
    if not doc:
        doc = {"user_id": uid, "balls": [], "total_collected": 0, "last_search": None}
        await _col("db_dragon_balls").insert_one(doc)
    return doc


async def _has_all_balls(uid: int) -> bool:
    doc = await _get_user_balls(uid)
    return len(set(doc.get("balls", []))) >= TOTAL_BALLS


async def _ensure_user(u) -> None:
    await get_or_create_user(
        u.id, u.username or "", u.first_name or "", u.last_name or ""
    )


# ══════════════════════════════════════════════════════════════════════════════
#  SHOP
# ══════════════════════════════════════════════════════════════════════════════

@_soul.app.on_message(filters.command(["dbshop", "buychar", "dbstore"]))
async def dbshop_cmd(_, m: Message):
    uid = m.from_user.id
    await _ensure_user(m.from_user)
    bal   = await get_balance(uid)
    owned = await _get_owned(uid)
    await m.reply(
        f"🛒 **Dragon Ball Fighter Shop**\n\n"
        f"💰 Balance: `{bal:,}` kakera\n"
        f"🥋 Owned: `{len(owned)}/{len(FIGHTERS)}` fighters\n\n"
        f"🔵 Hero  |  🔴 Villain  |  ⚪ Neutral\n"
        f"Pick a fighter to view & buy:",
        reply_markup=_shop_keyboard(page=0, uid=uid),
    )


@_soul.app.on_callback_query(filters.regex(r"^dbshop_page:(\d+):(\d+)$"))
async def dbshop_page_cb(_, cq: CallbackQuery):
    uid_str, page_str = cq.data.split(":")[1], cq.data.split(":")[2]
    uid = int(uid_str)
    if cq.from_user.id != uid:
        return await cq.answer("Not your shop!", show_alert=True)
    bal   = await get_balance(uid)
    owned = await _get_owned(uid)
    await _update_board(
        cq.message,
        f"🛒 **Dragon Ball Fighter Shop**\n\n"
        f"💰 Balance: `{bal:,}` kakera\n"
        f"🥋 Owned: `{len(owned)}/{len(FIGHTERS)}` fighters\n\n"
        f"🔵 Hero  |  🔴 Villain  |  ⚪ Neutral\n"
        f"Pick a fighter to view & buy:",
        _shop_keyboard(page=int(page_str), uid=uid),
    )
    await cq.answer()


@_soul.app.on_callback_query(filters.regex(r"^dbshop_mine:(\d+)$"))
async def dbshop_mine_cb(_, cq: CallbackQuery):
    uid = int(cq.data.split(":")[1])
    if cq.from_user.id != uid:
        return await cq.answer("Not your data!", show_alert=True)
    owned = await _get_owned(uid)
    lines = [
        f"{_team_dot(FIGHTERS[k]['team'])} **{FIGHTERS[k]['name']}** — "
        f"`{FIGHTERS[k]['base_power']:,}` PL"
        for k in owned if k in FIGHTERS
    ]
    await cq.answer()
    await cq.message.reply(
        f"🥋 **Your Fighters** (`{len(owned)}/{len(FIGHTERS)}`)\n\n"
        + ("\n".join(lines) if lines else "_None yet_")
    )


@_soul.app.on_callback_query(filters.regex(r"^dbshop_pwrup:(\d+)$"))
async def dbshop_pwrup_cb(_, cq: CallbackQuery):
    await cq.answer("Use /dbupgrade to power up!", show_alert=True)


@_soul.app.on_callback_query(filters.regex(r"^dbshop_view:(\d+):(\w+):(\d+)$"))
async def dbshop_view_cb(_, cq: CallbackQuery):
    parts              = cq.data.split(":")
    uid, key, page_str = int(parts[1]), parts[2], parts[3]
    if cq.from_user.id != uid:
        return await cq.answer("Not your shop!", show_alert=True)
    char = FIGHTERS.get(key)
    if not char:
        return await cq.answer("Unknown fighter!", show_alert=True)

    owned  = await _get_owned(uid)
    bal    = await get_balance(uid)
    page   = int(page_str)
    dot    = _team_dot(char["team"])
    already= key in owned

    if already:
        status = "✅ **OWNED**"
    elif bal >= char["price"]:
        p = "**FREE**" if char["price"] == 0 else f"`{char['price']:,}` kakera"
        status = f"💰 Price: {p}\n✅ You can afford this!"
    else:
        p = f"`{char['price']:,}` kakera"
        status = f"💰 Price: {p}\n❌ Not enough kakera"

    kb = _KB()
    if not already:
        if char["price"] > 0:
            kb.row(B(f"🛒 Buy {char['name']} — {char['price']:,} 💰",
                     f"dbbuy:{uid}:{key}:{page}", _CS_GREEN))
        else:
            kb.row(B(f"🎁 Claim {char['name']} FREE",
                     f"dbbuy:{uid}:{key}:{page}", _CS_GREEN))
    kb.row(B("◀️ Back to Shop", f"dbshop_page:{uid}:{page}", _CS_DEFAULT))

    text = (
        f"{dot} **{char['name']}**  {char['emoji']}\n"
        f"{_team_label(char['team'])} | ⚡ PL: `{char['base_power']:,}`\n"
        f"❤️ HP: `{char['hp']}` | 💥 `{char['signature']}`\n"
        f"🌟 Transform: **{char['transform_name']}** (×{char['transform_power']})\n"
        f"📖 _{char['desc']}_\n\n"
        f"{status}"
    )
    await _update_board(cq.message, text, kb.build())
    await cq.answer()


@_soul.app.on_callback_query(filters.regex(r"^dbbuy:(\d+):(\w+):(\d+)$"))
async def dbbuy_cb(_, cq: CallbackQuery):
    parts           = cq.data.split(":")
    uid, key, page  = int(parts[1]), parts[2], parts[3]
    if cq.from_user.id != uid:
        return await cq.answer("Not your purchase!", show_alert=True)
    char = FIGHTERS.get(key)
    if not char:
        return await cq.answer("Unknown fighter!", show_alert=True)
    owned = await _get_owned(uid)
    if key in owned:
        return await cq.answer("You already own this fighter!", show_alert=True)
    if char["price"] > 0:
        bal = await get_balance(uid)
        if bal < char["price"]:
            return await cq.answer(
                f"❌ Need {char['price']:,} kakera, you have {bal:,}!", show_alert=True
            )
        await deduct_balance(uid, char["price"])
    await _add_owned(uid, key)
    owned = await _get_owned(uid)
    dot   = _team_dot(char["team"])
    back  = markup([B("◀️ Back to Shop", f"dbshop_page:{uid}:{page}")])
    await cq.answer(f"✅ {char['name']} is yours!", show_alert=True)

    if char["videos"].get("idle"):
        sent = await _safe_reply_video(
            cq.message, char["videos"]["idle"],
            caption=(
                f"🎉 {dot} **{char['emoji']} {char['name']} Purchased!**\n\n"
                f"⚡ PL: `{char['base_power']:,}` | 💥 {char['signature']}\n"
                f"🌟 Transform: **{char['transform_name']}**\n\n"
                f"Use `/dbfight @user` to battle!"
            ),
        )
        if sent:
            return

    await _update_board(
        cq.message,
        f"🎉 {dot} **{char['emoji']} {char['name']} Purchased!**\n\n"
        f"⚡ PL: `{char['base_power']:,}` | 💥 {char['signature']}\n"
        f"🌟 Transform: **{char['transform_name']}**\n"
        f"🥋 You now own `{len(owned)}/{len(FIGHTERS)}` fighters.\n\n"
        f"Use `/dbfight @user` to battle!",
        back,
    )


@_soul.app.on_message(filters.command(["mydb", "myfighters", "dbcollection"]))
async def mydb_cmd(_, m: Message):
    target = m.reply_to_message.from_user if m.reply_to_message else m.from_user
    owned  = await _get_owned(target.id)
    lines  = [
        f"{_team_dot(FIGHTERS[k]['team'])} **{FIGHTERS[k]['name']}** — "
        f"`{FIGHTERS[k]['base_power']:,}` PL | {FIGHTERS[k]['signature']}"
        for k in owned if k in FIGHTERS
    ]
    await m.reply(
        f"🥋 **{target.first_name}'s Dragon Ball Fighters**\n\n"
        f"🔵 Hero  |  🔴 Villain  |  ⚪ Neutral\n\n"
        + ("\n".join(lines) if lines else "_No fighters yet — use /dbshop!_")
        + f"\n\n`{len(owned)}/{len(FIGHTERS)}` fighters owned\n"
          f"💡 Buy more with `/dbshop`"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  FIGHT SYSTEM
# ══════════════════════════════════════════════════════════════════════════════

@_soul.app.on_message(filters.command(["dbfight", "dragonbattle", "dbattle"]))
async def dbfight_cmd(_, m: Message):
    if not m.reply_to_message:
        return await m.reply(
            "⚔️ **Dragon Ball Fight!**\n\n"
            "Reply to someone to challenge them!\n"
            "`/dbfight` (reply to opponent)\n\n"
            "💡 Buy fighters first with `/dbshop`!"
        )

    challenger = m.from_user
    opponent   = m.reply_to_message.from_user

    if opponent.id == challenger.id:
        return await m.reply("❌ Can't fight yourself!")
    if opponent.is_bot:
        return await m.reply("❌ Bots don't fight!")

    await _ensure_user(challenger)
    await _ensure_user(opponent)

    c_owned  = await _get_owned(challenger.id)
    fight_id = f"{challenger.id}v{opponent.id}_{int(datetime.utcnow().timestamp())}"

    _active_fights[fight_id] = {
        "challenger_id":          challenger.id,
        "challenger_name":        challenger.first_name,
        "opponent_id":            opponent.id,
        "opponent_name":          opponent.first_name,
        "challenger_char":        None,
        "opponent_char":          None,
        "challenger_hp":          100,
        "opponent_hp":            100,
        "challenger_transformed": False,
        "opponent_transformed":   False,
        "turn":  challenger.id,
        "round": 1,
        "state": "picking_challenger",
    }

    caption = (
        f"🐉 **DRAGON BALL FIGHT!** 🐉\n\n"
        f"🔵 **{challenger.first_name}**  ⚔️  **{opponent.first_name}** 🔴\n\n"
        f"🔵 Hero  |  🔴 Villain  |  ⚪ Neutral\n\n"
        f"**{challenger.first_name}** — pick your fighter:"
    )
    km = _owned_pick_keyboard(c_owned, fight_id, "c")
    if not await _safe_reply_video(
        m, FIGHTERS["goku"]["videos"]["idle"], caption, reply_markup=km
    ):
        await m.reply(caption, reply_markup=km)


@_soul.app.on_callback_query(filters.regex(r"^dbpick_page:([^:]+):([co]):(\d+)$"))
async def dbpick_page_cb(_, cq: CallbackQuery):
    parts    = cq.data.split(":")
    fight_id = parts[1]
    role     = parts[2]
    page     = int(parts[3])
    state    = _active_fights.get(fight_id)
    if not state:
        return await cq.answer("Fight expired!", show_alert=True)
    pick_uid = state["challenger_id"] if role == "c" else state["opponent_id"]
    if cq.from_user.id != pick_uid:
        return await cq.answer("Not your pick!", show_alert=True)
    owned = await _get_owned(pick_uid)
    try:
        await cq.message.edit_reply_markup(
            reply_markup=_owned_pick_keyboard(owned, fight_id, role, page)
        )
    except Exception:
        pass
    await cq.answer()


@_soul.app.on_callback_query(filters.regex(r"^dbpick:([^:]+):([co]):(\w+)$"))
async def dbpick_cb(_, cq: CallbackQuery):
    parts    = cq.data.split(":")
    key      = parts[-1]
    role     = parts[-2]
    fight_id = ":".join(parts[1:-2])

    state = _active_fights.get(fight_id)
    if not state:
        return await cq.answer("Fight expired!", show_alert=True)

    pick_uid = state["challenger_id"] if role == "c" else state["opponent_id"]
    if cq.from_user.id != pick_uid:
        return await cq.answer("Not your turn to pick!", show_alert=True)

    owned = await _get_owned(pick_uid)
    if key not in owned:
        return await cq.answer("You don't own this fighter! Use /dbshop", show_alert=True)
    char = FIGHTERS.get(key)
    if not char:
        return await cq.answer("Unknown fighter!", show_alert=True)

    upg   = await _get_upgrades(pick_uid)
    power = char["base_power"] + upg.get("bonus_power", 0)
    dot   = _team_dot(char["team"])

    if role == "c":
        state["challenger_char"] = key
        state["challenger_hp"]   = char["hp"]
        state["state"]           = "picking_opponent"
        o_owned = await _get_owned(state["opponent_id"])

        await cq.answer(f"You picked {char['name']}!")
        if char["videos"].get("idle"):
            await _safe_reply_video(
                cq.message, char["videos"]["idle"],
                caption=(
                    f"{dot} **{state['challenger_name']}** enters as "
                    f"**{char['emoji']} {char['name']}**!\nPL: `{power:,}`"
                ),
            )
        await _update_board(
            cq.message,
            f"✅ {dot} **{state['challenger_name']}** chose "
            f"**{char['emoji']} {char['name']}**!\n"
            f"⚡ PL: `{power:,}` | 💥 {char['signature']}\n\n"
            f"🔵 Hero  |  🔴 Villain  |  ⚪ Neutral\n\n"
            f"Now **{state['opponent_name']}** — pick your fighter:",
            _owned_pick_keyboard(o_owned, fight_id, "o"),
        )

    else:  # opponent picked
        state["opponent_char"] = key
        state["opponent_hp"]   = char["hp"]
        state["state"]         = "fighting"

        c_key   = state["challenger_char"]
        c_char  = FIGHTERS[c_key]
        c_upg   = await _get_upgrades(state["challenger_id"])
        o_upg   = await _get_upgrades(state["opponent_id"])
        c_power = c_char["base_power"] + c_upg.get("bonus_power", 0)
        o_power = char["base_power"]   + o_upg.get("bonus_power", 0)
        c_dot   = _team_dot(c_char["team"])

        await cq.answer(f"You picked {char['name']}! FIGHT!")
        if char["videos"].get("idle"):
            await _safe_reply_video(
                cq.message, char["videos"]["idle"],
                caption=(
                    f"{dot} **{state['opponent_name']}** enters as "
                    f"**{char['emoji']} {char['name']}**!\nPL: `{o_power:,}`"
                ),
            )

        c_bar = _hp_bar(state["challenger_hp"], c_char["hp"])
        o_bar = _hp_bar(state["opponent_hp"],   char["hp"])
        await _update_board(
            cq.message,
            f"⚡ **ROUND 1 — FIGHT!** ⚡\n\n"
            f"{c_dot} {c_char['emoji']} **{state['challenger_name']}** "
            f"({c_char['name']}) `{c_power:,}`\n"
            f"❤️ {c_bar} `{state['challenger_hp']}/{c_char['hp']}`\n\n"
            f"{dot} {char['emoji']} **{state['opponent_name']}** "
            f"({char['name']}) `{o_power:,}`\n"
            f"❤️ {o_bar} `{state['opponent_hp']}/{char['hp']}`\n\n"
            f"⚔️ **{state['challenger_name']}'s turn!**",
            _action_keyboard(fight_id, state["challenger_id"], False),
        )


# ── Fight Actions ─────────────────────────────────────────────────────────────

@_soul.app.on_callback_query(
    filters.regex(r"^dbact:([^:]+):(\d+):(attack|special|transform)$")
)
async def dbact_cb(_, cq: CallbackQuery):
    parts    = cq.data.split(":")
    action   = parts[-1]
    uid      = int(parts[-2])
    fight_id = ":".join(parts[1:-2])

    if cq.from_user.id != uid:
        return await cq.answer("It's not your turn!", show_alert=True)

    state = _active_fights.get(fight_id)
    if not state or state["state"] != "fighting":
        return await cq.answer("Fight not found or already ended!", show_alert=True)
    if state["turn"] != uid:
        return await cq.answer("It's not your turn!", show_alert=True)

    is_challenger    = (uid == state["challenger_id"])
    atk_key          = state["challenger_char"] if is_challenger else state["opponent_char"]
    def_key          = state["opponent_char"]   if is_challenger else state["challenger_char"]
    atk_char         = FIGHTERS[atk_key]
    def_char         = FIGHTERS[def_key]
    atk_name         = state["challenger_name"] if is_challenger else state["opponent_name"]
    def_name         = state["opponent_name"]   if is_challenger else state["challenger_name"]
    def_uid          = state["opponent_id"]     if is_challenger else state["challenger_id"]
    atk_transformed  = (
        state["challenger_transformed"] if is_challenger else state["opponent_transformed"]
    )
    atk_dot = _team_dot(atk_char["team"])
    def_dot = _team_dot(def_char["team"])

    atk_upg = await _get_upgrades(uid)
    def_upg = await _get_upgrades(def_uid)
    atk_pow = atk_char["base_power"] + atk_upg.get("bonus_power", 0)
    def_pow = def_char["base_power"] + def_upg.get("bonus_power", 0)
    if atk_transformed:
        atk_pow = int(atk_pow * atk_char["transform_power"])

    damage    = 0
    video_url = None
    narrative = ""

    if action == "transform":
        if atk_transformed:
            return await cq.answer("Already transformed!", show_alert=True)
        if is_challenger:
            state["challenger_transformed"] = True
        else:
            state["opponent_transformed"] = True
        video_url = atk_char["videos"].get("transform")
        narrative = (
            f"🌟 {atk_dot} **{atk_name}** TRANSFORMS into "
            f"**{atk_char['transform_name']}**!\n"
            f"⚡ Power: `{int(atk_pow * atk_char['transform_power']):,}` "
            f"×{atk_char['transform_power']}"
        )

    elif action == "special":
        if random.random() < 0.20:
            narrative = (
                f"💨 {atk_dot} **{atk_name}** fires **{atk_char['signature']}** "
                f"but {def_dot} **{def_name} DODGES!**"
            )
        else:
            mult      = random.uniform(1.5, 2.5)
            damage    = int(atk_pow / max(def_pow, 1) * mult * random.uniform(18, 30))
            damage    = max(8, min(damage, 45))
            video_url = atk_char["videos"].get("attack")
            narrative = (
                f"💥 {atk_dot} **{atk_name}** unleashes **{atk_char['signature']}**!\n"
                f"🔥 `-{damage} HP` to {def_dot} {def_name}!"
            )
    else:  # attack
        if random.random() < 0.15:
            narrative = (
                f"💨 {atk_dot} **{atk_name}** attacks but "
                f"{def_dot} **{def_name} DODGES!**"
            )
        else:
            mult   = random.uniform(0.8, 1.3)
            damage = int(atk_pow / max(def_pow, 1) * mult * random.uniform(10, 20))
            damage = max(3, min(damage, 30))
            narrative = (
                f"⚔️ {atk_dot} **{atk_name}** attacks! "
                f"`-{damage} HP` to {def_dot} {def_name}!"
            )

    if is_challenger:
        state["opponent_hp"]   = max(0, state["opponent_hp"]   - damage)
    else:
        state["challenger_hp"] = max(0, state["challenger_hp"] - damage)

    if video_url:
        sent = await _safe_reply_video(cq.message, video_url, caption=narrative)
        if not sent and narrative:
            await cq.message.reply(narrative)

    c_hp    = state["challenger_hp"]
    o_hp    = state["opponent_hp"]
    c_char_d= FIGHTERS[state["challenger_char"]]
    o_char_d= FIGHTERS[state["opponent_char"]]
    c_dot2  = _team_dot(c_char_d["team"])
    o_dot2  = _team_dot(o_char_d["team"])

    # ── Check if fight is over ────────────────────────────────────────────────
    if c_hp <= 0 or o_hp <= 0:
        state["state"] = "ended"
        outcome = (
            "draw"             if c_hp <= 0 and o_hp <= 0 else
            "opponent_wins"    if c_hp <= 0 else
            "challenger_wins"
        )
        await _resolve_fight(cq, state, outcome)
        _active_fights.pop(fight_id, None)
        return

    # ── Auto-transform at low HP ──────────────────────────────────────────────
    def_transformed = (
        state["opponent_transformed"] if is_challenger else state["challenger_transformed"]
    )
    def_hp_now  = o_hp if is_challenger else c_hp
    def_max_hp  = o_char_d["hp"] if is_challenger else c_char_d["hp"]
    if (not def_transformed and def_hp_now < def_max_hp * 0.35
            and random.random() < 0.65):
        if is_challenger:
            state["opponent_transformed"] = True
        else:
            state["challenger_transformed"] = True
        t_vid     = def_char["videos"].get("transform")
        t_caption = (
            f"😤 {def_dot} **{def_name}** won't go down!\n"
            f"🌟 TRANSFORMS into **{def_char['transform_name']}**!"
        )
        if t_vid:
            if not await _safe_reply_video(cq.message, t_vid, caption=t_caption):
                await cq.message.reply(t_caption)
        else:
            await cq.message.reply(t_caption)

    state["turn"]  = def_uid
    state["round"] += 1

    c_bar    = _hp_bar(c_hp, c_char_d["hp"])
    o_bar    = _hp_bar(o_hp, o_char_d["hp"])
    c_t      = "✨" if state["challenger_transformed"] else ""
    o_t      = "✨" if state["opponent_transformed"]   else ""
    next_uid = def_uid
    next_t   = (
        state["opponent_transformed"] if is_challenger else state["challenger_transformed"]
    )
    next_name = state["opponent_name"] if is_challenger else state["challenger_name"]
    hit_line  = f"\n💥 {narrative}" if narrative and not video_url else ""

    await _update_board(
        cq.message,
        f"⚡ **ROUND {state['round']}** ⚡{hit_line}\n\n"
        f"{c_dot2} {c_char_d['emoji']} **{state['challenger_name']}** {c_t}"
        f"({c_char_d['name']})\n"
        f"❤️ {c_bar} `{c_hp}/{c_char_d['hp']}`\n\n"
        f"{o_dot2} {o_char_d['emoji']} **{state['opponent_name']}** {o_t}"
        f"({o_char_d['name']})\n"
        f"❤️ {o_bar} `{o_hp}/{o_char_d['hp']}`\n\n"
        f"⚔️ **{next_name}'s turn!**",
        _action_keyboard(fight_id, next_uid, next_t),
    )
    await cq.answer()


# ── Resolve Fight ─────────────────────────────────────────────────────────────

async def _resolve_fight(cq: CallbackQuery, state: dict, outcome: str) -> None:
    c_uid  = state["challenger_id"]
    o_uid  = state["opponent_id"]
    c_name = state["challenger_name"]
    o_name = state["opponent_name"]
    c_char = FIGHTERS[state["challenger_char"]]
    o_char = FIGHTERS[state["opponent_char"]]
    c_dot  = _team_dot(c_char["team"])
    o_dot  = _team_dot(o_char["team"])

    KAKERA_WIN  = 300
    XP_WIN      = 150
    KAKERA_LOSE = 150

    if outcome == "draw":
        for uid in (c_uid, o_uid):
            await add_xp(uid, 50)
            await add_balance(uid, 50)
            await _col("db_fight_stats").update_one(
                {"user_id": uid}, {"$inc": {"draws": 1, "total": 1}}, upsert=True
            )
        await _update_board(
            cq.message,
            f"🤝 **DRAW!**\n\n"
            f"{c_dot} **{c_name}** ({c_char['name']}) vs "
            f"{o_dot} **{o_name}** ({o_char['name']})\n"
            f"Both earn ⭐ +50 XP | 💰 +50 kakera",
            markup(),   # empty markup — no buttons after game ends
        )
        return

    if outcome == "challenger_wins":
        w_uid, l_uid   = c_uid, o_uid
        w_name, l_name = c_name, o_name
        w_char, l_char = c_char, o_char
        w_dot,  l_dot  = c_dot,  o_dot
    else:
        w_uid, l_uid   = o_uid, c_uid
        w_name, l_name = o_name, c_name
        w_char, l_char = o_char, c_char
        w_dot,  l_dot  = o_dot,  c_dot

    await add_balance(w_uid, KAKERA_WIN)
    await add_xp(w_uid, XP_WIN)
    await deduct_balance(l_uid, KAKERA_LOSE)
    await add_xp(l_uid, 30)

    await _col("db_fight_stats").update_one(
        {"user_id": w_uid}, {"$inc": {"wins": 1, "total": 1}}, upsert=True
    )
    await _col("db_fight_stats").update_one(
        {"user_id": l_uid}, {"$inc": {"losses": 1, "total": 1}}, upsert=True
    )

    harem_line = ""
    try:
        harem_char = await get_random_character()
        if harem_char:
            await add_to_harem(w_uid, harem_char["id"])
            harem_line = (
                f"🎁 **{w_name}** captured **{harem_char['name']}** "
                f"from battle! Added to harem!\n"
            )
    except Exception as exc:
        log.debug("harem reward failed: %s", exc)

    if l_char["videos"].get("lose"):
        await _safe_reply_video(
            cq.message, l_char["videos"]["lose"],
            caption=f"💀 {l_dot} **{l_name}** has been defeated!",
        )
    if w_char["videos"].get("win"):
        await _safe_reply_video(
            cq.message, w_char["videos"]["win"],
            caption=f"🏆 {w_dot} **{w_name}** WINS!",
        )

    await _update_board(
        cq.message,
        f"🏆 **FIGHT OVER!** 🏆\n\n"
        f"🥇 {w_dot} **{w_name}** ({w_char['name']}) defeats "
        f"{l_dot} **{l_name}** ({l_char['name']})!\n\n"
        f"💰 **{w_name}**: +{KAKERA_WIN} kakera | ⭐ +{XP_WIN} XP\n"
        f"😔 **{l_name}**: -{KAKERA_LOSE} kakera | ⭐ +30 XP\n\n"
        f"{harem_line}"
        f"🔄 Use `/dbfight` to battle again!",
        markup(),
    )


# ══════════════════════════════════════════════════════════════════════════════
#  POWER UPGRADE
# ══════════════════════════════════════════════════════════════════════════════

@_soul.app.on_message(filters.command(["dbupgrade", "powerup", "plup"]))
async def dbupgrade_cmd(_, m: Message):
    uid   = m.from_user.id
    upg   = await _get_upgrades(uid)
    cur   = upg.get("tier", 0)
    bonus = upg.get("bonus_power", 0)
    bal   = await get_balance(uid)

    if cur >= len(UPGRADE_TIERS):
        return await m.reply(
            f"🌟 **MAX POWER REACHED!**\n\n"
            f"You've achieved **Ultra Instinct** tier!\n"
            f"⚡ Bonus Power: `+{bonus:,}`\n"
            f"💰 Total Spent: `{upg.get('total_spent', 0):,}` kakera"
        )

    next_t        = UPGRADE_TIERS[cur]
    tiers_display = "\n".join(
        f"{'✅' if i < cur else ('⏩' if i == cur else '🔒')} "
        f"**{t['label']}** — +{t['boost']:,} PL | `{t['cost']:,}` kakera"
        for i, t in enumerate(UPGRADE_TIERS)
    )
    await m.reply(
        f"⚡ **POWER UPGRADE SYSTEM** ⚡\n\n"
        f"💰 Balance: `{bal:,}` kakera\n"
        f"🔥 Current Bonus: `+{bonus:,}` power\n"
        f"🏆 Tier: **{_power_tier_name(9000 + bonus)}**\n\n"
        f"**Upgrade Tiers:**\n{tiers_display}\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⬆️ **Next: {next_t['label']}**\n"
        f"Cost: `{next_t['cost']:,}` kakera → +`{next_t['boost']:,}` power",
        reply_markup=markup(
            [B(f"⚡ Spend {next_t['cost']:,} Kakera", f"dbupg:{uid}:{cur}", _CS_GREEN)],
            [B("❌ Cancel",                            f"dbupg:{uid}:cancel",  _CS_RED)],
        ),
    )


@_soul.app.on_callback_query(filters.regex(r"^dbupg:(\d+):(\w+)$"))
async def dbupg_cb(_, cq: CallbackQuery):
    parts          = cq.data.split(":")
    uid, tier_str  = int(parts[1]), parts[2]
    if cq.from_user.id != uid:
        return await cq.answer("Not your upgrade!", show_alert=True)
    if tier_str == "cancel":
        await _safe_edit_text(cq.message, "❌ Upgrade cancelled.")
        return await cq.answer()

    cur_tier  = int(tier_str)
    upg       = await _get_upgrades(uid)
    if upg.get("tier", 0) != cur_tier:
        return await cq.answer("Re-run /dbupgrade", show_alert=True)

    tier_data = UPGRADE_TIERS[cur_tier]
    bal       = await get_balance(uid)
    if bal < tier_data["cost"]:
        return await cq.answer(
            f"❌ Need {tier_data['cost']:,} kakera, you have {bal:,}!", show_alert=True
        )

    await deduct_balance(uid, tier_data["cost"])
    await _col("db_upgrades").update_one(
        {"user_id": uid},
        {"$inc": {
            "tier":        1,
            "bonus_power": tier_data["boost"],
            "total_spent": tier_data["cost"],
        }},
        upsert=True,
    )
    upg       = await _get_upgrades(uid)
    new_bonus = upg["bonus_power"]
    maxed     = upg["tier"] >= len(UPGRADE_TIERS)
    await _safe_edit_text(
        cq.message,
        f"🌟 **POWER UP!** 🌟\n\n"
        f"✅ **{tier_data['label']}** unlocked!\n"
        f"⚡ Total Bonus Power: `+{new_bonus:,}`\n"
        f"🏆 Tier: **{_power_tier_name(9000 + new_bonus)}**\n\n"
        + (
            "🔥 **MAX POWER REACHED! You are unstoppable!**"
            if maxed else "Keep going with `/dbupgrade`!"
        ),
    )
    await cq.answer(f"Power up! +{tier_data['boost']:,} power!")


# ══════════════════════════════════════════════════════════════════════════════
#  DRAGON BALL COLLECTION
# ══════════════════════════════════════════════════════════════════════════════

@_soul.app.on_message(filters.command(["searchball", "sball", "findball"]))
async def search_ball(_, m: Message):
    uid = m.from_user.id
    await _ensure_user(m.from_user)

    doc      = await _get_user_balls(uid)
    last_s   = doc.get("last_search")
    now      = datetime.utcnow()
    cooldown = 3600

    if last_s:
        if isinstance(last_s, str):
            last_s = datetime.fromisoformat(last_s)
        elapsed = (now - last_s).total_seconds()
        if elapsed < cooldown:
            mins = int((cooldown - elapsed) // 60)
            return await m.reply(f"🔭 Search again in `{mins}m`...")

    if await _has_all_balls(uid):
        return await m.reply("✨ You have ALL 7! Use `/wish` to summon Shenron!")

    found = None
    if random.random() < 0.45:
        owned  = set(doc.get("balls", []))
        needed = [i for i in range(1, 8) if i not in owned]
        if needed:
            found = random.choice(needed)

    await _col("db_dragon_balls").update_one(
        {"user_id": uid}, {"$set": {"last_search": now}}, upsert=True
    )

    if found:
        await _col("db_dragon_balls").update_one(
            {"user_id": uid},
            {"$addToSet": {"balls": found}, "$inc": {"total_collected": 1}},
        )
        owned_now = set(doc.get("balls", [])) | {found}
        await m.reply(
            f"🐉 **Dragon Ball Found!**\n\n"
            f"{DRAGON_BALLS[found - 1]} **{BALL_NAMES[found - 1]} Ball** (#{found})\n\n"
            f"📦 Collection: `{len(owned_now)}/7`\n"
            + (
                "✨ **ALL 7! Use `/wish` now!**"
                if len(owned_now) >= 7
                else "🔭 Keep searching!"
            )
        )
    else:
        await m.reply("🌌 *Nothing found this time...*\n🔭 Try again in 1 hour!")


@_soul.app.on_message(filters.command(["dragonballs", "myballs", "balls"]))
async def my_balls(_, m: Message):
    target = m.reply_to_message.from_user if m.reply_to_message else m.from_user
    doc    = await _get_user_balls(target.id)
    owned  = sorted(set(doc.get("balls", [])))
    rows   = [
        f"{'✅' if i in owned else '❌'} "
        f"{DRAGON_BALLS[i - 1] if i in owned else ''} "
        f"{'**' + BALL_NAMES[i - 1] + '**' if i in owned else '_' + BALL_NAMES[i - 1] + '_'}"
        for i in range(1, 8)
    ]
    total = doc.get("total_collected", 0)
    await m.reply(
        f"🐉 **{target.first_name}'s Dragon Balls**\n\n"
        + "\n".join(rows)
        + f"\n\n"
        + (
            "✨ **ALL COLLECTED! Use `/wish`!**"
            if len(owned) >= 7
            else f"📦 `{len(owned)}/7` collected"
        )
        + f"\n🎯 Total ever found: `{total}`"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  WISH / SHENRON
# ══════════════════════════════════════════════════════════════════════════════

@_soul.app.on_message(filters.command("wish"))
async def wish_cmd(_, m: Message):
    uid = m.from_user.id
    if not await _has_all_balls(uid):
        doc   = await _get_user_balls(uid)
        owned = len(set(doc.get("balls", [])))
        return await m.reply(f"❌ Need all 7 Dragon Balls! You have `{owned}/7`.")

    last = await _col("db_wishes").find_one(
        {"user_id": uid}, sort=[("wished_at", -1)]
    )
    if last:
        elapsed = (datetime.utcnow() - last["wished_at"]).total_seconds()
        if elapsed < WISH_COOLDOWN:
            days  = int((WISH_COOLDOWN - elapsed) // 86400)
            hours = int(((WISH_COOLDOWN - elapsed) % 86400) // 3600)
            return await m.reply(
                f"⏳ Dragon Balls recharging for **{days}d {hours}h**."
            )

    wish_rows = [
        [B(v["label"], f"wish:{uid}:{k}", v["style"])]
        for k, v in WISHES.items()
    ]
    kb = _KB()
    for row in wish_rows:
        kb.row(*row)

    await m.reply(
        "🐉 **SHENRON AWAKENS!** 🐉\n\n"
        "*'I will grant you one wish...'*\n\n"
        "Choose your wish:",
        reply_markup=kb.build(),
    )


@_soul.app.on_callback_query(filters.regex(r"^wish:(\d+):(\w+)$"))
async def wish_cb(_, cq: CallbackQuery):
    parts         = cq.data.split(":")
    uid, wish_key = int(parts[1]), parts[2]
    if cq.from_user.id != uid:
        return await cq.answer("This isn't your wish!", show_alert=True)
    wish = WISHES.get(wish_key)
    if not wish:
        return await cq.answer("Unknown wish.", show_alert=True)

    await _col("db_dragon_balls").update_one(
        {"user_id": uid}, {"$set": {"balls": []}}, upsert=True
    )

    result = ""
    if wish_key == "kakera":
        await add_balance(uid, 10_000)
        result = "💰 **10,000 kakera** added!"
    elif wish_key == "xp":
        await add_xp(uid, 2_000)
        result = "⭐ **2,000 XP** gained!"
    elif wish_key == "fighter":
        owned   = await _get_owned(uid)
        unowned = [k for k in FIGHTERS if k not in owned and FIGHTERS[k]["price"] > 0]
        if unowned:
            gift_key = random.choice(unowned)
            await _add_owned(uid, gift_key)
            gift     = FIGHTERS[gift_key]
            result   = (
                f"🎁 You received {_team_dot(gift['team'])} "
                f"**{gift['emoji']} {gift['name']}** for free!"
            )
        else:
            await add_balance(uid, 5_000)
            result = "🎁 You own all fighters! +5,000 kakera instead!"
    elif wish_key == "immunity":
        result = "🛡 **1-week immunity** granted!"
    elif wish_key == "reroll":
        result = "🎲 Your next `/daily` gives **double** kakera!"

    await _col("db_wishes").insert_one(
        {"user_id": uid, "wish": wish_key, "wished_at": datetime.utcnow()}
    )
    await _safe_edit_text(
        cq.message,
        f"🌟 **Wish Granted!**\n\n{result}\n\n"
        "*'Until we meet again...'* — Shenron 🐉\n\n"
        "_Dragon Balls return in 7 days._",
    )
    await cq.answer()


# ══════════════════════════════════════════════════════════════════════════════
#  MISC COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

@_soul.app.on_message(filters.command(["powerlevel", "pl", "power"]))
async def power_level_cmd(_, m: Message):
    target = m.reply_to_message.from_user if m.reply_to_message else m.from_user
    upg    = await _get_upgrades(target.id)
    stats  = await _col("db_fight_stats").find_one({"user_id": target.id}) or {}
    bonus  = upg.get("bonus_power", 0)
    tier   = upg.get("tier", 0)
    owned  = await _get_owned(target.id)

    w   = stats.get("wins", 0)
    l   = stats.get("losses", 0)
    pct = int(w / max(w + l, 1) * 100)

    tier_label    = UPGRADE_TIERS[tier - 1]["label"] if tier > 0 else "No upgrades yet"
    stars         = "⭐" * min(tier + 1, 6)
    hero_count    = sum(1 for k in owned if FIGHTERS.get(k, {}).get("team") == "hero")
    villain_count = sum(1 for k in owned if FIGHTERS.get(k, {}).get("team") == "villain")
    neutral_count = sum(1 for k in owned if FIGHTERS.get(k, {}).get("team") == "neutral")

    await m.reply(
        f"⚡ **{target.first_name}'s Power** ⚡\n\n"
        f"⬆️ Bonus PL: `+{bonus:,}`\n"
        f"🏆 Tier: **{_power_tier_name(9000 + bonus)}** {stars}\n"
        f"💎 Upgrade: **{tier_label}**\n\n"
        f"🥋 Fighters: `{len(owned)}/{len(FIGHTERS)}`\n"
        f"🔵 Heroes: `{hero_count}`  "
        f"🔴 Villains: `{villain_count}`  "
        f"⚪ Neutral: `{neutral_count}`\n\n"
        f"📊 W/L: `{w}/{l}` ({pct}% win rate)\n\n"
        f"💡 `/dbupgrade` to power up | `/dbshop` to buy fighters"
    )


@_soul.app.on_message(filters.command(["dbtop", "fightlb", "battletop"]))
async def dbtop_cmd(_, m: Message):
    fighters = (
        await _col("db_fight_stats").find({}).sort("wins", -1).limit(10).to_list(10)
    )
    if not fighters:
        return await m.reply("🏆 No battles yet. Use `/dbfight` to start!")

    lines = []
    for i, f in enumerate(fighters, 1):
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"`{i}.`")
        uid   = f["user_id"]
        w     = f.get("wins", 0)
        l     = f.get("losses", 0)
        upg   = await _get_upgrades(uid)
        bonus = upg.get("bonus_power", 0)
        lines.append(
            f"{medal} [User](tg://user?id={uid})\n"
            f"   🏆 `{w}W/{l}L` | ⚡ `+{bonus:,}` PL"
        )
    await m.reply(
        "⚡ **Dragon Ball Fight Leaderboard** ⚡\n\n" + "\n\n".join(lines),
        disable_web_page_preview=True,
    )


@_soul.app.on_message(filters.command(["dbhelp", "dbcommands"]))
async def dbhelp_cmd(_, m: Message):
    uid = m.from_user.id
    await m.reply(
        "🐉 **Dragon Ball Game — Commands** 🐉\n\n"
        "🔵 Hero  |  🔴 Villain  |  ⚪ Neutral\n\n"
        "**🛒 Shop**\n"
        "`/dbshop` — Browse & buy fighters\n"
        "`/mydb` — See your owned fighters\n\n"
        "**⚔️ Fight**\n"
        "`/dbfight` — Challenge someone (reply to them)\n"
        "`/powerlevel` — Check your power stats\n"
        "`/dbtop` — Fight leaderboard\n\n"
        "**⚡ Upgrade**\n"
        "`/dbupgrade` — Spend kakera to boost all fighters\n\n"
        "**🐉 Dragon Balls**\n"
        "`/searchball` — Hunt for Dragon Balls (1hr cooldown)\n"
        "`/myballs` — Check your collection\n"
        "`/wish` — Summon Shenron with all 7 balls\n\n"
        "💡 Buy fighters from `/dbshop` first, then fight!",
        reply_markup=markup(
            [B("🛒 Open Shop",       f"dbshop_page:{uid}:0",    _CS_GREEN)],
            [B("🥋 My Collection",   f"dbshop_mine:{uid}",      _CS_BLUE),
             B("⚡ Power Up",         f"dbshop_pwrup:{uid}",     _CS_ORANGE)],
        ),
    )
