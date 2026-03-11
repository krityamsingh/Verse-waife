"""SoulCatcher/config.py — all settings from environment variables.

🔐 SECURITY FIX: No hardcoded credentials allowed!
   All required variables MUST be set in environment.
   This file will raise RuntimeError if critical vars are missing.
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()

def _int_list(key, default=""):
    v = os.getenv(key, default)
    return [int(x.strip()) for x in v.split(",") if x.strip().lstrip("-").isdigit()]

def _str_list(key, default=""):
    v = os.getenv(key, default)
    return [x.strip() for x in v.split(",") if x.strip()]

def _validate_url(url: str, allow_empty: bool = False) -> bool:
    """Validate HTTPS URLs"""
    if not url:
        return allow_empty
    return url.startswith("https://") and len(url) < 2048

# ── Core Telegram (REQUIRED) ───────────────────────────────────────────────────
# All three are MANDATORY for bot operation
# Set via environment variables ONLY
API_ID = os.getenv("API_ID")
if not API_ID:
    raise RuntimeError(
        "❌ API_ID not set in environment variables!\n"
        "Get it from: https://my.telegram.org/apps\n"
        "Set with: export API_ID=your_api_id"
    )
try:
    API_ID = int(API_ID)
except ValueError:
    raise RuntimeError("❌ API_ID must be an integer!")

API_HASH = os.getenv("API_HASH")
if not API_HASH:
    raise RuntimeError(
        "❌ API_HASH not set in environment variables!\n"
        "Get it from: https://my.telegram.org/apps\n"
        "Set with: export API_HASH=your_api_hash"
    )

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError(
        "❌ BOT_TOKEN not set in environment variables!\n"
        "Get it from: @BotFather on Telegram\n"
        "Set with: export BOT_TOKEN=your_bot_token"
    )

# ── MongoDB (REQUIRED) ────────────────────────────────────────────────────────
MONGO_URI = os.getenv("MONGO_URI")
if not MONGO_URI:
    raise RuntimeError(
        "❌ MONGO_URI not set in environment variables!\n"
        "Create MongoDB Atlas cluster at: https://www.mongodb.com/cloud/atlas\n"
        "Set with: export MONGO_URI='mongodb+srv://user:pass@cluster.mongodb.net/?appName=app'"
    )

DB_NAME = os.getenv("DB_NAME", "soulcatcher")

# ── Access Control (RECOMMENDED) ──────────────────────────────────────────────
# Owner IDs are required for bot administration
OWNER_IDS = _int_list("OWNER_IDS")
if not OWNER_IDS:
    # Warn but don't fail - allows dev/test setup without owner
    print("⚠️  WARNING: OWNER_IDS not set. Bot will have no owner admin access.")
    OWNER_IDS = []

SUDO_IDS = _int_list("SUDO_IDS", "")  # Empty by default, added via /addsudo

# ── Channels (OPTIONAL) ────────────────────────────────────────────────────────
LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID", "0")
try:
    LOG_CHANNEL_ID = int(LOG_CHANNEL_ID)
except ValueError:
    LOG_CHANNEL_ID = 0

UPLOAD_CHANNEL_ID = os.getenv("UPLOAD_CHANNEL_ID", "0")
try:
    UPLOAD_CHANNEL_ID = int(UPLOAD_CHANNEL_ID)
except ValueError:
    UPLOAD_CHANNEL_ID = 0

UPLOAD_GC_ID = os.getenv("UPLOAD_GC_ID", "0")
try:
    UPLOAD_GC_ID = int(UPLOAD_GC_ID)
except ValueError:
    UPLOAD_GC_ID = 0

SUPPORT_GROUP = os.getenv("SUPPORT_GROUP", "soulcatcher_support")
UPDATE_CHANNEL = os.getenv("UPDATE_CHANNEL", "soulcatcher_updates")

# ── Identity (OPTIONAL) ────────────────────────────────────────────────────────
BOT_NAME = os.getenv("BOT_NAME", "SoulCatcher")
BOT_USERNAME = os.getenv("BOT_USERNAME", "soul_catcher_bot")
BOT_VERSION = "1.0.1"  # Bumped for security fixes

# ── Start Media (OPTIONAL with validation) ────────────────────────────────────
START_IMAGE_URL = os.getenv("START_IMAGE_URL", "")
if START_IMAGE_URL and not _validate_url(START_IMAGE_URL):
    raise RuntimeError(f"❌ START_IMAGE_URL must be HTTPS: {START_IMAGE_URL}")

START_VIDEO_URLS = _str_list("START_VIDEO_URLS", "")
for url in START_VIDEO_URLS:
    if not _validate_url(url):
        raise RuntimeError(f"❌ All START_VIDEO_URLS must be HTTPS: {url}")

START_STICKER_ID = os.getenv("START_STICKER_ID", "")

# ── Git (OPTIONAL) ────────────────────────────────────────────────────────────
GIT_REPO_URL = os.getenv("GIT_REPO_URL", "")
GIT_BRANCH = os.getenv("GIT_BRANCH", "main")

# ── Log configuration setup ───────────────────────────────────────────────────
if __name__ == "__main__":
    print("✅ Config validation passed!")
    print(f"  API_ID: {API_ID}")
    print(f"  API_HASH: {API_HASH[:10]}...")
    print(f"  BOT_TOKEN: {BOT_TOKEN[:20]}...")
    print(f"  MONGO_URI: {'SET' if MONGO_URI else 'NOT SET'}")
    print(f"  Owner IDs: {OWNER_IDS or 'None (set later with /addsudo)'}")
