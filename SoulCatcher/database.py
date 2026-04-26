"""
SoulCatcher/database.py — Complete async MongoDB data layer.

All DB operations live here. No module imports motor/pymongo directly.

Collections:
  users              — profile, economy, streaks, badges, xp/level
  characters         — master catalogue
  user_characters    — owned instances (harem)
  active_spawns      — live unclaimed spawns
  drop_logs          — per-group daily counters
  group_settings     — per-group config
  market_listings    — stock-based market
  market_purchases   — purchase audit trail
  wishlists          — user wishlists (max 25)
  trades             — trade sessions
  marriages          — active marriages
  global_bans
  global_mutes
  sudo_users
  dev_users
  uploaders
  sequences          — auto-increment char IDs
  top_groups         — tracked group IDs
  quizzes            — quiz sessions
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument

from .config import MONGO_URI, DB_NAME

log = logging.getLogger("SoulCatcher.db")

_client = None
_db     = None


# ── Init ──────────────────────────────────────────────────────────────────────

async def init_db() -> None:
    global _client, _db
    _client = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=30_000)
    _db     = _client[DB_NAME]
    await _create_indexes()
    log.info("✅ MongoDB connected → %s", DB_NAME)


def get_db():
    return _db


def _col(name: str):
    return _db[name]


# ── Indexes ───────────────────────────────────────────────────────────────────

def _index_name(keys, kwargs) -> str:
    return kwargs.get("name") or (
        f"{keys}_1" if isinstance(keys, str)
        else "_".join(f"{k}_{v}" for k, v in keys)
    )


def _index_name(keys, kwargs) -> str:
    return kwargs.get("name") or (
        f"{keys}_1" if isinstance(keys, str)
        else "_".join(f"{k}_{v}" for k, v in keys)
    )


async def _safe_index(col, keys, **kwargs) -> None:
    """Create an index, handling definition conflicts (code 86) gracefully.

    For unique indexes blocked by duplicate data (code 11000): we no longer
    attempt auto-deduplication here because patching key fields corrupts
    documents. Collections that can accumulate duplicates are cleared explicitly
    in _create_indexes() before their indexes are built.
    """
    from pymongo.errors import OperationFailure

    try:
        await col.create_index(keys, **kwargs)
    except OperationFailure as e:
        if e.code == 86:
            # Index definition changed — drop the old one and recreate.
            name = _index_name(keys, kwargs)
            log.warning("Index conflict on '%s' — dropping and recreating.", name)
            try:
                await col.drop_index(name)
                await col.create_index(keys, **kwargs)
            except Exception as ex:
                log.warning("Could not recreate index '%s': %s", name, ex)
        else:
            raise



async def _create_indexes() -> None:
    u   = _col("users")
    c   = _col("characters")
    uc  = _col("user_characters")
    sp  = _col("active_spawns")
    dl  = _col("drop_logs")
    gs  = _col("group_settings")
    ml  = _col("market_listings")
    mp  = _col("market_purchases")
    wl  = _col("wishlists")
    tr  = _col("trades")
    mar = _col("marriages")
    gb  = _col("global_bans")
    gm  = _col("global_mutes")
    su  = _col("sudo_users")
    dv  = _col("dev_users")
    up  = _col("uploaders")
    tg  = _col("top_groups")
    qz  = _col("quizzes")

    # users
    await u.delete_many({"user_id": None})
    await _safe_index(u, "user_id", unique=True)
    await _safe_index(u, [("balance", -1)])
    await _safe_index(u, [("level", -1)])
    await _safe_index(u, [("total_claimed", -1)])

    # characters
    await _safe_index(c, [("name", "text"), ("anime", "text")])
    await c.delete_many({"id": None})
    await _safe_index(c, "id", unique=True)
    await _safe_index(c, [("rarity", 1), ("enabled", 1)])

    # user_characters (harem)
    await _safe_index(uc, [("user_id", 1), ("rarity", 1)])
    await _safe_index(uc, [("user_id", 1), ("char_id", 1)])
    await uc.delete_many({"instance_id": None})
    await _safe_index(uc, "instance_id", unique=True)
    await _safe_index(uc, [("user_id", 1), ("obtained_at", -1)])

    # spawns — non-unique index; duplicate (chat_id, char_id) pairs are allowed
    # because uploaders replace characters and stale entries expire naturally.
    # Drop any pre-existing unique index on this pair before recreating it non-unique.
    try:
        await sp.drop_index("chat_id_1_char_id_1")
    except Exception:
        pass
    await _safe_index(sp, [("chat_id", 1), ("char_id", 1)])
    await _safe_index(sp, "expires_at")

    # drop logs
    await _safe_index(dl, [("chat_id", 1), ("rarity", 1), ("date", 1)], unique=True)

    # group settings
    await gs.delete_many({"chat_id": None})
    await _safe_index(gs, "chat_id", unique=True)

    # market
    await ml.delete_many({"listing_id": None})
    await _safe_index(ml, "listing_id", unique=True)
    await _safe_index(ml, [("status", 1), ("added_at", -1)])
    await _safe_index(ml, [("status", 1), ("rarity", 1)])
    await _safe_index(ml, [("char_id", 1), ("status", 1)])
    await _safe_index(mp, [("buyer_id", 1), ("listing_id", 1)])
    await _safe_index(mp, [("buyer_id", 1), ("purchased_at", -1)])

    # wishlists
    await _safe_index(wl, [("user_id", 1), ("char_id", 1)], unique=True)

    # trades
    await tr.delete_many({"trade_id": None})
    await _safe_index(tr, "trade_id", unique=True)

    # marriages
    await _safe_index(mar, [("user1", 1), ("user2", 1)])

    # moderation
    for col_obj in (gb, gm, su, dv, up):
        await col_obj.delete_many({"user_id": None})
        await _safe_index(col_obj, "user_id", unique=True)

    # top groups — purge any null/malformed chat_id docs before building unique index
    await tg.delete_many({"chat_id": None})
    await _safe_index(tg, "chat_id", unique=True)

    # quizzes
    await qz.delete_many({"chat_id": None})
    await _safe_index(qz, "chat_id", unique=True)

    log.info("✅ All indexes ready")


# ── Users ─────────────────────────────────────────────────────────────────────

async def get_or_create_user(
    user_id: int,
    username: str = "",
    first_name: str = "",
    last_name: str = "",
) -> dict:
    col  = _col("users")
    user = await col.find_one({"user_id": user_id})
    now  = datetime.utcnow()

    if not user:
        user = {
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

            # Collection
            "total_claimed":       0,
            "total_married":       0,
            "marriage_count":      0,
            "total_bought_market": 0,
            "total_gifted":        0,
            "total_traded":        0,

            # Progression
            "xp":       0,
            "level":    1,
            "xp_level": 1,

            # Streaks & cooldowns
            "daily_streak": 0,
            "last_daily":   None,
            "last_spin":    None,
            "last_claim":   None,
            "last_quiz":    None,

            # Preferences
            "badges":          [],
            "harem_sort":      "rarity",
            "collection_mode": "all",
            "favorites":       [],
            "custom_media":    None,
            "notifications":   True,

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
                "last_seen":  now,
            }},
        )

    return user


async def get_user(user_id: int) -> Optional[dict]:
    return await _col("users").find_one({"user_id": user_id})


async def update_user(uid: int, upd: dict) -> None:
    await _col("users").update_one({"user_id": uid}, upd, upsert=True)


async def get_all_user_ids() -> list[int]:
    docs = await _col("users").find({}, {"user_id": 1}).to_list(None)
    return [d["user_id"] for d in docs]


async def count_all_users() -> int:
    return await _col("users").count_documents({})


# ── Balance ───────────────────────────────────────────────────────────────────

async def get_balance(uid: int) -> int:
    u = await _col("users").find_one({"user_id": uid}, {"balance": 1})
    return int(u["balance"]) if u else 0


async def add_balance(uid: int, amt: int) -> None:
    await _col("users").update_one(
        {"user_id": uid},
        {"$inc": {"balance": amt}},
        upsert=True,
    )


async def deduct_balance(uid: int, amt: int) -> bool:
    """Atomically deduct amt kakera. Returns True on success."""
    result = await _col("users").find_one_and_update(
        {"user_id": uid, "balance": {"$gte": amt}},
        {"$inc": {"balance": -amt}},
    )
    return result is not None


# alias
deduct_balance_exact = deduct_balance


# ── XP & Levels ──────────────────────────────────────────────────────────────

def xp_for_level(level: int) -> int:
    return int(100 * (level ** 1.4))


async def add_xp(uid: int, xp: int) -> tuple[int, int, bool]:
    """
    Add XP and handle level-ups.
    Returns (new_xp, new_level, levelled_up).
    """
    user = await _col("users").find_one({"user_id": uid}, {"xp": 1, "level": 1})
    if not user:
        await _col("users").update_one(
            {"user_id": uid},
            {"$set": {"xp": xp, "level": 1, "xp_level": 1}},
            upsert=True,
        )
        return xp, 1, False

    current_xp    = int(user.get("xp", 0)) + xp
    current_level = int(user.get("level", 1))
    new_level     = current_level
    levelled_up   = False

    while current_xp >= xp_for_level(new_level + 1):
        new_level  += 1
        levelled_up = True

    await _col("users").update_one(
        {"user_id": uid},
        {"$set": {"xp": current_xp, "level": new_level, "xp_level": new_level}},
    )

    if levelled_up:
        bonus = 50 * (new_level - current_level)
        await add_balance(uid, bonus)
        log.info("LEVEL UP uid=%d %d→%d +%d kakera", uid, current_level, new_level, bonus)

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


async def unban_user_db(uid: int) -> None:
    await _col("users").update_one(
        {"user_id": uid},
        {"$set": {"is_banned": False, "ban_reason": ""}},
    )


# ── Characters ────────────────────────────────────────────────────────────────

async def next_char_id() -> str:
    seq = await _col("sequences").find_one({"_id": "character_id"})
    if seq is None:
        docs = await _col("characters").find({"id": {"$exists": True}}, {"id": 1}).to_list(None)
        existing = [int(c["id"]) for c in docs if str(c.get("id", "")).isdigit()]
        seed = max(existing, default=0)
        await _col("sequences").update_one(
            {"_id": "character_id"},
            {"$setOnInsert": {"v": seed}},
            upsert=True,
        )

    result = await _col("sequences").find_one_and_update(
        {"_id": "character_id"},
        {"$inc": {"v": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return str(result["v"]).zfill(4)


async def insert_character(doc: dict) -> str:
    doc["id"]       = await next_char_id()
    doc["enabled"]  = doc.get("enabled", True)
    doc["added_at"] = doc.get("added_at", datetime.utcnow())
    doc.setdefault("views", 0)
    doc.setdefault("claims", 0)
    await _col("characters").insert_one(doc)
    return doc["id"]


async def get_character(char_id: str) -> Optional[dict]:
    return await _col("characters").find_one({"id": char_id})


async def update_character(cid: str, upd: dict) -> None:
    await _col("characters").update_one({"id": cid}, upd)


async def delete_character(cid: str) -> bool:
    res = await _col("characters").delete_one({"id": cid})
    return res.deleted_count > 0


async def count_characters(enabled: bool = True) -> int:
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


async def get_characters_by_rarity(rarity: str, limit: int = 20) -> list[dict]:
    return await _col("characters").find(
        {"rarity": rarity, "enabled": True}
    ).limit(limit).to_list(limit)


async def increment_char_stat(char_id: str, field: str, amount: int = 1) -> None:
    await _col("characters").update_one({"id": char_id}, {"$inc": {field: amount}})


# ── Harem (user_characters) ───────────────────────────────────────────────────

async def add_to_harem(user_id: int, char: dict) -> str:
    from pymongo.errors import DuplicateKeyError as _DKE

    # Use the full UUID hex (32 chars) — collision probability is negligible.
    # The inner retry loop handles the astronomically rare case where two
    # concurrent inserts race to the same value.
    for _attempt in range(5):
        iid = uuid.uuid4().hex.upper()  # e.g. "A3F1B2C4D5E6F7A8B9C0D1E2F3A4B5C6"
        try:
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
            break  # success
        except _DKE:
            log.warning(
                "add_to_harem: instance_id collision on '%s' (attempt %d/5) — retrying",
                iid, _attempt + 1,
            )
    else:
        raise RuntimeError("add_to_harem: failed to generate a unique instance_id after 5 attempts")
    await _col("users").update_one(
        {"user_id": user_id},
        {"$inc": {"total_claimed": 1}},
        upsert=True,
    )
    await increment_char_stat(char["id"], "claims")
    return iid


SORT_MAP = {
    "rarity": [("rarity", 1), ("name", 1)],
    "name":   [("name", 1)],
    "anime":  [("anime", 1)],
    "recent": [("obtained_at", -1)],
}


async def get_harem(
    user_id: int,
    page: int = 1,
    per_page: int = 10,
    sort_by: str = "rarity",
) -> tuple[list[dict], int]:
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


async def set_favorite(user_id: int, instance_id: str, value: bool) -> bool:
    res = await _col("user_characters").update_one(
        {"user_id": user_id, "instance_id": instance_id},
        {"$set": {"is_favorite": value}},
    )
    return res.modified_count > 0


async def set_char_note(user_id: int, instance_id: str, note: str) -> bool:
    res = await _col("user_characters").update_one(
        {"user_id": user_id, "instance_id": instance_id},
        {"$set": {"note": note}},
    )
    return res.modified_count > 0


# ── Spawns ────────────────────────────────────────────────────────────────────

async def create_spawn(doc: dict) -> None:
    # Use replace_one+upsert instead of insert_one so that if a spawn for this
    # (chat_id, char_id) pair already exists (e.g. from a race condition or
    # bot restart), it is cleanly replaced rather than creating a duplicate that
    # would violate the unique index.
    await _col("active_spawns").replace_one(
        {"chat_id": doc["chat_id"], "char_id": doc["char_id"]},
        doc,
        upsert=True,
    )


async def get_spawn(chat_id: int) -> Optional[dict]:
    now = datetime.utcnow()
    return await _col("active_spawns").find_one(
        {"chat_id": chat_id, "expires_at": {"$gt": now}}
    )


async def get_spawn_by_char(chat_id: int, char_id: str) -> Optional[dict]:
    return await _col("active_spawns").find_one(
        {"chat_id": chat_id, "char_id": char_id}
    )


async def delete_spawn(chat_id: int) -> None:
    await _col("active_spawns").delete_many({"chat_id": chat_id})


async def delete_expired_spawns() -> int:
    res = await _col("active_spawns").delete_many(
        {"expires_at": {"$lt": datetime.utcnow()}}
    )
    return res.deleted_count


# ── Drop Logs ─────────────────────────────────────────────────────────────────

async def increment_drop_log(chat_id: int, rarity: str) -> int:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    doc = await _col("drop_logs").find_one_and_update(
        {"chat_id": chat_id, "rarity": rarity, "date": today},
        {"$inc": {"count": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc["count"]


async def get_drop_count(chat_id: int, rarity: str) -> int:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    doc = await _col("drop_logs").find_one(
        {"chat_id": chat_id, "rarity": rarity, "date": today}
    )
    return doc["count"] if doc else 0


# ── Group Settings ────────────────────────────────────────────────────────────

async def get_group_settings(chat_id: int) -> dict:
    doc = await _col("group_settings").find_one({"chat_id": chat_id})
    if not doc:
        doc = {
            "chat_id":           chat_id,
            "spawn_enabled":     True,
            "spawn_frequency":   15,
            "announcement_mode": True,
            "language":          "en",
            "created_at":        datetime.utcnow(),
        }
        await _col("group_settings").insert_one(doc)
    return doc


async def update_group_settings(chat_id: int, upd: dict) -> None:
    await _col("group_settings").update_one(
        {"chat_id": chat_id},
        {"$set": upd},
        upsert=True,
    )


# ── Wishlist ──────────────────────────────────────────────────────────────────

async def add_to_wishlist(user_id: int, char_id: str) -> bool:
    count = await _col("wishlists").count_documents({"user_id": user_id})
    if count >= 25:
        return False
    try:
        await _col("wishlists").insert_one({
            "user_id": user_id,
            "char_id": char_id,
            "added_at": datetime.utcnow(),
        })
        return True
    except Exception:
        return False


async def remove_from_wishlist(user_id: int, char_id: str) -> bool:
    res = await _col("wishlists").delete_one({"user_id": user_id, "char_id": char_id})
    return res.deleted_count > 0


async def get_wishlist(user_id: int) -> list[str]:
    docs = await _col("wishlists").find({"user_id": user_id}).to_list(25)
    return [d["char_id"] for d in docs]


async def is_in_wishlist(user_id: int, char_id: str) -> bool:
    doc = await _col("wishlists").find_one({"user_id": user_id, "char_id": char_id})
    return doc is not None


async def get_wishlist_users(char_id: str) -> list[int]:
    docs = await _col("wishlists").find({"char_id": char_id}).to_list(200)
    return [d["user_id"] for d in docs]


# ── Trades ────────────────────────────────────────────────────────────────────

async def create_trade(doc: dict) -> None:
    await _col("trades").insert_one(doc)


async def get_trade(trade_id: str) -> Optional[dict]:
    return await _col("trades").find_one({"trade_id": trade_id})


async def update_trade(trade_id: str, upd: dict) -> None:
    await _col("trades").update_one({"trade_id": trade_id}, upd)


async def get_pending_trade(user_id: int) -> Optional[dict]:
    return await _col("trades").find_one({
        "$or": [{"from_uid": user_id}, {"to_uid": user_id}],
        "status": "pending",
    })


# ── Marriages ─────────────────────────────────────────────────────────────────

async def get_marriage(user_id: int) -> Optional[dict]:
    return await _col("marriages").find_one({
        "$or": [{"user1": user_id}, {"user2": user_id}],
        "active": True,
    })


async def create_marriage(user1: int, user2: int) -> None:
    now = datetime.utcnow()
    await _col("marriages").insert_one({
        "user1": user1,
        "user2": user2,
        "active": True,
        "married_at": now,
    })
    for uid in (user1, user2):
        await _col("users").update_one(
            {"user_id": uid},
            {"$inc": {"total_married": 1, "marriage_count": 1}},
        )


async def end_marriage(user_id: int) -> bool:
    res = await _col("marriages").update_one(
        {"$or": [{"user1": user_id}, {"user2": user_id}], "active": True},
        {"$set": {"active": False, "ended_at": datetime.utcnow()}},
    )
    return res.modified_count > 0


# ── Market ────────────────────────────────────────────────────────────────────

async def create_market_listing(doc: dict) -> None:
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
    Atomically decrement stock and auto-flip to 'soldout' when stock hits 0.
    Returns updated doc on success, None if unavailable.
    """
    updated = await _col("market_listings").find_one_and_update(
        {"listing_id": listing_id, "status": "active", "stock_remaining": {"$gt": 0}},
        [
            {"$set": {
                "stock_sold":      {"$add": ["$stock_sold", 1]},
                "stock_remaining": {"$subtract": ["$stock_remaining", 1]},
                "status": {
                    "$cond": {
                        "if":   {"$lte": [{"$subtract": ["$stock_remaining", 1]}, 0]},
                        "then": "soldout",
                        "else": "active",
                    }
                },
            }}
        ],
        return_document=ReturnDocument.AFTER,
    )
    return updated


async def log_market_purchase(doc: dict) -> None:
    await _col("market_purchases").insert_one(doc)


async def get_user_market_purchase_count(buyer_id: int, listing_id: str) -> int:
    return await _col("market_purchases").count_documents(
        {"buyer_id": buyer_id, "listing_id": listing_id}
    )


async def get_user_market_history(buyer_id: int, limit: int = 10) -> list[dict]:
    return (
        await _col("market_purchases")
        .find({"buyer_id": buyer_id})
        .sort("purchased_at", -1)
        .limit(limit)
        .to_list(limit)
    )


async def market_aggregate_stats() -> dict:
    total   = await _col("market_listings").count_documents({})
    active  = await _col("market_listings").count_documents({"status": "active"})
    soldout = await _col("market_listings").count_documents({"status": "soldout"})
    removed = await _col("market_listings").count_documents({"status": "removed"})
    purch   = await _col("market_purchases").count_documents({})
    res     = await _col("market_purchases").aggregate(
        [{"$group": {"_id": None, "total": {"$sum": "$price"}}}]
    ).to_list(1)
    kakera_spent = res[0]["total"] if res else 0
    return {
        "total_listings":  total,
        "active":          active,
        "soldout":         soldout,
        "removed":         removed,
        "total_purchases": purch,
        "kakera_spent":    kakera_spent,
    }


async def top_market_listings(limit: int = 10) -> list[dict]:
    return (
        await _col("market_listings")
        .find({"status": {"$in": ["active", "soldout"]}})
        .sort("stock_sold", -1)
        .limit(limit)
        .to_list(limit)
    )


# ── Global Ban / Mute ─────────────────────────────────────────────────────────

async def add_global_ban(uid: int, reason: str = "") -> None:
    await _col("global_bans").update_one(
        {"user_id": uid},
        {"$set": {"user_id": uid, "reason": reason, "added_at": datetime.utcnow()}},
        upsert=True,
    )


async def remove_global_ban(uid: int) -> bool:
    res = await _col("global_bans").delete_one({"user_id": uid})
    return res.deleted_count > 0


async def is_globally_banned(uid: int) -> bool:
    return bool(await _col("global_bans").find_one({"user_id": uid}))


async def get_all_gbanned() -> list[dict]:
    return await _col("global_bans").find({}).to_list(None)


async def add_global_mute(uid: int, reason: str = "") -> None:
    await _col("global_mutes").update_one(
        {"user_id": uid},
        {"$set": {"user_id": uid, "reason": reason, "added_at": datetime.utcnow()}},
        upsert=True,
    )


async def remove_global_mute(uid: int) -> bool:
    res = await _col("global_mutes").delete_one({"user_id": uid})
    return res.deleted_count > 0


async def is_globally_muted(uid: int) -> bool:
    return bool(await _col("global_mutes").find_one({"user_id": uid}))


# ── Sudo / Dev / Uploader ─────────────────────────────────────────────────────

async def add_sudo(uid: int) -> None:
    await _col("sudo_users").update_one(
        {"user_id": uid},
        {"$set": {"user_id": uid, "added_at": datetime.utcnow()}},
        upsert=True,
    )


async def remove_sudo(uid: int) -> bool:
    res = await _col("sudo_users").delete_one({"user_id": uid})
    return res.deleted_count > 0


async def get_sudo_ids() -> list[int]:
    docs = await _col("sudo_users").find({}).to_list(None)
    return [d["user_id"] for d in docs]


async def add_dev(uid: int) -> None:
    await _col("dev_users").update_one(
        {"user_id": uid},
        {"$set": {"user_id": uid, "added_at": datetime.utcnow()}},
        upsert=True,
    )


async def remove_dev(uid: int) -> bool:
    res = await _col("dev_users").delete_one({"user_id": uid})
    return res.deleted_count > 0


async def get_dev_ids() -> list[int]:
    docs = await _col("dev_users").find({}).to_list(None)
    return [d["user_id"] for d in docs]


async def add_uploader(uid: int) -> None:
    await _col("uploaders").update_one(
        {"user_id": uid},
        {"$set": {"user_id": uid, "added_at": datetime.utcnow()}},
        upsert=True,
    )


async def remove_uploader(uid: int) -> bool:
    res = await _col("uploaders").delete_one({"user_id": uid})
    return res.deleted_count > 0


async def get_uploader_ids() -> list[int]:
    docs = await _col("uploaders").find({}).to_list(None)
    return [d["user_id"] for d in docs]


# ── Top Groups ────────────────────────────────────────────────────────────────

async def track_group(chat_id: int, title: str = "") -> None:
    await _col("top_groups").update_one(
        {"chat_id": chat_id},
        {"$set": {"chat_id": chat_id, "title": title, "last_seen": datetime.utcnow()}},
        upsert=True,
    )


async def get_all_group_ids() -> list[int]:
    docs = await _col("top_groups").find({}).to_list(None)
    return [d["chat_id"] for d in docs]


async def count_all_groups() -> int:
    return await _col("top_groups").count_documents({})


# ── Leaderboards ──────────────────────────────────────────────────────────────

async def get_top_users(field: str = "balance", limit: int = 10) -> list[dict]:
    return (
        await _col("users")
        .find({"is_banned": {"$ne": True}})
        .sort(field, -1)
        .limit(limit)
        .to_list(limit)
    )


async def get_top_collectors(limit: int = 10) -> list[dict]:
    return await get_top_users("total_claimed", limit)


# ── Quizzes ───────────────────────────────────────────────────────────────────

async def get_active_quiz(chat_id: int) -> Optional[dict]:
    return await _col("quizzes").find_one({"chat_id": chat_id, "active": True})


async def create_quiz(doc: dict) -> None:
    await _col("quizzes").insert_one(doc)


async def end_quiz(chat_id: int) -> None:
    await _col("quizzes").update_one(
        {"chat_id": chat_id, "active": True},
        {"$set": {"active": False, "ended_at": datetime.utcnow()}},
    )


async def delete_quiz(chat_id: int) -> None:
    await _col("quizzes").delete_many({"chat_id": chat_id})
