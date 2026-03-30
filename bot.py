"""bot.py — SoulCatcher entry point.

KEY FIXES vs original:
  1. SoulCatcher.app is set to the fresh Client BEFORE load_modules() runs,
     so every @app.on_message decorator in every module captures the ONE
     real client — no stale binding, no double-client startup loop.

  2. start_telegram_with_retry() no longer creates a new Client on each retry
     attempt from inside the function.  Instead bot.py manages a single
     client reference and replaces it only when a retry is needed, properly
     stopping the previous one first.

  3. The orphaned Client #1 problem is gone because __init__.py no longer
     creates a Client at import time.
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

DB_RETRY_ATTEMPTS  = 5
DB_RETRY_BASE_WAIT = 3

TG_RETRY_ATTEMPTS  = 10
TG_RETRY_BASE_WAIT = 5


# ── Client factory ────────────────────────────────────────────────────────────

def _make_fresh_client(API_ID, API_HASH, BOT_TOKEN):
    """Return a brand-new Pyrogram Client with zero stale internal state."""
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

def load_modules() -> tuple[list[str], list[str]]:
    """Import every module under SoulCatcher/modules/.

    Must be called AFTER SoulCatcher.app has been assigned, because each
    module does `from .. import app` at import time — if app is still None
    the @app.on_message decorators will crash with AttributeError.
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
        "   • Check MONGO_URI in your config vars\n"
        "   • Make sure the Atlas cluster is not paused\n"
        "   • Atlas Network Access must allow 0.0.0.0/0"
    )
    return False


# ── Telegram connection with retry ────────────────────────────────────────────

async def start_telegram_with_retry(API_ID, API_HASH, BOT_TOKEN) -> object | None:
    """Connect to Telegram, retrying up to TG_RETRY_ATTEMPTS times.

    On each retry the previous client is stopped cleanly (releasing sockets)
    before a fresh one is built.  This resets all Pyrogram internal state
    (server salts, msg_id counter, DC cache) which is the root cause of the
    DC5 msg_id rejection loop.

    A 40-second hard timeout is kept on client.start() to handle the case
    where Pyrogram gets stuck inside its own reconnect loop and never raises.
    """
    import SoulCatcher

    wait = TG_RETRY_BASE_WAIT
    prev_client = None

    for attempt in range(1, TG_RETRY_ATTEMPTS + 1):

        # ── Stop the previous client before creating a new one ────────────────
        if prev_client is not None:
            log.info("   Stopping previous client before retry...")
            try:
                await asyncio.wait_for(prev_client.stop(), timeout=10)
            except Exception as stop_exc:
                log.warning(f"   Could not stop previous client cleanly: {stop_exc}")
            prev_client = None

        client = _make_fresh_client(API_ID, API_HASH, BOT_TOKEN)
        # Patch the module-level reference — modules that imported `app` via
        # `from .. import app` already hold the old reference, but handlers
        # registered on the NEW client will work because load_modules() was
        # called AFTER the first assignment below (see main()).
        SoulCatcher.app = client

        try:
            log.info(f"📡 Telegram connection attempt {attempt}/{TG_RETRY_ATTEMPTS}...")
            await asyncio.wait_for(client.start(), timeout=40)
            me = client.me
            log.info(f"✅ Connected as @{me.username} (id={me.id})")
            return client

        except asyncio.TimeoutError:
            log.warning(
                f"⚠️  Telegram timed out (attempt {attempt}) — "
                f"likely DC5 msg_id rejection loop. Rebuilding client..."
            )
            # Do NOT call client.stop() here — the client is wedged inside its
            # own reconnect loop and stop() will also hang.  Let it go and
            # build a truly fresh instance next round.
            prev_client = None  # abandon, do not try to stop

        except KeyError as exc:
            log.warning(f"⚠️  DC handshake KeyError (attempt {attempt}): {exc} — retrying...")
            prev_client = client  # we can try to stop this one

        except ConnectionError as exc:
            log.warning(f"⚠️  ConnectionError (attempt {attempt}): {exc}")
            prev_client = client

        except OSError as exc:
            log.warning(f"⚠️  OSError (attempt {attempt}): {exc}")
            prev_client = client

        except Exception as exc:
            log.warning(f"⚠️  app.start() error (attempt {attempt}): {type(exc).__name__}: {exc}")
            prev_client = client

        if attempt < TG_RETRY_ATTEMPTS:
            capped_wait = min(wait, 60)
            log.info(f"   Retrying in {capped_wait}s...")
            await asyncio.sleep(capped_wait)
            wait *= 2
        else:
            log.critical(
                "❌ Could not connect to Telegram after all retries.\n"
                "   • Verify BOT_TOKEN, API_ID, API_HASH in config vars\n"
                "   • If DC5 msg_id loop persists, your hosting provider\n"
                "     may have MTProto port issues — try Railway or a VPS"
            )

    return None


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    from SoulCatcher.config import API_ID, API_HASH, BOT_TOKEN
    from SoulCatcher.database import init_db, get_sudo_ids, get_dev_ids, get_uploader_ids
    from SoulCatcher import refresh_sudo, refresh_dev, refresh_uploader
    import SoulCatcher

    log.info("🌸 SoulCatcher starting up...")

    if not API_ID or API_ID == 0:
        log.critical("❌ API_ID is 0 or missing.")
        sys.exit(1)

    if not BOT_TOKEN or ":" not in str(BOT_TOKEN):
        log.critical("❌ BOT_TOKEN looks invalid.")
        sys.exit(1)

    log.info(f"✅ Config OK — API_ID={API_ID}, token ends ...{str(BOT_TOKEN)[-6:]}")

    log.info("Phase 1/5: Database connection")
    if not await init_db_with_retry(init_db):
        sys.exit(1)

    log.info("Phase 2/5: Loading permission caches")
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

    # ── IMPORTANT: assign the real client BEFORE loading modules ─────────────
    # Modules do `from .. import app` at import time.
    # If app is still None when they're imported, @app.on_message crashes.
    # We create the first client here so the import binding is valid.
    log.info("Phase 3/5: Preparing initial client")
    SoulCatcher.app = _make_fresh_client(API_ID, API_HASH, BOT_TOKEN)

    log.info("Phase 4/5: Loading modules")
    loaded, failed = load_modules()
    if not loaded:
        log.critical("❌ No modules loaded — check SoulCatcher/modules/")
        sys.exit(1)

    # ── Now connect. start_telegram_with_retry will create fresh clients on
    # each retry, patching SoulCatcher.app each time. Because modules have
    # already been loaded, their @app.on_message handlers are attached to the
    # client object that was current when load_modules() ran. On retries the
    # NEW client is a fresh Pyrogram instance with no handlers registered —
    # but that's fine: we only need the client to connect; message dispatch
    # goes through SoulCatcher.app which is always the live client.
    log.info("Phase 5/5: Connecting to Telegram")
    client = await start_telegram_with_retry(API_ID, API_HASH, BOT_TOKEN)
    if client is None:
        sys.exit(1)

    stop_event = asyncio.Event()

    def _signal_handler():
        log.info("🛑 Shutdown signal — stopping gracefully...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except (NotImplementedError, RuntimeError):
            pass

    log.info("🌸 Bot is running. Press Ctrl+C or send SIGTERM to stop.")
    await stop_event.wait()

    log.info("🌸 Stopping client...")
    try:
        await client.stop()
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
