"""
SoulCatcher/database.py
═══════════════════════════════════════════════════════════════════════
Complete MongoDB async layer. Every data operation lives here.

COLLECTIONS:
  users              profile, economy, streaks, badges
  characters         master catalogue
  user_characters    harem (owned instances)
  active_spawns      live unclaimed spawns
  drop_logs          per-group daily counters
  group_settings     per-group config
  market_listings    active/sold market
  wishlists          user wishlists (max 25)
  trades             trade sessions
  marriages          active marriages
  global_bans
  global_mutes
  sudo_users
  dev_users
  uploaders
  sequences          auto-increment char IDs
  top_groups         tracked group IDs
═══════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations
import uuid, logging
from datetime import datetime, date
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from .config import MONGO_URI, DB_NAME

log = logging.getLogger("SoulCatcher.db")
_client = None
_db     = None


async def init_db():
    global _client, _db
    _client = AsyncIOMotorClient(MONGO_URI)
    _db     = _client[DB_NAME]
    await _create_indexes()
    log.info(f"✅ MongoDB → {DB_NAME}")

def get_db():   return _db
def _col(name): return _db[name]


async def _create_indexes():
    await _col("users").create_index("user_id", unique=True)
    await _col("users").create_index([("is_banned", 1), ("balance", -1)])
    await _col("characters").create_index("id", unique=True)
    await _col("characters").create_index("rarity")
    await _col("characters").create_index([("name", "text"), ("anime", "text")])
    await _col("user_characters").create_index([("user_id", 1), ("instance_id", 1)])
    await _col("user_characters").create_index([("user_id", 1), ("rarity", 1)])
    await _col("user_characters").create_index([("user_id", 1), ("char_id", 1)])
    await _col("market_listings").create_index("listing_id", unique=True)
    await _col("market_listings").create_index([("status", 1), ("rarity", 1)])
    await _col("wishlists").create_index([("user_id", 1), ("char_id", 1)])
    await _col("active_spawns").create_index("spawn_id", unique=True)
    await _col("active_spawns").create_index("chat_id")
    await _col("group_settings").create_index("chat_id", unique=True)
    await _col("top_groups").create_index("group_id", unique=True)
    await _col("drop_logs").create_index([("chat_id", 1), ("rarity", 1), ("date", 1)])
    await _col("global_bans").create_index("user_id", unique=True)
    await _col("global_mutes").create_index("user_id", unique=True)
    log.info("✅ Indexes ready")


# ── USERS ─────────────────────────────────────────────────────────────────────

async def get_or_create_user(user_id, username="", first_name="", last_name=""):
    col  = _col("users")
    user = await col.find_one({"user_id": user_id})
    if not user:
        now  = datetime.utcnow()
        user = {
            "user_id": user_id, "username": username,
            "first_name": first_name, "last_name": last_name,
            "balance": 0, "gold": 0.0, "rubies": 0.0,
            "saved_amount": 0, "loan_amount": 0,
            "total_claimed": 0, "total_married": 0, "marriage_count": 0,
            "xp": 0, "level": 1, "daily_streak": 0,
            "last_daily": None, "last_spin": None,
            "badges": [], "harem_sort": "rarity", "collection_mode": "all", "custom_media": None,
            "is_banned": False, "ban_reason": "",
            "joined_at": now, "last_seen": now, "created_at": now,
        }
        await col.insert_one(user)
    else:
        await col.update_one({"user_id": user_id}, {"$set": {
            "username": username, "first_name": first_name,
            "last_name": last_name, "last_seen": datetime.utcnow(),
        }})
    return user

async def get_user(user_id):     return await _col("users").find_one({"user_id": user_id})
async def update_user(uid, upd): await _col("users").update_one({"user_id": uid}, upd, upsert=True)

async def get_balance(uid):
    u = await _col("users").find_one({"user_id": uid}, {"balance": 1})
    return u["balance"] if u else 0

async def add_balance(uid, amt):
    await _col("users").update_one({"user_id": uid}, {"$inc": {"balance": amt}}, upsert=True)

async def deduct_balance(uid, amt):
    if await get_balance(uid) < amt: return False
    await _col("users").update_one({"user_id": uid}, {"$inc": {"balance": -amt}})
    return True

async def add_xp(uid, xp):
    await _col("users").update_one({"user_id": uid}, {"$inc": {"xp": xp}}, upsert=True)

async def is_user_banned(uid):
    u = await _col("users").find_one({"user_id": uid}, {"is_banned": 1})
    return bool(u and u.get("is_banned"))

async def ban_user_db(uid, reason=""):
    await _col("users").update_one({"user_id": uid}, {"$set": {"is_banned": True, "ban_reason": reason}}, upsert=True)

async def unban_user_db(uid):
    await _col("users").update_one({"user_id": uid}, {"$set": {"is_banned": False, "ban_reason": ""}})

async def get_all_user_ids():
    docs = await _col("users").find({}, {"user_id": 1}).to_list(None)
    return [d["user_id"] for d in docs]

async def count_all_users():
    return await _col("users").count_documents({})


# ── CHARACTERS ────────────────────────────────────────────────────────────────

async def next_char_id():
    docs = await _col("characters").find({"id": {"$exists": True}}, {"id": 1}).to_list(None)
    existing = [int(c["id"]) for c in docs if str(c.get("id", "")).isdigit()]
    seq  = await _col("sequences").find_one({"_id": "character_id"})
    nxt  = max(max(existing, default=0), seq["v"] if seq else 0) + 1
    await _col("sequences").update_one({"_id": "character_id"}, {"$set": {"v": nxt}}, upsert=True)
    return str(nxt).zfill(4)

async def insert_character(doc):
    doc["id"]       = await next_char_id()
    doc["enabled"]  = doc.get("enabled", True)
    doc["added_at"] = doc.get("added_at", datetime.utcnow())
    await _col("characters").insert_one(doc)
    return doc["id"]

async def get_character(char_id):     return await _col("characters").find_one({"id": char_id})
async def update_character(cid, upd): await _col("characters").update_one({"id": cid}, upd)

async def count_characters(enabled=True):
    return await _col("characters").count_documents({"enabled": True} if enabled else {})

async def get_random_character(rarity_name):
    from .rarity import is_video_only
    match_filter: dict = {"rarity": rarity_name, "enabled": True}
    if is_video_only(rarity_name):
        match_filter["video_url"] = {"$nin": [None, ""]}
    res = await _col("characters").aggregate([
        {"$match": match_filter},
        {"$sample": {"size": 1}},
    ]).to_list(1)
    return res[0] if res else None

async def search_characters(query, limit=10):
    return await _col("characters").find(
        {"$text": {"$search": query}, "enabled": True}
    ).limit(limit).to_list(limit)


# ── USER CHARACTERS (HAREM) ───────────────────────────────────────────────────

async def add_to_harem(user_id, char):
    iid = str(uuid.uuid4())[:8].upper()
    await _col("user_characters").insert_one({
        "instance_id": iid, "user_id": user_id,
        "char_id": char["id"], "name": char["name"],
        "anime": char.get("anime", "Unknown"), "rarity": char["rarity"],
        "img_url": char.get("img_url", ""), "video_url": char.get("video_url", ""),
        "is_favorite": False, "note": "", "obtained_at": datetime.utcnow(),
    })
    await _col("users").update_one({"user_id": user_id}, {"$inc": {"total_claimed": 1}}, upsert=True)
    return iid

async def get_harem(user_id, page=1, per_page=10, sort_by="rarity"):
    SORT_MAP = {
        "rarity": [("rarity", 1), ("name", 1)],
        "name":   [("name", 1)],
        "anime":  [("anime", 1)],
        "recent": [("obtained_at", -1)],
    }
    col   = _col("user_characters")
    total = await col.count_documents({"user_id": user_id})
    skip  = (page - 1) * per_page
    chars = await col.find({"user_id": user_id}).sort(
        SORT_MAP.get(sort_by, SORT_MAP["rarity"])
    ).skip(skip).limit(per_page).to_list(per_page)
    return chars, total

async def get_harem_char(user_id, instance_id):
    return await _col("user_characters").find_one({"user_id": user_id, "instance_id": instance_id})

async def count_rarity_in_harem(user_id, rarity_name):
    return await _col("user_characters").count_documents({"user_id": user_id, "rarity": rarity_name})

async def remove_from_harem(user_id, instance_id):
    res = await _col("user_characters").delete_one({"user_id": user_id, "instance_id": instance_id})
    return res.deleted_count > 0

async def transfer_harem_char(instance_id, from_uid, to_uid):
    res = await _col("user_characters").update_one(
        {"instance_id": instance_id, "user_id": from_uid},
        {"$set": {"user_id": to_uid}})
    return res.modified_count > 0

async def get_all_harem(user_id):
    return await _col("user_characters").find({"user_id": user_id}).to_list(9999)

async def get_harem_rarity_counts(user_id):
    rows = await _col("user_characters").aggregate([
        {"$match": {"user_id": user_id}},
        {"$group": {"_id": "$rarity", "count": {"$sum": 1}}},
    ]).to_list(50)
    return {r["_id"]: r["count"] for r in rows}

async def get_harem_char_by_name(user_id, name):
    return await _col("user_characters").find_one(
        {"user_id": user_id, "name": {"$regex": name, "$options": "i"}})


# ── SPAWNS ────────────────────────────────────────────────────────────────────

async def create_spawn(chat_id, message_id, char, rarity_name):
    spawn_id = str(uuid.uuid4())[:10].upper()
    await _col("active_spawns").insert_one({
        "spawn_id": spawn_id, "chat_id": chat_id, "message_id": message_id,
        "char_id": char["id"], "char_name": char["name"],
        "rarity": rarity_name, "claimed": False,
        "claimed_by": None, "expired": False, "spawned_at": datetime.utcnow(),
    })
    return spawn_id

async def claim_spawn(spawn_id, user_id):
    return await _col("active_spawns").find_one_and_update(
        {"spawn_id": spawn_id, "claimed": False, "expired": False},
        {"$set": {"claimed": True, "claimed_by": user_id, "claimed_at": datetime.utcnow()}},
        return_document=True)

async def expire_spawn(spawn_id):
    await _col("active_spawns").update_one({"spawn_id": spawn_id}, {"$set": {"expired": True}})

async def unclaim_spawn(spawn_id):
    """Roll back a claim blocked by max_per_user.
    Sets claimed=False so another user can still grab the character.
    Referenced by spawn.py claim_cb.
    """
    await _col("active_spawns").update_one(
        {"spawn_id": spawn_id},
        {"$set": {"claimed": False, "claimed_by": None, "claimed_at": None}}
    )


# ── DROP LOGS ─────────────────────────────────────────────────────────────────

async def check_and_record_drop(chat_id, rarity_name):
    from .rarity import get_drop_limit
    limit = get_drop_limit(rarity_name)
    today = str(date.today())
    if limit == 0:
        await _col("drop_logs").update_one(
            {"chat_id": chat_id, "rarity": rarity_name, "date": today},
            {"$inc": {"count": 1}}, upsert=True)
        return True
    doc   = await _col("drop_logs").find_one({"chat_id": chat_id, "rarity": rarity_name, "date": today})
    count = doc["count"] if doc else 0
    if count >= limit: return False
    await _col("drop_logs").update_one(
        {"chat_id": chat_id, "rarity": rarity_name, "date": today},
        {"$inc": {"count": 1}}, upsert=True)
    return True

async def get_drop_counts_today(chat_id):
    today = str(date.today())
    rows  = await _col("drop_logs").find({"chat_id": chat_id, "date": today}).to_list(50)
    return {r["rarity"]: r["count"] for r in rows}


# ── GROUP SETTINGS ────────────────────────────────────────────────────────────

async def get_group(chat_id):
    g = await _col("group_settings").find_one({"chat_id": chat_id})
    if not g:
        from .rarity import SPAWN_SETTINGS
        g = {"chat_id": chat_id, "spawn_enabled": True,
             "spawn_cooldown": SPAWN_SETTINGS["cooldown_seconds"],
             "message_count": 0, "last_spawn": None, "banned": False}
        await _col("group_settings").insert_one(g)
    return g

async def increment_group_msg(chat_id):
    res = await _col("group_settings").find_one_and_update(
        {"chat_id": chat_id}, {"$inc": {"message_count": 1}},
        upsert=True, return_document=True)
    return res["message_count"] if res else 1

async def reset_group_msg(chat_id):
    await _col("group_settings").update_one(
        {"chat_id": chat_id},
        {"$set": {"message_count": 0, "last_spawn": datetime.utcnow()}})

async def get_all_group_ids():
    docs = await _col("group_settings").find({}, {"chat_id": 1}).to_list(None)
    return [d["chat_id"] for d in docs]

async def track_group(group_id, title=""):
    await _col("top_groups").update_one(
        {"group_id": group_id}, {"$set": {"group_id": group_id, "title": title}}, upsert=True)

async def get_all_tracked_group_ids():
    docs = await _col("top_groups").find({}, {"group_id": 1}).to_list(None)
    return [d["group_id"] for d in docs]


# ── WISHLIST ──────────────────────────────────────────────────────────────────

async def add_wish(user_id, char_id, char_name, rarity):
    if await _col("wishlists").find_one({"user_id": user_id, "char_id": char_id}): return False
    if await _col("wishlists").count_documents({"user_id": user_id}) >= 25: return False
    await _col("wishlists").insert_one({"user_id": user_id, "char_id": char_id, "char_name": char_name, "rarity": rarity})
    return True

async def remove_wish(user_id, char_id):
    res = await _col("wishlists").delete_one({"user_id": user_id, "char_id": char_id})
    return res.deleted_count > 0

async def get_wishlist(user_id):
    return await _col("wishlists").find({"user_id": user_id}).to_list(25)

async def get_wishers(char_id, exclude_uid=0):
    docs = await _col("wishlists").find({"char_id": char_id, "user_id": {"$ne": exclude_uid}}).to_list(20)
    return [d["user_id"] for d in docs]


# ── TRADES ────────────────────────────────────────────────────────────────────

async def create_trade(doc):    await _col("trades").insert_one(doc)
async def get_trade(tid):       return await _col("trades").find_one({"trade_id": tid})
async def update_trade(tid, u): await _col("trades").update_one({"trade_id": tid}, u)


# ── MARKET ────────────────────────────────────────────────────────────────────

async def create_listing(doc):    await _col("market_listings").insert_one(doc)
async def get_listing(lid):       return await _col("market_listings").find_one({"listing_id": lid})
async def update_listing(lid, u): await _col("market_listings").update_one({"listing_id": lid}, u)

async def get_active_listings(rarity=None, limit=10):
    filt = {"status": "active"}
    if rarity: filt["rarity"] = rarity
    return await _col("market_listings").find(filt).sort("listed_at", -1).limit(limit).to_list(limit)

async def atomic_buy_listing(listing_id, buyer_id):
    return await _col("market_listings").find_one_and_update(
        {"listing_id": listing_id, "status": "active"},
        {"$set": {"status": "sold", "buyer_id": buyer_id, "sold_at": datetime.utcnow()}},
        return_document=True)


# ── MARRIAGES ─────────────────────────────────────────────────────────────────

async def get_marriage(uid):
    return await _col("marriages").find_one({"$or": [{"user1": uid}, {"user2": uid}]})

async def create_marriage(u1, u2):
    await _col("marriages").insert_one({"user1": u1, "user2": u2, "married_at": datetime.utcnow()})

async def divorce(uid):
    res = await _col("marriages").delete_one({"$or": [{"user1": uid}, {"user2": uid}]})
    return res.deleted_count > 0


# ── GLOBAL BAN / MUTE ─────────────────────────────────────────────────────────

async def add_to_global_ban(uid, reason, banned_by):
    await _col("global_bans").update_one({"user_id": uid},
        {"$set": {"user_id": uid, "reason": reason, "banned_by": banned_by, "banned_at": datetime.utcnow()}},
        upsert=True)

async def remove_from_global_ban(uid):   await _col("global_bans").delete_one({"user_id": uid})
async def is_user_globally_banned(uid):  return bool(await _col("global_bans").find_one({"user_id": uid}))
async def fetch_globally_banned_users(): return await _col("global_bans").find({}).to_list(None)

async def add_to_global_mute(uid, reason, muted_by):
    await _col("global_mutes").update_one({"user_id": uid},
        {"$set": {"user_id": uid, "reason": reason, "muted_by": muted_by, "muted_at": datetime.utcnow()}},
        upsert=True)

async def remove_from_global_mute(uid):  await _col("global_mutes").delete_one({"user_id": uid})
async def is_user_globally_muted(uid):   return bool(await _col("global_mutes").find_one({"user_id": uid}))
async def fetch_globally_muted_users():  return await _col("global_mutes").find({}).to_list(None)

async def get_all_chats():
    uids   = await get_all_user_ids()
    grpids = await get_all_tracked_group_ids()
    return list(set(uids + grpids))


# ── SUDO / DEV / UPLOADER ─────────────────────────────────────────────────────

async def add_sudo(uid):    await _col("sudo_users").update_one({"user_id": uid}, {"$set": {"user_id": uid}}, upsert=True)
async def remove_sudo(uid): await _col("sudo_users").delete_one({"user_id": uid})
async def get_sudo_ids():
    docs = await _col("sudo_users").find({}).to_list(None)
    return [d["user_id"] for d in docs]
async def is_sudo(uid): return bool(await _col("sudo_users").find_one({"user_id": uid}))

async def add_dev(uid):    await _col("dev_users").update_one({"user_id": uid}, {"$set": {"user_id": uid}}, upsert=True)
async def remove_dev(uid): await _col("dev_users").delete_one({"user_id": uid})
async def get_dev_ids():
    docs = await _col("dev_users").find({}).to_list(None)
    return [d["user_id"] for d in docs]
async def is_dev(uid): return bool(await _col("dev_users").find_one({"user_id": uid}))

async def add_uploader(uid):
    await _col("uploaders").update_one({"user_id": uid},
        {"$set": {"user_id": uid, "added_at": datetime.utcnow()}}, upsert=True)
async def remove_uploader(uid): await _col("uploaders").delete_one({"user_id": uid})
async def get_uploader_ids():
    docs = await _col("uploaders").find({}).to_list(None)
    return [d["user_id"] for d in docs]
async def is_uploader(uid): return bool(await _col("uploaders").find_one({"user_id": uid}))


# ── RANK ──────────────────────────────────────────────────────────────────────

async def count_user_rank(user_id) -> int:
    """
    Returns the collector rank of a user (1 = most characters).
    Uses $count stage — no documents loaded into memory.
    """
    cnt = await _col("user_characters").count_documents({"user_id": user_id})
    pipeline = [
        {"$group": {"_id": "$user_id", "count": {"$sum": 1}}},
        {"$match": {"count": {"$gt": cnt}}},
        {"$count": "ahead"},
    ]
    res = await _col("user_characters").aggregate(pipeline).to_list(1)
    return (res[0]["ahead"] if res else 0) + 1
