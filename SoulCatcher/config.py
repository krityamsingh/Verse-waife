"""
SoulCatcher/config.py — All settings loaded from environment variables.

Security: No hardcoded credentials. All required vars must be in environment.
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()


def _int_list(key: str, default: str = "") -> list[int]:
    v = os.getenv(key, default)
    return [int(x.strip()) for x in v.split(",") if x.strip().lstrip("-").isdigit()]


def _str_list(key: str, default: str = "") -> list[str]:
    v = os.getenv(key, default)
    return [x.strip() for x in v.split(",") if x.strip()]


def _validate_url(url: str) -> bool:
    if not url:
        return True  # empty is fine (optional)
    return url.startswith("https://") and len(url) < 2048


# ── Core Telegram (REQUIRED) ──────────────────────────────────────────────────

_raw_api_id = os.getenv("API_ID", "")
if not _raw_api_id:
    sys.exit("❌ API_ID not set. Get it from https://my.telegram.org/apps")
try:
    API_ID: int = int(_raw_api_id)
except ValueError:
    sys.exit("❌ API_ID must be an integer.")

API_HASH: str = os.getenv("API_HASH", "")
if not API_HASH:
    sys.exit("❌ API_HASH not set. Get it from https://my.telegram.org/apps")

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN or ":" not in BOT_TOKEN:
    sys.exit("❌ BOT_TOKEN not set or invalid. Get it from @BotFather")

# ── MongoDB (REQUIRED) ────────────────────────────────────────────────────────

MONGO_URI: str = os.getenv("MONGO_URI", "")
if not MONGO_URI:
    sys.exit("❌ MONGO_URI not set. Create a cluster at https://cloud.mongodb.com")

DB_NAME: str = os.getenv("DB_NAME", "soulcatcher")

# ── Access Control ────────────────────────────────────────────────────────────

OWNER_IDS: list[int] = _int_list("OWNER_IDS")
if not OWNER_IDS:
    print("⚠️  WARNING: OWNER_IDS not set — bot will have no admin.")

SUDO_IDS: list[int] = _int_list("SUDO_IDS")

# ── Channels ──────────────────────────────────────────────────────────────────

def _safe_channel_id(key: str) -> int:
    raw = os.getenv(key, "0")
    try:
        val = int(raw)
        if val != 0 and len(str(abs(val))) > 14:
            print(f"⚠️  {key} '{val}' looks invalid — resetting to 0.")
            return 0
        return val
    except ValueError:
        return 0


LOG_CHANNEL_ID:    int = _safe_channel_id("LOG_CHANNEL_ID")
UPLOAD_CHANNEL_ID: int = _safe_channel_id("UPLOAD_CHANNEL_ID")
UPLOAD_GC_ID:      int = _safe_channel_id("UPLOAD_GC_ID")

SUPPORT_GROUP:  str = os.getenv("SUPPORT_GROUP", "")
UPDATE_CHANNEL: str = os.getenv("UPDATE_CHANNEL", "")

# ── Identity ──────────────────────────────────────────────────────────────────

BOT_NAME:     str = os.getenv("BOT_NAME",     "SoulCatcher")
BOT_USERNAME: str = os.getenv("BOT_USERNAME", "soul_catcher_bot")
BOT_VERSION:  str = "2.0.0"

# ── Start Media ───────────────────────────────────────────────────────────────

START_IMAGE_URL: str = os.getenv("START_IMAGE_URL", "")
if not _validate_url(START_IMAGE_URL):
    sys.exit(f"❌ START_IMAGE_URL must be HTTPS: {START_IMAGE_URL}")

START_VIDEO_URLS: list[str] = _str_list("START_VIDEO_URLS")
for _url in START_VIDEO_URLS:
    if not _validate_url(_url):
        sys.exit(f"❌ START_VIDEO_URLS contains invalid URL: {_url}")

START_STICKER_ID: str = os.getenv("START_STICKER_ID", "")

# ── Git Integration ───────────────────────────────────────────────────────────

GIT_REPO_URL: str = os.getenv("GIT_REPO_URL", "")
GIT_BRANCH:   str = os.getenv("GIT_BRANCH",   "main")
