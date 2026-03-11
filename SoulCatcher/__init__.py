"""SoulCatcher/__init__.py — Pyrogram client + permission filters + auto module loader."""
from __future__ import annotations
import logging, importlib
from pathlib import Path
from pyrogram import Client, filters
from pyrogram.types import Message
from .config import API_ID, API_HASH, BOT_TOKEN, OWNER_IDS, SUDO_IDS

log = logging.getLogger("SoulCatcher")

app = Client(
    "SoulCatcher",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
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

# ── Auto-load all modules ─────────────────────────────────────────────────────
def load_modules():
    modules_dir = Path(__file__).parent / "modules"
    loaded, failed = [], []

    for f in sorted(modules_dir.glob("*.py")):
        if f.name.startswith("_"):
            continue
        mod_path = f"SoulCatcher.modules.{f.stem}"
        try:
            importlib.import_module(mod_path)
            loaded.append(f.stem)
            log.info(f"  ✅ {f.stem}")
        except Exception as e:
            failed.append(f.stem)
            log.error(f"  ❌ {f.stem}: {e}")

    log.info(f"Modules loaded: {len(loaded)} ✅  |  failed: {len(failed)} ❌")
    if failed:
        log.warning(f"Failed modules: {', '.join(failed)}")
