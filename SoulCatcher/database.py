"""
SoulCatcher/database.py
═══════════════════════════════════════════════════════════════════════════════
Complete async MongoDB layer.  Every data operation lives here.
No module should ever import motor / pymongo directly.

COLLECTIONS
  users                profile, economy, streaks, badges, level/xp
  characters           master catalogue
  user_characters      harem (owned instances)
  active_spawns        live unclaimed spawns
  drop_logs            per-group daily counters
  group_settings       per-group config
  market_listings      stock-based market (new schema)
  market_purchases     per-buyer purchase history + audit trail (new)
  wishlists            user wishlists (max 25)
  trades               trade sessions
  marriages            active marriages
  global_bans
  global_mutes
  sudo_users
  dev_users
  uploaders
  sequences            auto-increment char IDs
  top_groups           tracked group IDs

CHANGE LOG (vs original)
  • deduct_balance       → now fully atomic via find_one_and_update filter
  • count_characters     → fixed enabled=False branch (was always True)
  • get_or_create_user   → added last_claim, total_bought_market, xp_level, reward_claimed fields
  • add_xp               → now handles level-up and returns (new_xp, new_level, levelled_up)
  • _create_indexes      → added all market_listings stock indexes +
                           full market_purchases collection indexes
  • Market section       → replaced old single-copy functions with stock-based variants:
                               create_market_listing / get_market_listing /
                               update_market_listing / get_active_market_listings /
                               count_active_market_listings /
                               atomic_market_buy / log_market_purchase /
                               get_user_market_purchase_count / get_user_market_history /
                               market_aggregate_stats / top_market_listings
  • Kept all legacy helpers untouched so existing modules compile without changes
═══════════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, date
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient

from .config import MONGO_URI, DB_NAME

log = logging.getLogger("SoulCatcher.db")

_client = None
_db     = None


# ─────────────────────────────────────────────────────────────────────────────
#  Init
# ─────────────────────────────────────────────────────────────────────────────

async def init_db() -> None:
    global _client, _db
    _client = AsyncIOMotorClient(MONGO_URI)
    _db     = _client[DB_NAME]
    await _create_indexes()
    log.info("✅ MongoDB → %s", DB_NAME)


def get_db():
    return _db


def _col(name: str):
    return _db[name]


# ─────────────────────────────────────────────────────────────────────────────
#  Indexes
# ─────────────────────────────────────────────────────────────────────────────

async def _safe_create_index(collection, keys, **kwargs) -> None:
    """Create an index, dropping the old one first if there's a spec conflict (code 86)."""
    from pymongo.errors import OperationFailure
    try:
        await collection.create_index(keys, **kwargs)
    except OperationFailure as e:
        if e.code == 86:  # IndexKeySpecsConflict
            # Derive the auto-generated index name the same way MongoDB does
            if isinstance(keys, str):
                index_name = f"{keys}_1"
            else:
                index_name = "_".join(f"{k}_{v}" for k, v in keys)
            log.warning("⚠️  Index spec conflict on '%s' — dropping and recreating.", index_name)
            try:
                await collection.drop_index(index_name)
            except Exception:
                pass
            await collection.create_index(keys, **kwargs)
        else:
            raise


async def _create_indexes() -> None:
    # ── Users ──────────────────────────────────────────────────────────────────
    await _safe_create_index(_col("users"), "user_id",  unique=True)
    await _safe_create_index(_col("users"), "balance")
    await _safe_create_index(_col("users"), "xp")
    await _safe_create_index(_col("users"), "level")

    # ── Characters ─────────────────────────────────────────────────────────────
    await _safe_create_index(_col("characters"), "id",     unique=True)
    await _safe_create_index(_col("characters"), "rarity")
    await _safe_create_index(_col("characters"), "enabled")
    await _safe_create_index(_col("characters"), [("name", "text"), ("anime", "text")])

    # ── User characters (harem) ────────────────────────────────────────────────
    await _safe_create_index(_col("user_characters"), [("user_id", 1), ("instance_id", 1)], unique=True)
    await _safe_create_index(_col("user_characters"), [("user_id", 1), ("rarity", 1)])
    await _safe_create_index(_col("user_characters"), [("user_id", 1), ("char_id", 1)])
    await _safe_create_index(_col("user_characters"), "obtained_at")

    # ── Market listings (stock-based) ─────────────────────────────────────────
    await _safe_create_index(_col("market_listings"), "listing_id",  unique=True)
    await _safe_create_index(_col("market_listings"), "char_id")
    await _safe_create_index(_col("market_listings"), "status")
    await _safe_create_index(_col("market_listings"), "added_by")
    await _safe_create_index(_col("market_listings"), "added_at")
    await _safe_create_index(_col("market_listings"), [("status", 1), ("rarity", 1)])
    await _safe_create_index(_col("market_listings"), [("status", 1), ("added_at", -1)])
    await _safe_create_index(_col("market_listings"), [("status", 1), ("stock_remaining", 1)])

    # ── Market purchases ───────────────────────────────────────────────────────
    await _safe_create_index(_col("market_purchases"), "purchase_id",  unique=True)
    await _safe_create_index(_col("market_purchases"), "listing_id")
    await _safe_create_index(_col("market_purchases"), "buyer_id")
    await _safe_create_index(_col("market_purchases"), "char_id")
    await _safe_create_index(_col("market_purchases"), "purchased_at")
    await _safe_create_index(_col("market_purchases"), [("listing_id", 1), ("buyer_id", 1)])

    # ── Wishlists ─────────────────────────────────────────────────────────────
    await _safe_create_index(_col("wishlists"), [("user_id", 1), ("char_id", 1)], unique=True)

    # ── Spawns ────────────────────────────────────────────────────────────────
    await _safe_create_index(_col("active_spawns"), "spawn_id",  unique=True)
    await _safe_create_index(_col("active_spawns"), "chat_id")
    await _safe_create_index(_col("active_spawns"), "spawned_at")

    # ── Group settings ────────────────────────────────────────────────────────
    await _safe_create_index(_col("group_settings"), "chat_id",  unique=True)
    await _safe_create_index(_col("top_groups"), "group_id",     unique=True)

    # ── Drop logs ─────────────────────────────────────────────────────────────
    await _safe_create_index(_col("drop_logs"), [("chat_id", 1), ("rarity", 1), ("date", 1)])

    # ── Moderation ────────────────────────────────────────────────────────────
    await _safe_create_index(_col("global_bans"), "user_id",  unique=True)
    await _safe_create_index(_col("global_mutes"), "user_id", unique=True)

    # ── Trades ────────────────────────────────────────────────────────────────
    await _safe_create_index(_col("trades"), "trade_id")
    await _safe_create_index(_col("trades"), [("proposer_id", 1), ("status", 1)])
    await _safe_create_index(_col("trades"), [("receiver_id", 1), ("status", 1)])

    log.info("✅ Indexes ready")


# ═════════════════════════════════════════════════════════════════════════════
#  USERS
# ═════════════════════════════════════════════════════════════════════════════

async def get_or_create_user(
    user_id: int,
    username: str = "",
    first_name: str = "",
    last_name: str = "",
) -> dict:
    col  = _col("users")
    user = await col.find_one({"user_id": user_id})
    if not user:
        now  = datetime.utcnow()
        user = {
            # Identity
            "user_id":    user_id,
            "username":   username,
            "first_name": first_name,
            "last_name":  last_name,

            # Economy
            "balance":         0,
            "gold":            0.0,
            "rubies":          0.0,
            "saved_amount":    0,
            "loan_amount":     0,

            # Collection stats
            "total_claimed":       0,
            "total_married":       0,
            "marriage_count":      0,
            "total_bought_market": 0,   # ← market purchases counter

            # Progression
            "xp":         0,
            "level":      1,
            "xp_level":   1,            # mirrors level, kept separate for UI

            # Streaks & cooldowns
            "daily_streak": 0,
            "last_daily":   None,
            "last_spin":    None,
            "last_claim":   None,       # ← daily free character claim

            # One-time verse reward
            "reward_claimed":    False,
            "reward_claimed_at": None,

            # Preferences
            "badges":          [],
            "harem_sort":      "rarity",
            "collection_mode": "all",
            "favorites":       [],
            "custom_media":    None,

            # Moderation
            "is_banned":  False,
            "ban_reason": "",

            # Timestamps
            "joined_at":  now,
            "last_seen":  now,
            "created_at": now,
        }
        await col.insert_one(user)
    else:
        await col.update_one(
            {"user_id": user_id},
            {"$set": {
                "username":   username,
                "first_name": first_name,
                "last_name":  last_name,
                "last_seen":  datetime.utcnow(),
            }},
        )
    return user


async def get_user(user_id: int) -> Optional[dict]:
    return await _col("users").find_one({"user_id": user_id})


async def update_user(uid: int, upd: dict) -> None:
    await _col("users").update_one({"user_id": uid}, upd, upsert=True)


# ── Balance ───────────────────────────────────────────────────────────────────

async def get_balance(uid: int) -> int:
    u = await _col("users").find_one({"user_id": uid}, {"balance": 1})
    return int(u["balance"]) if u else 0


async def add_balance(uid: int, amt: int) -> None:
    """Add (or subtract if negative) kakera. Creates user if missing."""
    await _col("users").update_one(
        {"user_id": uid},
        {"$inc": {"balance": amt}},
        upsert=True,
    )


async def deduct_balance(uid: int, amt: int) -> bool:
    """
    Atomically deduct *amt* kakera only if the user has enough.

    Uses a single find_one_and_update with the balance condition inside the
    query filter — fully race-condition-free (no separate read + write).

    Returns True on success, False if insufficient funds.
    """
    result = await _col("users").find_one_and_update(
        {"user_id": uid, "balance": {"$gte": amt}},
        {"$inc": {"balance": -amt}},
    )
    return result is not None


async def deduct_balance_exact(uid: int, amt: int) -> bool:
    """Alias of deduct_balance for clarity in market/trade code."""
    return await deduct_balance(uid, amt)


# ── XP & Levels ──────────────────────────────────────────────────────────────

def xp_for_level(level: int) -> int:
    """Total XP required to *reach* the given level from zero."""
    return int(100 * (level ** 1.4))


async def add_xp(uid: int, xp: int) -> tuple[int, int, bool]:
    """
    Add XP to a user and handle level-ups.

    Returns (new_xp, new_level, levelled_up).
    Grants 50 kakera per level-up as a bonus.
    """
    user = await _col("users").find_one({"user_id": uid}, {"xp": 1, "level": 1})
    if not user:
        await _col("users").update_one(
            {"user_id": uid}, {"$set": {"xp": xp, "level": 1, "xp_level": 1}}, upsert=True
        )
        return xp, 1, False

    current_xp    = int(user.get("xp", 0)) + xp
    current_level = int(user.get("level", 1))
    new_level     = current_level
    levelled_up   = False

    # Check for level-up(s) — handles multiple level-ups in one call
    while current_xp >= xp_for_level(new_level + 1):
        new_level  += 1
        levelled_up = True

    updates: dict = {"xp": current_xp, "level": new_level, "xp_level": new_level}
    await _col("users").update_one({"user_id": uid}, {"$set": updates})

    if levelled_up:
        # Kakera bonus for levelling up
        bonus = 50 * (new_level - current_level)
        await add_balance(uid, bonus)
        log.info("LEVEL UP uid=%d  %d→%d  +%d kakera bonus", uid, current_level, new_level, bonus)

    return current_xp, new_level, levelled_up


# ── Ban / Mute ────────────────────────────────────────────────────────────────

async def is_user_banned(uid: int) -> bool:
    u = await _col("users").find_one({"user_id": uid}, {"is_banned": 1})
    return bool(u and u.get("is_banned"))


async def ban_user_db(uid: int, reason: str = "") -> None:
    await _col("users").update_one(
        {"user_id": uid},
        {"$set": {"is_banned": True, "ban_reason": reason}},
        upsert=True,
    )


async def reset_reward_claim(uid: int) -> None:
    """Reset a user's one-time verse reward so they can claim again."""
    await _col("users").update_one(
        {"user_id": uid},
        {"$set": {"reward_claimed": False, "reward_claimed_at": None}},
    )


async def unban_user_db(uid: int) -> None:
    await _col("users").update_one(
        {"user_id": uid},
        {"$set": {"is_banned": False, "ban_reason": ""}},
    )


# ── Bulk user helpers ─────────────────────────────────────────────────────────

async def get_all_user_ids() -> list[int]:
    docs = await _col("users").find({}, {"user_id": 1}).to_list(None)
    return [d["user_id"] for d in docs]


async def count_all_users() -> int:
    return await _col("users").count_documents({})


# ═════════════════════════════════════════════════════════════════════════════
#  CHARACTERS  (master catalogue)
# ═════════════════════════════════════════════════════════════════════════════

async def next_char_id() -> str:
    docs     = await _col("characters").find({"id": {"$exists": True}}, {"id": 1}).to_list(None)
    existing = [int(c["id"]) for c in docs if str(c.get("id", "")).isdigit()]
    seq      = await _col("sequences").find_one({"_id": "character_id"})
    nxt      = max(max(existing, default=0), seq["v"] if seq else 0) + 1
    await _col("sequences").update_one(
        {"_id": "character_id"}, {"$set": {"v": nxt}}, upsert=True
    )
    return str(nxt).zfill(4)


async def insert_character(doc: dict) -> str:
    doc["id"]       = await next_char_id()
    doc["enabled"]  = doc.get("enabled", True)
    doc["added_at"] = doc.get("added_at", datetime.utcnow())
    await _col("characters").insert_one(doc)
    return doc["id"]


async def get_character(char_id: str) -> Optional[dict]:
    return await _col("characters").find_one({"id": char_id})


async def update_character(cid: str, upd: dict) -> None:
    await _col("characters").update_one({"id": cid}, upd)


async def count_characters(enabled: bool = True) -> int:
    """
    Count characters.  enabled=True → only active chars.
                        enabled=False → ALL chars (active + disabled).
    """
    if enabled:
        return await _col("characters").count_documents({"enabled": True})
    return await _col("characters").count_documents({})


async def get_random_character(rarity_name: str) -> Optional[dict]:
    from .rarity import is_video_only
    match_filter: dict = {"rarity": rarity_name, "enabled": True}
    if is_video_only(rarity_name):
        match_filter["video_url"] = {"$nin": [None, ""]}
    res = await _col("characters").aggregate([
        {"$match": match_filter},
        {"$sample": {"size": 1}},
    ]).to_list(1)
    return res[0] if res else None


async def search_characters(query: str, limit: int = 10) -> list[dict]:
    return await _col("characters").find(
        {"$text": {"$search": query}, "enabled": True}
    ).limit(limit).to_list(limit)


# ═════════════════════════════════════════════════════════════════════════════
#  USER CHARACTERS  (harem — owned instances)
# ═════════════════════════════════════════════════════════════════════════════

async def add_to_harem(user_id: int, char: dict) -> str:
    """
    Add a character instance to a user's harem.
    Increments total_claimed counter.
    Returns the generated instance_id.
    """
    iid = str(uuid.uuid4())[:8].upper()
    await _col("user_characters").insert_one({
        "instance_id": iid,
        "user_id":     user_id,
        "char_id":     char["id"],
        "name":        char["name"],
        "anime":       char.get("anime", "Unknown"),
        "rarity":      char["rarity"],
        "img_url":     char.get("img_url", ""),
        "video_url":   char.get("video_url", ""),
        "is_favorite": False,
        "note":        "",
        "obtained_at": datetime.utcnow(),
    })
    await _col("users").update_one(
        {"user_id": user_id},
        {"$inc": {"total_claimed": 1}},
        upsert=True,
    )
    return iid


async def get_harem(
    user_id: int,
    page: int = 1,
    per_page: int = 10,
    sort_by: str = "rarity",
) -> tuple[list[dict], int]:
    SORT_MAP = {
        "rarity": [("rarity", 1), ("name", 1)],
        "name":   [("name", 1)],
        "anime":  [("anime", 1)],
        "recent": [("obtained_at", -1)],
    }
    col   = _col("user_characters")
    total = await col.count_documents({"user_id": user_id})
    skip  = (page - 1) * per_page
    chars = (
        await col.find({"user_id": user_id})
        .sort(SORT_MAP.get(sort_by, SORT_MAP["rarity"]))
        .skip(skip)
        .limit(per_page)
        .to_list(per_page)
    )
    return chars, total


async def get_harem_count(user_id: int) -> int:
    return await _col("user_characters").count_documents({"user_id": user_id})


async def get_harem_char(user_id: int, instance_id: str) -> Optional[dict]:
    return await _col("user_characters").find_one(
        {"user_id": user_id, "instance_id": instance_id}
    )


async def count_rarity_in_harem(user_id: int, rarity_name: str) -> int:
    return await _col("user_characters").count_documents(
        {"user_id": user_id, "rarity": rarity_name}
    )


async def remove_from_harem(user_id: int, instance_id: str) -> bool:
    res = await _col("user_characters").delete_one(
        {"user_id": user_id, "instance_id": instance_id}
    )
    return res.deleted_count > 0


async def transfer_harem_char(instance_id: str, from_uid: int, to_uid: int) -> bool:
    res = await _col("user_characters").update_one(
        {"instance_id": instance_id, "user_id": from_uid},
        {"$set": {"user_id": to_uid}},
    )
    return res.modified_count > 0


async def get_all_harem(user_id: int) -> list[dict]:
    return await _col("user_characters").find({"user_id": user_id}).to_list(9999)


async def get_harem_rarity_counts(user_id: int) -> dict[str, int]:
    rows = await _col("user_characters").aggregate([
        {"$match": {"user_id": user_id}},
        {"$group": {"_id": "$rarity", "count": {"$sum": 1}}},
    ]).to_list(50)
    return {r["_id"]: r["count"] for r in rows}


async def get_harem_char_by_name(user_id: int, name: str) -> Optional[dict]:
    return await _col("user_characters").find_one(
        {"user_id": user_id, "name": {"$regex": name, "$options": "i"}}
    )


# ═════════════════════════════════════════════════════════════════════════════
#  MARKET LISTINGS  (stock-based, new schema)
# ═════════════════════════════════════════════════════════════════════════════
#
#  Document schema:
#    listing_id       str      "MKT-XXXXXX"
#    char_id          str      catalogue ID
#    char_name        str
#    anime            str
#    rarity           str      rarity key
#    img_url          str
#    video_url        str
#    price            int      kakera per copy
#    stock_total      int      original stock
#    stock_sold       int      copies sold so far
#    stock_remaining  int      stock_total − stock_sold  (denormalised)
#    per_user_limit   int      0 = unlimited
#    added_by         int      user_id of uploader
#    added_at         datetime
#    status           str      "active" | "soldout" | "removed"
# ─────────────────────────────────────────────────────────────────────────────

async def create_market_listing(doc: dict) -> None:
    """Insert a new market listing document."""
    await _col("market_listings").insert_one(doc)


async def get_market_listing(listing_id: str) -> Optional[dict]:
    return await _col("market_listings").find_one({"listing_id": listing_id})


async def update_market_listing(listing_id: str, upd: dict) -> None:
    await _col("market_listings").update_one({"listing_id": listing_id}, upd)


async def get_active_market_listings(
    rarity: Optional[str] = None,
    skip: int = 0,
    limit: int = 6,
) -> list[dict]:
    filt: dict = {"status": "active"}
    if rarity:
        filt["rarity"] = rarity
    return (
        await _col("market_listings")
        .find(filt)
        .sort("added_at", -1)
        .skip(skip)
        .limit(limit)
        .to_list(limit)
    )


async def count_active_market_listings(rarity: Optional[str] = None) -> int:
    filt: dict = {"status": "active"}
    if rarity:
        filt["rarity"] = rarity
    return await _col("market_listings").count_documents(filt)


async def atomic_market_buy(listing_id: str) -> Optional[dict]:
    """
    Atomically decrement stock_remaining and increment stock_sold.
    Only succeeds when status='active' AND stock_remaining > 0.

    Automatically flips status to 'soldout' when stock hits 0.

    Returns the UPDATED document on success, None if unavailable.
    """
    updated = await _col("market_listings").find_one_and_update(
        {"listing_id": listing_id, "status": "active", "stock_remaining": {"$gt": 0}},
        {"$inc": {"stock_sold": 1, "stock_remaining": -1}},
        return_document=True,
    )
    if updated is None:
        return None

    # Auto-flip to soldout when stock hits zero
    if updated.get("stock_remaining", 0) <= 0:
        await _col("market_listings").update_one(
            {"listing_id": listing_id},
            {"$set": {"status": "soldout"}},
        )
        updated["status"] = "soldout"

    return updated


async def market_aggregate_stats() -> dict:
    """Return a stats dict used by /mstats."""
    total    = await _col("market_listings").count_documents({})
    active   = await _col("market_listings").count_documents({"status": "active"})
    soldout  = await _col("market_listings").count_documents({"status": "soldout"})
    removed  = await _col("market_listings").count_documents({"status": "removed"})
    purch    = await _col("market_purchases").count_documents({})

    pipeline = [{"$group": {"_id": None, "total": {"$sum": "$price"}}}]
    res = await _col("market_purchases").aggregate(pipeline).to_list(1)
    kakera_spent = res[0]["total"] if res else 0

    return {
        "total_listings":  total,
        "active":          active,
        "soldout":         soldout,
        "removed":         removed,
        "total_purchases": purch,
        "kakera_spent":    kakera_spent,
    }


async def top_market_listings(limit: int = 5) -> list[dict]:
    """Top listings by copies sold."""
    return (
        await _col("market_listings")
        .find({"stock_sold": {"$gt": 0}})
        .sort("stock_sold", -1)
        .limit(limit)
        .to_list(limit)
    )


# ═════════════════════════════════════════════════════════════════════════════
#  MARKET PURCHASES  (per-buyer audit trail)
# ═════════════════════════════════════════════════════════════════════════════
#
#  Document schema:
#    purchase_id   str
#    listing_id    str
#    buyer_id      int
#    char_id       str
#    char_name     str
#    instance_id   str      harem instance created
#    price         int      kakera paid
#    purchased_at  datetime
# ─────────────────────────────────────────────────────────────────────────────

async def log_market_purchase(doc: dict) -> None:
    """Insert a purchase record. Also bumps total_bought_market on the user."""
    await _col("market_purchases").insert_one(doc)
    await _col("users").update_one(
        {"user_id": doc["buyer_id"]},
        {"$inc": {"total_bought_market": 1}},
        upsert=True,
    )


async def get_user_market_purchase_count(listing_id: str, buyer_id: int) -> int:
    """How many times has buyer_id purchased from this specific listing."""
    return await _col("market_purchases").count_documents(
        {"listing_id": listing_id, "buyer_id": buyer_id}
    )


async def get_user_market_history(buyer_id: int, limit: int = 20) -> list[dict]:
    """Recent market purchases for a user, newest first."""
    return (
        await _col("market_purchases")
        .find({"buyer_id": buyer_id})
        .sort("purchased_at", -1)
        .limit(limit)
        .to_list(limit)
    )


# ── Legacy market helpers (kept for any code that still imports them) ─────────

async def create_listing(doc: dict) -> None:
    """Legacy alias → create_market_listing."""
    await create_market_listing(doc)


async def get_listing(lid: str) -> Optional[dict]:
    """Legacy alias → get_market_listing."""
    return await get_market_listing(lid)


async def update_listing(lid: str, upd: dict) -> None:
    """Legacy alias → update_market_listing."""
    await update_market_listing(lid, upd)


async def get_active_listings(rarity: Optional[str] = None, limit: int = 10) -> list[dict]:
    """Legacy alias → get_active_market_listings (uses added_at sort)."""
    return await get_active_market_listings(rarity=rarity, limit=limit)


async def atomic_buy_listing(listing_id: str, buyer_id: int) -> Optional[dict]:
    """
    Legacy atomic buy — single-copy model shim.
    Routes through the new atomic_market_buy.
    """
    return await atomic_market_buy(listing_id)


# ═════════════════════════════════════════════════════════════════════════════
#  SPAWNS
# ═════════════════════════════════════════════════════════════════════════════

async def create_spawn(chat_id: int, message_id: int, char: dict, rarity_name: str) -> str:
    spawn_id = str(uuid.uuid4())[:10].upper()
    await _col("active_spawns").insert_one({
        "spawn_id":   spawn_id,
        "chat_id":    chat_id,
        "message_id": message_id,
        "char_id":    char["id"],
        "char_name":  char["name"],
        "rarity":     rarity_name,
        "claimed":    False,
        "claimed_by": None,
        "expired":    False,
        "spawned_at": datetime.utcnow(),
    })
    return spawn_id


async def claim_spawn(spawn_id: str, user_id: int) -> Optional[dict]:
    return await _col("active_spawns").find_one_and_update(
        {"spawn_id": spawn_id, "claimed": False, "expired": False},
        {"$set": {
            "claimed":    True,
            "claimed_by": user_id,
            "claimed_at": datetime.utcnow(),
        }},
        return_document=True,
    )


async def expire_spawn(spawn_id: str) -> None:
    await _col("active_spawns").update_one(
        {"spawn_id": spawn_id},
        {"$set": {"expired": True}},
    )


async def unclaim_spawn(spawn_id: str) -> None:
    """Roll back a claim blocked by max_per_user."""
    await _col("active_spawns").update_one(
        {"spawn_id": spawn_id},
        {"$set": {"claimed": False, "claimed_by": None, "claimed_at": None}},
    )


# ═════════════════════════════════════════════════════════════════════════════
#  DROP LOGS  (per-group daily rarity counters)
# ═════════════════════════════════════════════════════════════════════════════

async def check_and_record_drop(chat_id: int, rarity_name: str) -> bool:
    """
    Check whether a rarity may still drop today (daily limit).
    If yes, record it and return True.  If limit reached, return False.
    """
    from .rarity import get_drop_limit
    limit = get_drop_limit(rarity_name)
    today = str(date.today())

    if limit == 0:
        await _col("drop_logs").update_one(
            {"chat_id": chat_id, "rarity": rarity_name, "date": today},
            {"$inc": {"count": 1}},
            upsert=True,
        )
        return True

    doc   = await _col("drop_logs").find_one(
        {"chat_id": chat_id, "rarity": rarity_name, "date": today}
    )
    count = doc["count"] if doc else 0
    if count >= limit:
        return False

    await _col("drop_logs").update_one(
        {"chat_id": chat_id, "rarity": rarity_name, "date": today},
        {"$inc": {"count": 1}},
        upsert=True,
    )
    return True


async def get_drop_counts_today(chat_id: int) -> dict[str, int]:
    today = str(date.today())
    rows  = await _col("drop_logs").find(
        {"chat_id": chat_id, "date": today}
    ).to_list(50)
    return {r["rarity"]: r["count"] for r in rows}


# ═════════════════════════════════════════════════════════════════════════════
#  GROUP SETTINGS
# ═════════════════════════════════════════════════════════════════════════════

async def get_group(chat_id: int) -> dict:
    g = await _col("group_settings").find_one({"chat_id": chat_id})
    if not g:
        from .rarity import SPAWN_SETTINGS
        g = {
            "chat_id":        chat_id,
            "spawn_enabled":  True,
            "spawn_cooldown": SPAWN_SETTINGS["cooldown_seconds"],
            "message_count":  0,
            "last_spawn":     None,
            "banned":         False,
        }
        await _col("group_settings").insert_one(g)
    return g


async def increment_group_msg(chat_id: int) -> int:
    res = await _col("group_settings").find_one_and_update(
        {"chat_id": chat_id},
        {"$inc": {"message_count": 1}},
        upsert=True,
        return_document=True,
    )
    return res["message_count"] if res else 1


async def reset_group_msg(chat_id: int) -> None:
    await _col("group_settings").update_one(
        {"chat_id": chat_id},
        {"$set": {"message_count": 0, "last_spawn": datetime.utcnow()}},
    )


async def set_group_spawn_limit(chat_id: int, limit: int) -> None:
    await _col("group_settings").update_one(
        {"chat_id": chat_id},
        {"$set": {"spawn_msg_limit": limit}},
        upsert=True,
    )
    log.info("Group %s: spawn_msg_limit → %s", chat_id, limit)


async def get_all_group_ids() -> list[int]:
    docs = await _col("group_settings").find({}, {"chat_id": 1}).to_list(None)
    return [d["chat_id"] for d in docs]


async def track_group(group_id: int, title: str = "") -> None:
    await _col("top_groups").update_one(
        {"group_id": group_id},
        {"$set": {"group_id": group_id, "title": title}},
        upsert=True,
    )


async def get_all_tracked_group_ids() -> list[int]:
    docs = await _col("top_groups").find({}, {"group_id": 1}).to_list(None)
    return [d["group_id"] for d in docs]


# ═════════════════════════════════════════════════════════════════════════════
#  WISHLIST
# ═════════════════════════════════════════════════════════════════════════════

async def add_wish(user_id: int, char_id: str, char_name: str, rarity: str) -> bool:
    if await _col("wishlists").find_one({"user_id": user_id, "char_id": char_id}):
        return False
    if await _col("wishlists").count_documents({"user_id": user_id}) >= 25:
        return False
    await _col("wishlists").insert_one({
        "user_id":   user_id,
        "char_id":   char_id,
        "char_name": char_name,
        "rarity":    rarity,
    })
    return True


async def remove_wish(user_id: int, char_id: str) -> bool:
    res = await _col("wishlists").delete_one({"user_id": user_id, "char_id": char_id})
    return res.deleted_count > 0


async def get_wishlist(user_id: int) -> list[dict]:
    return await _col("wishlists").find({"user_id": user_id}).to_list(25)


async def get_wishers(char_id: str, exclude_uid: int = 0) -> list[int]:
    docs = await _col("wishlists").find(
        {"char_id": char_id, "user_id": {"$ne": exclude_uid}}
    ).to_list(20)
    return [d["user_id"] for d in docs]


# ═════════════════════════════════════════════════════════════════════════════
#  TRADES
# ═════════════════════════════════════════════════════════════════════════════

async def create_trade(doc: dict) -> None:
    await _col("trades").insert_one(doc)


async def get_trade(tid: str) -> Optional[dict]:
    return await _col("trades").find_one({"trade_id": tid})


async def update_trade(tid: str, upd: dict) -> None:
    await _col("trades").update_one({"trade_id": tid}, upd)


# ═════════════════════════════════════════════════════════════════════════════
#  MARRIAGES
# ═════════════════════════════════════════════════════════════════════════════

async def get_marriage(uid: int) -> Optional[dict]:
    return await _col("marriages").find_one(
        {"$or": [{"user1": uid}, {"user2": uid}]}
    )


async def create_marriage(u1: int, u2: int) -> None:
    await _col("marriages").insert_one(
        {"user1": u1, "user2": u2, "married_at": datetime.utcnow()}
    )


async def divorce(uid: int) -> bool:
    res = await _col("marriages").delete_one(
        {"$or": [{"user1": uid}, {"user2": uid}]}
    )
    return res.deleted_count > 0


# ═════════════════════════════════════════════════════════════════════════════
#  GLOBAL BAN / MUTE
# ═════════════════════════════════════════════════════════════════════════════

async def add_to_global_ban(uid: int, reason: str, banned_by: int) -> None:
    await _col("global_bans").update_one(
        {"user_id": uid},
        {"$set": {
            "user_id":   uid,
            "reason":    reason,
            "banned_by": banned_by,
            "banned_at": datetime.utcnow(),
        }},
        upsert=True,
    )


async def remove_from_global_ban(uid: int) -> None:
    await _col("global_bans").delete_one({"user_id": uid})


async def is_user_globally_banned(uid: int) -> bool:
    return bool(await _col("global_bans").find_one({"user_id": uid}))


async def fetch_globally_banned_users() -> list[dict]:
    return await _col("global_bans").find({}).to_list(None)


async def add_to_global_mute(uid: int, reason: str, muted_by: int) -> None:
    await _col("global_mutes").update_one(
        {"user_id": uid},
        {"$set": {
            "user_id":  uid,
            "reason":   reason,
            "muted_by": muted_by,
            "muted_at": datetime.utcnow(),
        }},
        upsert=True,
    )


async def remove_from_global_mute(uid: int) -> None:
    await _col("global_mutes").delete_one({"user_id": uid})


async def is_user_globally_muted(uid: int) -> bool:
    return bool(await _col("global_mutes").find_one({"user_id": uid}))


async def fetch_globally_muted_users() -> list[dict]:
    return await _col("global_mutes").find({}).to_list(None)


async def get_all_chats() -> list[int]:
    uids   = await get_all_user_ids()
    grpids = await get_all_tracked_group_ids()
    return list(set(uids + grpids))


# ═════════════════════════════════════════════════════════════════════════════
#  SUDO / DEV / UPLOADER
# ═════════════════════════════════════════════════════════════════════════════

async def add_sudo(uid: int) -> None:
    await _col("sudo_users").update_one(
        {"user_id": uid}, {"$set": {"user_id": uid}}, upsert=True
    )


async def remove_sudo(uid: int) -> None:
    await _col("sudo_users").delete_one({"user_id": uid})


async def get_sudo_ids() -> list[int]:
    docs = await _col("sudo_users").find({}).to_list(None)
    return [d["user_id"] for d in docs]


async def is_sudo(uid: int) -> bool:
    return bool(await _col("sudo_users").find_one({"user_id": uid}))


async def add_dev(uid: int) -> None:
    await _col("dev_users").update_one(
        {"user_id": uid}, {"$set": {"user_id": uid}}, upsert=True
    )


async def remove_dev(uid: int) -> None:
    await _col("dev_users").delete_one({"user_id": uid})


async def get_dev_ids() -> list[int]:
    docs = await _col("dev_users").find({}).to_list(None)
    return [d["user_id"] for d in docs]


async def is_dev(uid: int) -> bool:
    return bool(await _col("dev_users").find_one({"user_id": uid}))


async def add_uploader(uid: int) -> None:
    await _col("uploaders").update_one(
        {"user_id": uid},
        {"$set": {"user_id": uid, "added_at": datetime.utcnow()}},
        upsert=True,
    )


async def remove_uploader(uid: int) -> None:
    await _col("uploaders").delete_one({"user_id": uid})


async def get_uploader_ids() -> list[int]:
    docs = await _col("uploaders").find({}).to_list(None)
    return [d["user_id"] for d in docs]


async def is_uploader(uid: int) -> bool:
    return bool(await _col("uploaders").find_one({"user_id": uid}))


# ═════════════════════════════════════════════════════════════════════════════
#  RANK
# ═════════════════════════════════════════════════════════════════════════════

async def count_user_rank(user_id: int) -> int:
    """Returns collector rank (1 = most characters owned)."""
    cnt = await _col("user_characters").count_documents({"user_id": user_id})
    pipeline = [
        {"$group": {"_id": "$user_id", "count": {"$sum": 1}}},
        {"$match": {"count": {"$gt": cnt}}},
        {"$count": "ahead"},
    ]
    res = await _col("user_characters").aggregate(pipeline).to_list(1)
    return (res[0]["ahead"] if res else 0) + 1


# ═════════════════════════════════════════════════════════════════════════════
#  LEADERBOARDS
# ═════════════════════════════════════════════════════════════════════════════

async def top_richest(limit: int = 10) -> list[dict]:
    """Top users by kakera balance."""
    return (
        await _col("users")
        .find({"balance": {"$gt": 0}})
        .sort("balance", -1)
        .limit(limit)
        .to_list(limit)
    )


async def top_collectors(limit: int = 10) -> list[dict]:
    """Top users by total characters owned, display names joined in."""
    pipeline = [
        {"$group": {"_id": "$user_id", "char_count": {"$sum": 1}}},
        {"$sort": {"char_count": -1}},
        {"$limit": limit},
        {"$lookup": {
            "from":         "users",
            "localField":   "_id",
            "foreignField": "user_id",
            "as":           "user_info",
        }},
        {"$unwind": {"path": "$user_info", "preserveNullAndEmptyArrays": True}},
        {"$project": {
            "_id":        0,
            "user_id":    "$_id",
            "char_count": 1,
            "first_name": {"$ifNull": ["$user_info.first_name", ""]},
            "username":   {"$ifNull": ["$user_info.username",   ""]},
        }},
    ]
    return await _col("user_characters").aggregate(pipeline).to_list(limit)


async def top_by_level(limit: int = 10) -> list[dict]:
    """Top users by XP level."""
    return (
        await _col("users")
        .find({"level": {"$gt": 1}})
        .sort([("level", -1), ("xp", -1)])
        .limit(limit)
        .to_list(limit)
    )
