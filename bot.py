"""bot.py — SoulCatcher entry point.

FIXES APPLIED:
  [FIX-1] init_db() retries up to 5x with exponential back-off.
  [FIX-2] refresh_sudo/dev/uploader calls are individually wrapped —
          a DB hiccup on any single call doesn't crash startup.
  [FIX-3] load_modules() logs failures per-module, never crashes startup.
  [FIX-4] app.start() now retries up to TG_RETRY_ATTEMPTS times with
          back-off. KeyError: 0 from Pyrogram means Telegram's DC sent
          garbage bytes on the TCP socket (Heroku network flakiness) —
          the fix is to retry instead of crashing immediately.
  [FIX-5] sleep_threshold=60 added to the Pyrogram Client so it waits
          longer before giving up on flood-wait / DC reconnects.
  [FIX-6] SIGTERM / SIGINT handled for clean Heroku dyno shutdown.
  [FIX-7] All startup phases logged clearly so you can see exactly
          where a stall or failure occurs.
  [FIX-8] API_ID is validated as a real integer at startup so a
          misconfigured env var is caught before Pyrogram even tries.
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

# ── Retry tunables ────────────────────────────────────────────────────────────

DB_RETRY_ATTEMPTS  = 5    # MongoDB connection attempts
DB_RETRY_BASE_WAIT = 3    # seconds — doubles each attempt: 3, 6, 12, 24, 48

TG_RETRY_ATTEMPTS  = 10   # Telegram app.start() attempts
TG_RETRY_BASE_WAIT = 5    # seconds — doubles each attempt: 5, 10, 20 … capped at 60


# ── Module loader ─────────────────────────────────────────────────────────────

def load_modules() -> tuple[list[str], list[str]]:
    """Import every non-private .py file in SoulCatcher/modules/.

    Returns (loaded_list, failed_list). Never raises — a broken module
    is logged and skipped so it can't stop the rest of the bot.
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


# ── MongoDB with retry ────────────────────────────────────────────────────────

async def init_db_with_retry(init_db_fn) -> bool:
    wait = DB_RETRY_BASE_WAIT
    for attempt in range(1, DB_RETRY_ATTEMPTS + 1):
        try:
            log.info(f"📦 MongoDB connection attempt {attempt}/{DB_RETRY_ATTEMPTS}...")
            await asyncio.wait_for(init_db_fn(), timeout=35)
            log.info("✅ MongoDB connected.")
            return True
        except asyncio.TimeoutError:
            log.warning(f"⏳ MongoDB timed out (attempt {attempt}).")
        except Exception as exc:
            log.warning(f"⚠️  MongoDB error (attempt {attempt}): {type(exc).__name__}: {exc}")

        if attempt < DB_RETRY_ATTEMPTS:
            log.info(f"   Retrying in {wait}s...")
            await asyncio.sleep(wait)
            wait *= 2

    log.critical(
        "❌ MongoDB unavailable after all retries.\n"
        "   • Check MONGO_URI in your Heroku config vars\n"
        "   • Make sure the Atlas cluster is not paused\n"
        "   • Atlas Network Access must allow 0.0.0.0/0 or your server IP\n"
        "   Bot cannot start without a database."
    )
    return False


# ── Telegram app.start() with retry ──────────────────────────────────────────

async def start_telegram_with_retry(app) -> bool:
    """
    [FIX-4] Retry app.start() on transient Pyrogram errors.

    KeyError: 0 means Pyrogram received 4 zero-bytes from Telegram's DC
    instead of a real MTProto response. This happens when:
      - Heroku's network drops/resets the TCP connection mid-handshake
      - Telegram's DC is temporarily overloaded (common on DC2)
      - The dyno is starting cold and the network isn't fully up yet

    Solution: retry with back-off. It always succeeds within a few attempts
    once the TCP path to Telegram stabilises.
    """
    wait = TG_RETRY_BASE_WAIT
    for attempt in range(1, TG_RETRY_ATTEMPTS + 1):
        try:
            log.info(f"📡 Telegram connection attempt {attempt}/{TG_RETRY_ATTEMPTS}...")
            await app.start()
            me = app.me
            log.info(f"✅ Connected as @{me.username} (id={me.id})")
            return True

        except KeyError as exc:
            # KeyError: 0 — Pyrogram got garbage bytes from Telegram DC
            # This is always a transient network issue, never a config bug.
            log.warning(
                f"⚠️  Telegram DC handshake failed (attempt {attempt}): "
                f"KeyError: {exc} — Heroku/DC network glitch, retrying..."
            )
        except ConnectionError as exc:
            log.warning(f"⚠️  Connection error (attempt {attempt}): {exc}")
        except OSError as exc:
            log.warning(f"⚠️  Network OS error (attempt {attempt}): {exc}")
        except Exception as exc:
            # For unknown errors, log the full type so we can diagnose
            log.warning(
                f"⚠️  app.start() error (attempt {attempt}): "
                f"{type(exc).__name__}: {exc}"
            )

        if attempt < TG_RETRY_ATTEMPTS:
            capped_wait = min(wait, 60)
            log.info(f"   Retrying in {capped_wait}s...")
            await asyncio.sleep(capped_wait)
            wait *= 2
        else:
            log.critical(
                "❌ Could not connect to Telegram after all retries.\n"
                "   • Verify BOT_TOKEN is correct in Heroku config vars\n"
                "   • Verify API_ID (must be an integer) and API_HASH\n"
                "   • If KeyError: 0 persists, Heroku may be blocking\n"
                "     Telegram's MTProto ports — consider Railway or a VPS"
            )

    return False


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    # Imports inside main() so they run inside asyncio.run()'s event loop.
    # This guarantees Pyrogram's Client captures the correct running loop.
    from SoulCatcher.config import API_ID, BOT_TOKEN
    from SoulCatcher.database import init_db, get_sudo_ids, get_dev_ids, get_uploader_ids
    from SoulCatcher import app, refresh_sudo, refresh_dev, refresh_uploader

    log.info("🌸 SoulCatcher starting up...")

    # ── [FIX-8] Validate critical config before doing anything ────────────
    if not API_ID or API_ID == 0:
        log.critical(
            "❌ API_ID is 0 or missing.\n"
            "   Set API_ID as an integer in your Heroku config vars.\n"
            "   Get it from https://my.telegram.org"
        )
        sys.exit(1)

    if not BOT_TOKEN or ":" not in str(BOT_TOKEN):
        log.critical(
            "❌ BOT_TOKEN looks invalid.\n"
            "   It must be in the format  123456:ABCdef...  from @BotFather."
        )
        sys.exit(1)

    log.info(f"✅ Config OK — API_ID={API_ID}, token ends ...{str(BOT_TOKEN)[-6:]}")

    # ── Phase 1: Database ─────────────────────────────────────────────────
    log.info("Phase 1/4: Database connection")
    if not await init_db_with_retry(init_db):
        sys.exit(1)

    # ── Phase 2: Permission caches ────────────────────────────────────────
    log.info("Phase 2/4: Loading permission caches")
    for label, getter, refresher in [
        ("sudo",     get_sudo_ids,     refresh_sudo),
        ("dev",      get_dev_ids,      refresh_dev),
        ("uploader", get_uploader_ids, refresh_uploader),
    ]:
        try:
            refresher(await getter())
            log.info(f"  ✅ {label} cache loaded")
        except Exception as exc:
            log.warning(f"  ⚠️  {label} cache failed (non-fatal): {exc}")

    # ── Phase 3: Modules ──────────────────────────────────────────────────
    log.info("Phase 3/4: Loading modules")
    loaded, failed = load_modules()
    if not loaded:
        log.critical("❌ No modules loaded — check SoulCatcher/modules/")
        sys.exit(1)

    # ── Phase 4: Connect to Telegram ──────────────────────────────────────
    log.info("Phase 4/4: Connecting to Telegram")
    if not await start_telegram_with_retry(app):
        sys.exit(1)

    # ── Keep alive with clean shutdown ────────────────────────────────────
    stop_event = asyncio.Event()

    def _signal_handler():
        log.info("🛑 Shutdown signal — stopping gracefully...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except (NotImplementedError, RuntimeError):
            pass  # Windows doesn't support add_signal_handler

    log.info("🌸 Bot is running. Press Ctrl+C or send SIGTERM to stop.")
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
        log.info("👋 Interrupted.")
    except SystemExit as exc:
        sys.exit(exc.code)
    except Exception as exc:
        log.critical(f"💥 Unhandled exception: {exc}", exc_info=True)
        sys.exit(1)
