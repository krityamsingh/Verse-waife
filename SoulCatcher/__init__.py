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

# ── Client placeholder (lazy proxy) ──────────────────────────────────────────
# bot.py calls SoulCatcher.app._set(real_client) before importing modules.
# Modules do `from .. import app` which binds this proxy object — NOT None.
# All attribute access (@app.on_message etc.) is forwarded to the real client.

class _AppProxy:
    """Lazy proxy that forwards all attribute access to the real Pyrogram Client.

    This lets modules do `from .. import app` and use `@app.on_message(...)` as
    decorators at import time, while bot.py sets the actual client just before
    importing those modules via `SoulCatcher.app._set(client)`.
    """
    _client = None

    def _set(self, client) -> None:
        object.__setattr__(self, '_client', client)

    def __getattr__(self, name: str):
        client = object.__getattribute__(self, '_client')
        if client is None:
            raise RuntimeError(
                f"SoulCatcher.app.{name!r} was accessed before the Pyrogram Client "
                "was initialised. Make sure bot.py calls SoulCatcher.app._set(client) "
                "before load_modules()."
            )
        return getattr(client, name)

    def __setattr__(self, name: str, value) -> None:
        if name == '_client':
            object.__setattr__(self, name, value)
        else:
            client = object.__getattribute__(self, '_client')
            if client is None:
                raise RuntimeError("SoulCatcher.app not initialised.")
            setattr(client, name, value)

    def __repr__(self) -> str:
        client = object.__getattribute__(self, '_client')
        return f"<_AppProxy wrapping {client!r}>"


app: _AppProxy = _AppProxy()


def get_app() -> _AppProxy:
    """Return the live Pyrogram Client proxy. Raises if not yet initialised."""
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
