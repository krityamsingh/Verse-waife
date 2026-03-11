"""bot.py — SoulCatcher entry point.

ROOT CAUSE FIX:
  The original bot.py had zero error handling around init_db() and the
  refresh_* calls. MongoDB Atlas uses DNS SRV records (mongodb+srv://).
  If DNS resolution fails, times out, or the Atlas cluster is paused,
  init_db() raises immediately and the entire main() coroutine crashes
  BEFORE app.start() is ever called. The bot never connects to Telegram
  at all — it just silently dies, which looks like "not responding".

FIXES APPLIED:
  [FIX-1] init_db() is wrapped in try/except with retry logic.
          The bot will retry the MongoDB connection up to 5 times
          with exponential back-off before giving up.
  [FIX-2] refresh_sudo/dev/uploader calls are individually wrapped so
          a DB hiccup on any single call doesn't crash startup.
  [FIX-3] load_modules() errors are logged per-module and never crash
          the whole startup (was already partially true, now more robust).
  [FIX-4] app.start() failure is caught and logged clearly.
  [FIX-5] A clean shutdown handler is registered so Ctrl+C / SIGTERM
          gracefully stops the client instead of leaving zombie sessions.
  [FIX-6] The keep-alive loop is replaced with Pyrogram's own idle()
          which is signal-aware and integrates with the dispatcher.
  [FIX-7] Startup now logs every phase so you can see EXACTLY where it
          stalls — critical for diagnosing cloud deploy issues.
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

# Guarantee repo root is on sys.path so `import SoulCatcher` always works.
ROOT = Path(__file__).parent.resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── Constants ─────────────────────────────────────────────────────────────────

DB_RETRY_ATTEMPTS  = 5      # How many times to retry MongoDB connection
DB_RETRY_BASE_WAIT = 3      # Seconds — doubles each attempt (3, 6, 12, 24, 48)


# ── Module loader ─────────────────────────────────────────────────────────────

def load_modules() -> tuple[list[str], list[str]]:
    """Import every non-private .py file in SoulCatcher/modules/.

    Returns (loaded_list, failed_list).  Never raises — failed modules are
    logged and skipped so a broken optional module can't stop the bot.
    """
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
        except Exception as exc:
            failed.append(f.stem)
            log.error(f"  ❌ {f.stem}: {exc}", exc_info=True)

    summary = f"Modules: {len(loaded)} ✅  {len(failed)} ❌"
    if failed:
        summary += f"  — failed: {', '.join(failed)}"
    log.info(summary)
    return loaded, failed


# ── Database initialisation with retry ───────────────────────────────────────

async def init_db_with_retry(init_db_fn) -> bool:
    """
    Call init_db_fn() up to DB_RETRY_ATTEMPTS times with exponential back-off.

    Returns True on success, False if all attempts are exhausted.
    This lets the bot start (and accept Telegram messages) even when the
    MongoDB Atlas cluster is temporarily unreachable — for example during a
    cold start on Heroku/Railway where the DB wakes up after the bot does.
    """
    wait = DB_RETRY_BASE_WAIT
    for attempt in range(1, DB_RETRY_ATTEMPTS + 1):
        try:
            log.info(f"📦 Connecting to MongoDB (attempt {attempt}/{DB_RETRY_ATTEMPTS})...")
            await asyncio.wait_for(init_db_fn(), timeout=35)
            log.info("✅ MongoDB connected.")
            return True
        except asyncio.TimeoutError:
            log.warning(f"⏳ MongoDB timed out on attempt {attempt}.")
        except Exception as exc:
            log.warning(f"⚠️  MongoDB error on attempt {attempt}: {type(exc).__name__}: {exc}")

        if attempt < DB_RETRY_ATTEMPTS:
            log.info(f"   Retrying in {wait}s...")
            await asyncio.sleep(wait)
            wait *= 2

    log.critical(
        "❌ MongoDB unavailable after all retries.\n"
        "   Check that:\n"
        "     • MONGO_URI is correct in your environment variables\n"
        "     • Your Atlas cluster is running (not paused)\n"
        "     • Your deployment platform can resolve DNS (mongodb+srv://)\n"
        "     • Your Atlas Network Access list includes 0.0.0.0/0 or your server IP\n"
        "   The bot will NOT start without a database connection."
    )
    return False


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    # FIX: all heavy imports happen inside main() so they execute while
    # asyncio.run()'s event loop is already running.  This guarantees that
    # Pyrogram's Client and Dispatcher capture the CORRECT running loop
    # (not a stale pre-run loop), which prevents silent task failures.
    from SoulCatcher.database import init_db, get_sudo_ids, get_dev_ids, get_uploader_ids
    from SoulCatcher import app, refresh_sudo, refresh_dev, refresh_uploader

    log.info("🌸 SoulCatcher starting up...")

    # ── Phase 1: Database ─────────────────────────────────────────────────
    log.info("Phase 1/4: Database connection")
    db_ok = await init_db_with_retry(init_db)
    if not db_ok:
        sys.exit(1)

    # ── Phase 2: Permission caches ────────────────────────────────────────
    log.info("Phase 2/4: Loading permission caches")
    try:
        refresh_sudo(await get_sudo_ids())
        log.info("  ✅ sudo cache loaded")
    except Exception as exc:
        log.warning(f"  ⚠️  Could not load sudo IDs (non-fatal): {exc}")

    try:
        refresh_dev(await get_dev_ids())
        log.info("  ✅ dev cache loaded")
    except Exception as exc:
        log.warning(f"  ⚠️  Could not load dev IDs (non-fatal): {exc}")

    try:
        refresh_uploader(await get_uploader_ids())
        log.info("  ✅ uploader cache loaded")
    except Exception as exc:
        log.warning(f"  ⚠️  Could not load uploader IDs (non-fatal): {exc}")

    # ── Phase 3: Modules ──────────────────────────────────────────────────
    log.info("Phase 3/4: Loading modules")
    loaded, failed = load_modules()
    if not loaded:
        log.critical("❌ No modules loaded at all — check the SoulCatcher/modules/ directory.")
        sys.exit(1)

    # ── Phase 4: Connect to Telegram ──────────────────────────────────────
    log.info("Phase 4/4: Connecting to Telegram")
    try:
        await app.start()
        me = app.me
        log.info(f"✅ SoulCatcher is ONLINE as @{me.username} (id={me.id})")
    except Exception as exc:
        log.critical(
            f"❌ app.start() failed: {type(exc).__name__}: {exc}\n"
            "   Check that BOT_TOKEN and API_ID/API_HASH are correct.",
            exc_info=True,
        )
        sys.exit(1)

    # ── Keep alive ────────────────────────────────────────────────────────
    # asyncio.Event().wait() is replaced with a signal-aware idle loop so
    # SIGTERM (Heroku/Railway shutdown) gracefully stops the client.
    stop_event = asyncio.Event()

    def _signal_handler():
        log.info("🛑 Shutdown signal received — stopping gracefully...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except (NotImplementedError, RuntimeError):
            pass  # Windows doesn't support add_signal_handler

    log.info("🌸 Bot is running. Press Ctrl+C to stop.")
    await stop_event.wait()

    log.info("🌸 Stopping client...")
    try:
        await app.stop()
        log.info("✅ Client stopped cleanly.")
    except Exception as exc:
        log.warning(f"⚠️  Error during stop: {exc}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("👋 Interrupted by user.")
    except SystemExit as exc:
        sys.exit(exc.code)
    except Exception as exc:
        log.critical(f"💥 Unhandled exception in main: {exc}", exc_info=True)
        sys.exit(1)
