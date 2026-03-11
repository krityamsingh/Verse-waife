"""
SoulCatcher/bot.py — Entry point
"""
import asyncio, logging, importlib, sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("SoulCatcher")

MODULES = [
    "SoulCatcher.modules.start",
    "SoulCatcher.modules.spawn",
    "SoulCatcher.modules.profile",
    "SoulCatcher.modules.economy",
    "SoulCatcher.modules.collection",
    "SoulCatcher.modules.social",
    "SoulCatcher.modules.sudo",
    "SoulCatcher.modules.admin",
    "SoulCatcher.modules.autouploader",
]


async def main():
    from SoulCatcher.database import init_db, get_sudo_ids, get_dev_ids, get_uploader_ids
    from SoulCatcher import app, refresh_sudo, refresh_dev, refresh_uploader

    log.info("🌸 SoulCatcher starting up...")

    # Connect DB
    await init_db()

    # Load permission caches
    refresh_sudo(await get_sudo_ids())
    refresh_dev(await get_dev_ids())
    refresh_uploader(await get_uploader_ids())

    # Load all modules
    for mod in MODULES:
        try:
            importlib.import_module(mod)
            log.info(f"  ✅ {mod}")
        except Exception as e:
            log.error(f"  ❌ {mod}: {e}")

    log.info("🌸 All modules loaded. Starting bot...")
    await app.start()
    log.info("✅ SoulCatcher is online!")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
