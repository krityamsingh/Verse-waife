"""SoulCatcher/config.py — all settings from environment variables."""
import os
from dotenv import load_dotenv

load_dotenv()

def _int_list(key, default=""):
    v = os.getenv(key, default)
    return [int(x.strip()) for x in v.split(",") if x.strip().lstrip("-").isdigit()]

def _str_list(key, default=""):
    v = os.getenv(key, default)
    return [x.strip() for x in v.split(",") if x.strip()]

# ── Core Telegram ──────────────────────────────────────────────────────────────
API_ID    = int(os.getenv("API_ID",    "26676741"))
API_HASH  =     os.getenv("API_HASH",  "6fbc29f23c15bdb0c7fbbefe65c9193a")
BOT_TOKEN =     os.getenv("BOT_TOKEN", "8607423040:AAEYVPF5LlR0AI22t3ygiBxi5WnIafJBBa4")

# ── MongoDB ────────────────────────────────────────────────────────────────────
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://erenxironman09:erenxironman09@catcherbot.koejwre.mongodb.net/?appName=catcherbot")
DB_NAME   = os.getenv("DB_NAME",   "soulcatcher")

# ── Access Control ─────────────────────────────────────────────────────────────
OWNER_IDS = _int_list("6118760915")
SUDO_IDS  = _int_list("6118760915")

# ── Channels ───────────────────────────────────────────────────────────────────
LOG_CHANNEL_ID    = int(os.getenv("LOG_CHANNEL_ID",    "0"))
UPLOAD_CHANNEL_ID = int(os.getenv("UPLOAD_CHANNEL_ID", "0"))
UPLOAD_GC_ID      = int(os.getenv("UPLOAD_GC_ID",      "0"))
SUPPORT_GROUP     =     os.getenv("SUPPORT_GROUP",  "soulcatcher_support")
UPDATE_CHANNEL    =     os.getenv("UPDATE_CHANNEL", "soulcatcher_updates")

# ── Identity ───────────────────────────────────────────────────────────────────
BOT_NAME     = os.getenv("BOT_NAME",     "SoulCatcher")
BOT_USERNAME = os.getenv("BOT_USERNAME", "soul_catcher_bot")
BOT_VERSION  = "1.0.0"

# ── Start Media ────────────────────────────────────────────────────────────────
START_IMAGE_URL  = os.getenv("START_IMAGE_URL", "https://files.catbox.moe/43vfsu.jpg")
START_VIDEO_URLS = _str_list("START_VIDEO_URLS",
    "https://files.catbox.moe/28291c.mp4,https://files.catbox.moe/pqkx90.mp4")
START_STICKER_ID = os.getenv("START_STICKER_ID",
    "CAACAgQAAxkBAAEkaVVoi2qUJ_xfrzADYu6zXbX4tUO4lwACDhUAAv1c6VOHhn_KhsuzHDYE")

# ── Git ────────────────────────────────────────────────────────────────────────
GIT_REPO_URL = os.getenv("GIT_REPO_URL", "")
GIT_BRANCH   = os.getenv("GIT_BRANCH",   "main")
