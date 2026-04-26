"""
bot.py — SoulCatcher entry point.

Boot sequence:
  1. Create ONE Pyrogram Client
  2. Assign SoulCatcher.app = that client
  3. Import all modules (handlers register on that client)
  4. Call client.start() — the SAME object
  5. Wait for stop signal
"""

import asyncio
import importlib
import logging
import signal
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("SoulCatcher")

ROOT = Path(__file__).parent.resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── Retry config ──────────────────────────────────────────────────────────────

DB_RETRY_ATTEMPTS  = 5
DB_RETRY_BASE_WAIT = 3
TG_RETRY_ATTEMPTS  = 10
TG_RETRY_BASE_WAIT = 5


# ── Client factory ────────────────────────────────────────────────────────────

def _make_client(API_ID, API_HASH, BOT_TOKEN):
    from pyrogram import Client
    return Client(
        "SoulCatcher",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        sleep_threshold=60,
        in_memory=True,
    )


# ── Module loader ─────────────────────────────────────────────────────────────

def load_modules(reload: bool = False) -> tuple[list[str], list[str]]:
    modules_dir = ROOT / "SoulCatcher" / "modules"
    loaded, failed = [], []

    for f in sorted(modules_dir.glob("*.py")):
        if f.name.startswith("_"):
            continue
        mod_path = f"SoulCatcher.modules.{f.stem}"
        try:
            if reload and mod_path in sys.modules:
                importlib.reload(sys.modules[mod_path])
            else:
                importlib.import_module(mod_path)
            loaded.append(f.stem)
            log.info("  ✅ %s", f.stem)
        except Exception as exc:
            failed.append(f.stem)
            log.error("  ❌ %s: %s", f.stem, exc, exc_info=True)

    summary = f"Modules: {len(loaded)} ✅  {len(failed)} ❌"
    if failed:
        summary += f"  — failed: {', '.join(failed)}"
    log.info(summary)
    return loaded, failed


# ── MongoDB with retry ────────────────────────────────────────────────────────

async def init_db_with_retry(init_db_fn) -> bool:
    wait = DB_RETRY_BASE_WAIT
    for attempt in range(1, DB_RETRY_ATTEMPTS + 1):
        try:
            log.info("📦 MongoDB attempt %d/%d...", attempt, DB_RETRY_ATTEMPTS)
            await asyncio.wait_for(init_db_fn(), timeout=35)
            log.info("✅ MongoDB connected.")
            return True
        except asyncio.TimeoutError:
            log.warning("⏳ MongoDB timed out (attempt %d).", attempt)
        except Exception as exc:
            log.warning("⚠️  MongoDB error (attempt %d): %s: %s", attempt, type(exc).__name__, exc)

        if attempt < DB_RETRY_ATTEMPTS:
            log.info("   Retrying in %ds...", wait)
            await asyncio.sleep(wait)
            wait = min(wait * 2, 60)

    log.critical(
        "❌ MongoDB unavailable after all retries.\n"
        "   • Check MONGO_URI in config vars\n"
        "   • Ensure Atlas cluster is not paused\n"
        "   • Atlas Network Access must allow 0.0.0.0/0"
    )
    return False


# ── Telegram connection with retry ────────────────────────────────────────────

async def start_telegram_with_retry(API_ID, API_HASH, BOT_TOKEN):
    import SoulCatcher
    wait = TG_RETRY_BASE_WAIT

    for attempt in range(1, TG_RETRY_ATTEMPTS + 1):
        # Get the real underlying client from the proxy
        client = object.__getattribute__(SoulCatcher.app, '_client')
        try:
            log.info("📡 Telegram attempt %d/%d...", attempt, TG_RETRY_ATTEMPTS)
            await asyncio.wait_for(client.start(), timeout=40)
            me = await client.get_me()
            log.info("✅ Connected as @%s (id=%d)", me.username, me.id)
            return client

        except asyncio.TimeoutError:
            log.warning("⚠️  Telegram timed out (attempt %d) — rebuilding client.", attempt)

        except (KeyError, ConnectionError, OSError, Exception) as exc:
            log.warning("⚠️  Connection error (attempt %d): %s: %s", attempt, type(exc).__name__, exc)
            try:
                await client.stop()
            except Exception:
                pass

        # Build fresh client and reload modules on retry
        SoulCatcher.app._set(_make_client(API_ID, API_HASH, BOT_TOKEN))
        load_modules(reload=True)

        if attempt < TG_RETRY_ATTEMPTS:
            capped = min(wait, 60)
            log.info("   Retrying in %ds...", capped)
            await asyncio.sleep(capped)
            wait *= 2
        else:
            log.critical(
                "❌ Could not connect to Telegram after all retries.\n"
                "   • Verify BOT_TOKEN, API_ID, API_HASH\n"
                "   • MTProto port issues? Try Railway or a VPS"
            )

    return None


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    from SoulCatcher.config import API_ID, API_HASH, BOT_TOKEN
    from SoulCatcher.database import init_db, get_sudo_ids, get_dev_ids, get_uploader_ids
    from SoulCatcher import refresh_sudo, refresh_dev, refresh_uploader
    import SoulCatcher

    log.info("🌸 SoulCatcher v2.0 starting up...")

    # Phase 1: DB
    log.info("Phase 1/4: Database connection")
    if not await init_db_with_retry(init_db):
        sys.exit(1)

    # Phase 2: Permission caches
    log.info("Phase 2/4: Loading permission caches")
    for label, getter, refresher in [
        ("sudo",     get_sudo_ids,     refresh_sudo),
        ("dev",      get_dev_ids,      refresh_dev),
        ("uploader", get_uploader_ids, refresh_uploader),
    ]:
        try:
            refresher(await getter())
            log.info("  ✅ %s cache loaded", label)
        except Exception as exc:
            log.warning("  ⚠️  %s cache failed (non-fatal): %s", label, exc)

    # Phase 3: Modules — create client FIRST via proxy, then import modules
    log.info("Phase 3/4: Loading modules")
    real_client = _make_client(API_ID, API_HASH, BOT_TOKEN)
    SoulCatcher.app._set(real_client)

    # DragonBall DB init — must run AFTER _set() so module decorators can register
    try:
        from SoulCatcher.modules.dragonball import init_db as db_init
        await asyncio.wait_for(db_init(), timeout=15)
        log.info("✅ DragonBall DB ready")
    except Exception as exc:
        log.warning("⚠️  DragonBall DB skipped (non-fatal): %s", exc)

    loaded, failed = load_modules()
    if not loaded:
        log.critical("❌ No modules loaded — check SoulCatcher/modules/")
        sys.exit(1)

    # Phase 4: Connect
    log.info("Phase 4/4: Connecting to Telegram")
    client = await start_telegram_with_retry(API_ID, API_HASH, BOT_TOKEN)
    if client is None:
        sys.exit(1)

    # Shutdown handler
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _stop():
        log.info("🛑 Shutdown signal received.")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except (NotImplementedError, RuntimeError):
            pass

    log.info("🌸 SoulCatcher is running! Press Ctrl+C to stop.")
    await stop_event.wait()

    log.info("🌸 Stopping client...")
    try:
        await client.stop()
        log.info("✅ Stopped cleanly.")
    except Exception as exc:
        log.warning("⚠️  Error during stop: %s", exc)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("👋 Interrupted.")
    except SystemExit as exc:
        sys.exit(exc.code)
    except Exception as exc:
        log.critical("💥 Unhandled exception: %s", exc, exc_info=True)
        sys.exit(1)
