"""SoulCatcher/bot.py — Entry point"""
import asyncio, logging, sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("SoulCatcher")


async def main():
    from SoulCatcher.database import init_db, get_sudo_ids, get_dev_ids, get_uploader_ids
    from SoulCatcher import app, refresh_sudo, refresh_dev, refresh_uploader, load_modules

    log.info("🌸 SoulCatcher starting up...")

    # Connect DB
    await init_db()

    # Load permission caches from DB
    refresh_sudo(await get_sudo_ids())
    refresh_dev(await get_dev_ids())
    refresh_uploader(await get_uploader_ids())

    # Auto-load every .py in SoulCatcher/modules/
    load_modules()

    log.info("🌸 Starting bot...")
    await app.start()
    log.info("✅ SoulCatcher is online!")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
