"""SoulCatcher/__init__.py — Permission filters + runtime caches.

FIXED: This file no longer creates a Pyrogram Client.
       bot.py owns the full Client lifecycle.
       All modules must reference `SoulCatcher.app` (module attribute lookup)
       NOT `from .. import app` (which binds to the object at import time
       and becomes stale when bot.py rebuilds the client on a retry).
"""
from __future__ import annotations
import logging
from pyrogram import filters
from pyrogram.types import Message
from .config import OWNER_IDS, SUDO_IDS

log = logging.getLogger("SoulCatcher")

# ── Client placeholder ────────────────────────────────────────────────────────
# bot.py assigns this before loading any module.
# Never create a Client here — doing so causes a second Client to connect
# to Telegram during import, and all @app.on_message handlers in modules
# get permanently bound to that dead instance instead of the live one.
app = None  # type: ignore[assignment]  # replaced by bot.py before module load


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
