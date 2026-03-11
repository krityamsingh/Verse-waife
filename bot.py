"""SoulCatcher/bot.py — Entry point"""
import asyncio, logging, sys, importlib
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("SoulCatcher")

# Guarantee repo root is on sys.path so `import SoulCatcher` always works
ROOT = Path(__file__).parent.resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_modules():
    modules_dir = ROOT / "SoulCatcher" / "modules"
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
    log.info(f"Modules: {len(loaded)} ✅  {len(failed)} ❌" + (f"  — failed: {', '.join(failed)}" if failed else ""))


async def main():
    # sys.path is set above — safe to import now
    from SoulCatcher.database import init_db, get_sudo_ids, get_dev_ids, get_uploader_ids
    from SoulCatcher import app, refresh_sudo, refresh_dev, refresh_uploader

    log.info("🌸 SoulCatcher starting up...")

    await init_db()

    refresh_sudo(await get_sudo_ids())
    refresh_dev(await get_dev_ids())
    refresh_uploader(await get_uploader_ids())

    load_modules()

    log.info("🌸 Starting bot...")
    await app.start()
    log.info("✅ SoulCatcher is online!")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
