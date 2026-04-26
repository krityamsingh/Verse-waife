"""
fix_duplicate_instance_ids.py
──────────────────────────────
One-time migration: finds every duplicate instance_id in user_characters,
keeps the earliest document, and patches the others with a fresh full-UUID hex.

Run ONCE before (or instead of) restarting the bot:
    python fix_duplicate_instance_ids.py

Requires: pymongo, python-dotenv (or set MONGO_URI in your environment)
"""

import os
import uuid
import pprint
from pymongo import MongoClient

MONGO_URI = os.environ.get("MONGO_URI", "")
DB_NAME   = os.environ.get("DB_NAME", "soulcatcher")

if not MONGO_URI:
    raise SystemExit("❌  Set the MONGO_URI environment variable before running this script.")

client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=15_000)
db     = client[DB_NAME]
col    = db["user_characters"]

print(f"✅  Connected to: {DB_NAME}.user_characters")
print("🔍  Scanning for duplicate instance_id values...\n")

# ── Find all duplicated instance_id values ────────────────────────────────────
# Use $min (not $push) to avoid accumulating all _ids in memory.
# allowDiskUse lets the $group spill to disk on Atlas free-tier.
pipeline = [
    {"$group": {"_id": "$instance_id", "keep_id": {"$min": "$_id"}, "count": {"$sum": 1}}},
    {"$match": {"count": {"$gt": 1}}},
    {"$sort":  {"count": -1}},
]
duplicates = list(col.aggregate(pipeline, allowDiskUse=True))

if not duplicates:
    print("🎉  No duplicates found — collection is clean!")
    client.close()
    raise SystemExit(0)

total_dupes = sum(len(g["docs"]) - 1 for g in duplicates)
print(f"⚠️   Found {len(duplicates)} duplicated instance_id value(s) affecting {total_dupes} extra document(s):\n")
for g in duplicates:
    print(f"  instance_id={g['_id']!r}  →  {g['count']} copies, _ids: {g['docs']}")

print()

# ── Patch every duplicate (keep first, reassign the rest) ─────────────────────
patched = 0
for group in duplicates:
    # Sort by _id (ObjectId is roughly insertion-ordered) to keep the earliest
    sorted_ids = sorted(group["docs"])
    keep_id    = sorted_ids[0]
    patch_ids  = sorted_ids[1:]

    for dup_id in patch_ids:
        new_iid = uuid.uuid4().hex.upper()
        result  = col.update_one({"_id": dup_id}, {"$set": {"instance_id": new_iid}})
        if result.modified_count:
            print(f"  ✔  _id={dup_id}  instance_id  {group['_id']!r} → {new_iid!r}")
            patched += 1
        else:
            print(f"  ✘  _id={dup_id}  update failed (already gone?)")

print(f"\n✅  Done — patched {patched} document(s).")
print("You can now restart the bot; the unique index on instance_id will build cleanly.")
client.close()
