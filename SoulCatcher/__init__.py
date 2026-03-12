"""SoulCatcher/__init__.py — Pyrogram client + permission filters."""
from __future__ import annotations
import logging
from pyrogram import Client, filters
from pyrogram.types import Message
from .config import API_ID, API_HASH, BOT_TOKEN, OWNER_IDS, SUDO_IDS

log = logging.getLogger("SoulCatcher")

app = Client(
    "SoulCatcher",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    sleep_threshold=60,
)

# ── Runtime permission caches ─────────────────────────────────────────────────
_sudo_cache:     set[int] = set(SUDO_IDS)
_dev_cache:      set[int] = set()
_uploader_cache: set[int] = set()

def refresh_sudo(ids):     _sudo_cache.update(ids)
def refresh_dev(ids):      _dev_cache.update(ids)
def refresh_uploader(ids): _uploader_cache.update(ids)

# ── Permission filters ────────────────────────────────────────────────────────
def _owner(_, __, m: Message):    return bool(m.from_user and m.from_user.id in OWNER_IDS)
def _sudo(_, __, m: Message):     return bool(m.from_user and (m.from_user.id in OWNER_IDS or m.from_user.id in _sudo_cache))
def _dev(_, __, m: Message):      return bool(m.from_user and (m.from_user.id in OWNER_IDS or m.from_user.id in _dev_cache))
def _uploader(_, __, m: Message): return bool(m.from_user and (m.from_user.id in OWNER_IDS or m.from_user.id in _uploader_cache))

owner_filter    = filters.create(_owner)
sudo_filter     = filters.create(_sudo)
dev_filter      = filters.create(_dev)
uploader_filter = filters.create(_uploader)

def capsify(text: str) -> str:
    return text

gban_watcher  = 1
gmute_watcher = 2
