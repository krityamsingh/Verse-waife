"""
SoulCatcher/__init__.py — Permission filters, runtime caches, utilities.

The Pyrogram Client is created and owned by bot.py.
All modules must look up `SoulCatcher.app` at handler call time, not at import.
"""
from __future__ import annotations

import logging
from pyrogram import filters
from pyrogram.types import Message

from .config import OWNER_IDS, SUDO_IDS

log = logging.getLogger("SoulCatcher")

# ── Client placeholder ────────────────────────────────────────────────────────
# bot.py assigns this before importing any module.
app = None  # type: ignore[assignment]


def get_app():
    """Return the live Pyrogram Client. Raises if bot.py hasn't set it yet."""
    if app is None:
        raise RuntimeError("SoulCatcher.app not initialised — check bot.py startup order.")
    return app


# ── Runtime permission caches ─────────────────────────────────────────────────
_sudo_cache:     set[int] = set(SUDO_IDS)
_dev_cache:      set[int] = set()
_uploader_cache: set[int] = set()


def refresh_sudo(ids):
    _sudo_cache.update(ids)


def refresh_dev(ids):
    _dev_cache.update(ids)


def refresh_uploader(ids):
    _uploader_cache.update(ids)


def is_sudo(uid: int) -> bool:
    return uid in OWNER_IDS or uid in _sudo_cache


def is_dev(uid: int) -> bool:
    return uid in OWNER_IDS or uid in _dev_cache


def is_uploader(uid: int) -> bool:
    return uid in OWNER_IDS or uid in _uploader_cache


# ── Permission filters ────────────────────────────────────────────────────────

def _owner(_, __, m: Message):
    return bool(m.from_user and m.from_user.id in OWNER_IDS)


def _sudo(_, __, m: Message):
    return bool(m.from_user and (
        m.from_user.id in OWNER_IDS or m.from_user.id in _sudo_cache
    ))


def _dev(_, __, m: Message):
    return bool(m.from_user and (
        m.from_user.id in OWNER_IDS or m.from_user.id in _dev_cache
    ))


def _uploader(_, __, m: Message):
    return bool(m.from_user and (
        m.from_user.id in OWNER_IDS or m.from_user.id in _uploader_cache
    ))


owner_filter    = filters.create(_owner)
sudo_filter     = filters.create(_sudo)
dev_filter      = filters.create(_dev)
uploader_filter = filters.create(_uploader)


# ── Text utilities ────────────────────────────────────────────────────────────

def capsify(text: str) -> str:
    """Alternate upper/lower case on alphabetic characters."""
    result, upper = [], True
    for ch in text:
        if ch.isalpha():
            result.append(ch.upper() if upper else ch.lower())
            upper = not upper
        else:
            result.append(ch)
    return "".join(result)


def mention(user_id: int, name: str) -> str:
    """Return a Telegram mention link."""
    return f"[{name}](tg://user?id={user_id})"


def fmt_number(n: int) -> str:
    """1234567 → '1,234,567'"""
    return f"{n:,}"


# ── Handler group constants ───────────────────────────────────────────────────
GBAN_HANDLER_GROUP  = 1
GMUTE_HANDLER_GROUP = 2
gban_watcher        = GBAN_HANDLER_GROUP   # legacy alias
gmute_watcher       = GMUTE_HANDLER_GROUP  # legacy alias
