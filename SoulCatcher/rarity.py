"""
╔══════════════════════════════════════════════════════════════════════════════╗
║        SoulCatcher 🌸  rarity.py  —  THE SINGLE SOURCE OF TRUTH             ║
║                                                                              ║
║  7 MAIN TIERS:                                                               ║
║   1 ⚫ Common       most frequent, unlimited                                 ║
║   2 🔵 Rare         noticeably better, limited daily drops                  ║
║   3 🌌 Cosmos       group must be somewhat active                            ║
║   4 🔥 Infernal     high-activity groups only, announced                    ║
║   5 💎 Crystal    + sub: 🌸 Seasonal   (holiday/event chars)                ║
║   6 🔴 Mythic     + sub: 🔮 Limited Edition (time-limited)                  ║
║   7 ✨ Eternal    + sub: 🎠 Cartoon  (western cartoon chars · VIDEO ONLY)   ║
║                                                                              ║
║  HOW TO CUSTOMISE:                                                           ║
║   • Add/edit main tier  → RARITIES dict                                     ║
║   • Add sub-rarity      → SUB_RARITIES, attach to parent.sub_rarities       ║
║   • Nothing else needs touching — all modules auto-load from here            ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import random

# ─────────────────────────────────────────────────────────────────────────────
# DATACLASS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RarityTier:
    # Identity
    id: int
    name: str
    display_name: str
    emoji: str
    color_hex: str

    # Drop mechanics
    weight: float
    drop_limit_per_day: int        # 0 = unlimited
    group_spawn_chance: float
    claim_window_seconds: int
    spawn_requires_activity: bool
    announce_spawn: bool

    # Content
    video_only: bool = False       # Tier 7 Eternal sub (Cartoon)

    # Economy
    sell_price_min: int = 50
    sell_price_max: int = 300
    kakera_reward: int = 10
    wishlist_ping: bool = False

    # Restrictions
    trade_allowed: bool = True
    gift_allowed: bool = True
    max_per_user: int = 0          # 0 = unlimited

    sub_rarities: list = field(default_factory=list)
    description: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# ★  7 MAIN TIERS  ★
# ─────────────────────────────────────────────────────────────────────────────

RARITIES: dict[str, RarityTier] = {

    # ── TIER 1 ─ Common ───────────────────────────────────────────────────────
    "common": RarityTier(
        id=1, name="common",
        display_name="Common", emoji="⚫", color_hex="#9E9E9E",
        weight=55.0, drop_limit_per_day=0,
        group_spawn_chance=0.55, claim_window_seconds=60,
        spawn_requires_activity=False, announce_spawn=False,
        sell_price_min=50,    sell_price_max=200,   kakera_reward=5,
        wishlist_ping=False, trade_allowed=True, gift_allowed=True, max_per_user=0,
        description="Drops constantly. Good starting point.",
    ),

    # ── TIER 2 ─ Rare ─────────────────────────────────────────────────────────
    "rare": RarityTier(
        id=2, name="rare",
        display_name="Rare", emoji="🔵", color_hex="#2196F3",
        weight=22.0, drop_limit_per_day=0,
        group_spawn_chance=0.22, claim_window_seconds=55,
        spawn_requires_activity=False, announce_spawn=False,
        sell_price_min=200,   sell_price_max=600,   kakera_reward=20,
        wishlist_ping=False, trade_allowed=True, gift_allowed=True, max_per_user=0,
        description="Noticeably better than Common. Still no daily limit.",
    ),

    # ── TIER 3 ─ Cosmos ───────────────────────────────────────────────────────
    "cosmos": RarityTier(
        id=3, name="cosmos",
        display_name="Cosmos", emoji="🌌", color_hex="#3F51B5",
        weight=10.0, drop_limit_per_day=30,
        group_spawn_chance=0.10, claim_window_seconds=48,
        spawn_requires_activity=False, announce_spawn=False,
        sell_price_min=700,   sell_price_max=1800,  kakera_reward=50,
        wishlist_ping=True,  trade_allowed=True, gift_allowed=True, max_per_user=0,
        description="Wishlist pings activate. Limited to 30/day/group.",
    ),

    # ── TIER 4 ─ Infernal ─────────────────────────────────────────────────────
    "infernal": RarityTier(
        id=4, name="infernal",
        display_name="Infernal", emoji="🔥", color_hex="#FF5722",
        weight=5.0, drop_limit_per_day=15,
        group_spawn_chance=0.05, claim_window_seconds=40,
        spawn_requires_activity=True, announce_spawn=True,
        sell_price_min=2000,  sell_price_max=6000,  kakera_reward=150,
        wishlist_ping=True, trade_allowed=True, gift_allowed=True, max_per_user=0,
        description="Announced on spawn. Needs active chat.",
    ),

    # ── TIER 5 ─ Crystal  (has sub-rarity: Seasonal) ──────────────────────────
    "crystal": RarityTier(
        id=5, name="crystal",
        display_name="Crystal", emoji="💎", color_hex="#00BCD4",
        weight=2.5, drop_limit_per_day=8,
        group_spawn_chance=0.022, claim_window_seconds=30,
        spawn_requires_activity=True, announce_spawn=True,
        sell_price_min=6000,  sell_price_max=15000, kakera_reward=400,
        wishlist_ping=True, trade_allowed=True, gift_allowed=True, max_per_user=5,
        description="Has 🌸 Seasonal sub-rarity. Max 5 per user.",
    ),

    # ── TIER 6 ─ Mythic  (has sub-rarity: Limited Edition) ────────────────────
    "mythic": RarityTier(
        id=6, name="mythic",
        display_name="Mythic", emoji="🔴", color_hex="#F44336",
        weight=0.8, drop_limit_per_day=3,
        group_spawn_chance=0.007, claim_window_seconds=20,
        spawn_requires_activity=True, announce_spawn=True,
        sell_price_min=18000, sell_price_max=45000, kakera_reward=1000,
        wishlist_ping=True, trade_allowed=False, gift_allowed=False, max_per_user=3,
        description="Cannot be traded/gifted. Has 🔮 Limited Edition sub. Max 3 per user.",
    ),

    # ── TIER 7 ─ Eternal  (has sub-rarity: Cartoon · VIDEO ONLY) ─────────────
    "eternal": RarityTier(
        id=7, name="eternal",
        display_name="✦ ETERNAL ✦", emoji="✨", color_hex="#FFD700",
        weight=0.10, drop_limit_per_day=1,
        group_spawn_chance=0.001, claim_window_seconds=12,
        spawn_requires_activity=True, announce_spawn=True,
        sell_price_min=60000, sell_price_max=120000, kakera_reward=4000,
        wishlist_ping=True, trade_allowed=False, gift_allowed=False, max_per_user=1,
        description="Ultra-rare. Has 🎠 Cartoon sub (VIDEO ONLY). Max 1 per user.",
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# SUB-RARITIES  (1 per high tier: Crystal / Mythic / Eternal)
# ─────────────────────────────────────────────────────────────────────────────

SUB_RARITIES: dict[str, RarityTier] = {

    # ── Crystal → 🌸 Seasonal ─────────────────────────────────────────────────
    "seasonal": RarityTier(
        id=51, name="seasonal",
        display_name="Seasonal", emoji="🌸", color_hex="#EC407A",
        weight=1.2, drop_limit_per_day=3,
        group_spawn_chance=0.012, claim_window_seconds=25,
        spawn_requires_activity=True, announce_spawn=True,
        sell_price_min=10000, sell_price_max=28000, kakera_reward=650,
        wishlist_ping=True, trade_allowed=True, gift_allowed=True, max_per_user=2,
        description="Holiday & seasonal event characters only. Spring / Summer / Winter / Autumn.",
    ),

    # ── Mythic → 🔮 Limited Edition ───────────────────────────────────────────
    "limited_edition": RarityTier(
        id=61, name="limited_edition",
        display_name="Limited Edition", emoji="🔮", color_hex="#7E57C2",
        weight=0.35, drop_limit_per_day=1,
        group_spawn_chance=0.003, claim_window_seconds=18,
        spawn_requires_activity=True, announce_spawn=True,
        sell_price_min=25000, sell_price_max=60000, kakera_reward=1500,
        wishlist_ping=True, trade_allowed=False, gift_allowed=False, max_per_user=1,
        description="Drops only during limited-time events. 1 per user. NOT tradeable.",
    ),

    # ── Eternal → 🎠 Cartoon (VIDEO ONLY) ────────────────────────────────────
    "cartoon": RarityTier(
        id=71, name="cartoon",
        display_name="Cartoon", emoji="🎠", color_hex="#FF9800",
        weight=0.04, drop_limit_per_day=1,
        group_spawn_chance=0.0004, claim_window_seconds=10,
        spawn_requires_activity=True, announce_spawn=True,
        video_only=True,           # ← WESTERN CARTOON CHARS, VIDEO FORMAT ONLY
        sell_price_min=100000, sell_price_max=250000, kakera_reward=8000,
        wishlist_ping=True, trade_allowed=False, gift_allowed=False, max_per_user=1,
        description="Western cartoon characters. VIDEO ONLY. 1 per user. The rarest drop in existence.",
    ),
}

# Attach sub-rarities to parents
RARITIES["crystal"].sub_rarities = [SUB_RARITIES["seasonal"]]
RARITIES["mythic"].sub_rarities   = [SUB_RARITIES["limited_edition"]]
RARITIES["eternal"].sub_rarities  = [SUB_RARITIES["cartoon"]]


# ─────────────────────────────────────────────────────────────────────────────
# NUMERIC MAP  (/upload <number> uses this)
# ─────────────────────────────────────────────────────────────────────────────

RARITY_ID_MAP: dict[int, RarityTier] = {}
for _r  in RARITIES.values():     RARITY_ID_MAP[_r.id]  = _r
for _sr in SUB_RARITIES.values(): RARITY_ID_MAP[_sr.id] = _sr

RARITY_LIST_TEXT = "\n".join(
    f"`{r.id:>2}` {r.emoji} {r.display_name}"
    + (" *(video only)*" if r.video_only else "")
    for r in sorted({**RARITIES, **SUB_RARITIES}.values(), key=lambda x: x.id)
)

# ─────────────────────────────────────────────────────────────────────────────
# SPAWN CONFIG
# ─────────────────────────────────────────────────────────────────────────────

SPAWN_SETTINGS = {
    "messages_per_spawn":        15,
    "activity_threshold":         5,
    "cooldown_seconds":          90,
    "expire_seconds":           300,
    "reveal_rarity_on_spawn":  False,   # ❓ mystery until claimed
    "sub_rarity_upgrade_chance": 0.28,  # 28% chance parent → sub
}

# ─────────────────────────────────────────────────────────────────────────────
# GAME MODES
# ─────────────────────────────────────────────────────────────────────────────

GAME_MODES: dict[str, dict] = {
    "normal":     {"weight_mult": 1.0, "kakera_mult": 1.0,  "label": "🌙 Normal"},
    "happy_hour": {"weight_mult": 1.8, "kakera_mult": 2.5,  "label": "🎉 Happy Hour"},
    "event":      {"weight_mult": 2.5, "kakera_mult": 2.0,  "label": "🎊 Event Mode"},
    "night":      {"weight_mult": 0.7, "kakera_mult": 1.5,  "label": "🌃 Night Mode"},
    "blitz":      {"weight_mult": 3.0, "kakera_mult": 1.0,  "label": "⚡ Blitz Mode"},
}
CURRENT_MODE = "normal"

# ─────────────────────────────────────────────────────────────────────────────
# ECONOMY
# ─────────────────────────────────────────────────────────────────────────────

ECONOMY = {
    "daily_base":            200,
    "daily_streak_bonus":     30,
    "daily_streak_max":       10,
    "quiz_reward":            35,
    "duel_win":              120,
    "duel_loss":              15,
    "spin_cooldown":        3600,
    "trade_fee_pct":           5,
    "market_listing_fee":     50,
    "basket_cooldown":        30,
    "basket_min_bet_pct":   0.07,
    "marry_success_chance": 0.50,
    "propose_success_chance":0.65,
    "transfer_fee_pct":        2,
}

# ─────────────────────────────────────────────────────────────────────────────
# HELPER FUNCTIONS  (imported by all modules)
# ─────────────────────────────────────────────────────────────────────────────

def get_all_rarities() -> list[RarityTier]:
    return list(RARITIES.values())

def get_all_sub_rarities() -> list[RarityTier]:
    return list(SUB_RARITIES.values())

def get_rarity(name: str) -> Optional[RarityTier]:
    return RARITIES.get(name) or SUB_RARITIES.get(name)

def get_rarity_by_id(rid: int) -> Optional[RarityTier]:
    return RARITY_ID_MAP.get(rid)

def rarity_display(name: str) -> str:
    r = get_rarity(name)
    return f"{r.emoji} {r.display_name}" if r else "❓ Unknown"

def roll_rarity() -> RarityTier:
    import SoulCatcher.rarity as _mod
    mode  = GAME_MODES.get(_mod.CURRENT_MODE, GAME_MODES["normal"])
    pool  = list(RARITIES.values())
    wgts  = [r.weight * mode["weight_mult"] for r in pool]
    return random.choices(pool, weights=wgts, k=1)[0]

def roll_sub_rarity(parent_name: str) -> Optional[RarityTier]:
    parent = get_rarity(parent_name)
    if not parent or not parent.sub_rarities:
        return None
    if random.random() < SPAWN_SETTINGS["sub_rarity_upgrade_chance"]:
        return random.choice(parent.sub_rarities)
    return None

def get_kakera_reward(name: str) -> int:
    import SoulCatcher.rarity as _mod
    mode = GAME_MODES.get(_mod.CURRENT_MODE, GAME_MODES["normal"])
    r    = get_rarity(name)
    return int(r.kakera_reward * mode["kakera_mult"]) if r else 5

def get_sell_price(name: str) -> int:
    r = get_rarity(name)
    return random.randint(r.sell_price_min, r.sell_price_max) if r else 50

def get_rarity_order() -> list[str]:
    all_r = {**RARITIES, **SUB_RARITIES}
    return sorted(all_r.keys(), key=lambda k: all_r[k].weight, reverse=True)

def is_video_only(name: str) -> bool:
    r = get_rarity(name); return r.video_only if r else False

def can_trade(name: str) -> bool:
    r = get_rarity(name); return r.trade_allowed if r else False

def can_gift(name: str) -> bool:
    r = get_rarity(name); return r.gift_allowed if r else False

def get_claim_window(name: str) -> int:
    r = get_rarity(name); return r.claim_window_seconds if r else 60

def get_drop_limit(name: str) -> int:
    r = get_rarity(name); return r.drop_limit_per_day if r else 0

def get_rarity_card(name: str) -> str:
    r = get_rarity(name)
    if not r: return "Unknown rarity."
    subs = ", ".join(f"{s.emoji} {s.display_name}" for s in r.sub_rarities) if r.sub_rarities else "None"
    return (
        f"{r.emoji} **{r.display_name}** (Tier {r.id})\n"
        f"  ├ Weight: `{r.weight}` | Daily limit: `{r.drop_limit_per_day or '∞'}`\n"
        f"  ├ Claim: `{r.claim_window_seconds}s` | Kakera: `{r.kakera_reward}`\n"
        f"  ├ Price: `{r.sell_price_min:,}–{r.sell_price_max:,}`\n"
        f"  ├ Trade: `{r.trade_allowed}` | Gift: `{r.gift_allowed}` | Max/user: `{r.max_per_user or '∞'}`\n"
        f"  ├ Video-only: `{r.video_only}` | Announce: `{r.announce_spawn}`\n"
        f"  ├ Sub-rarities: {subs}\n"
        f"  └ {r.description}"
    )
