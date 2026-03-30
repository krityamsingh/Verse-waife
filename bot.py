"""bot.py — SoulCatcher entry point.

Architecture: ONE client, one assignment, one .start()
─────────────────────────────────────────────────────
The fundamental rule for Pyrogram handler registration:

    @app.on_message(...)  binds the handler to whichever Client object
    `app` points to AT DECORATION TIME (i.e. at module import).

So the correct boot sequence is:

    1. Create Client X
    2. Assign SoulCatcher.app = Client X
    3. Import all modules  →  handlers register on Client X
    4. Call Client X.start()   (NOT a new client — the SAME one)

If you call _make_fresh_client() again after step 3, the new client has
zero handlers and commands never fire.

On a DC5 timeout retry, we reload ALL modules onto the new client so
handlers re-register correctly.
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

def load_modules(reload: bool = False) -> tuple[list[str], list[str]]:
    """Import (or reload) every module under SoulCatcher/modules/.

    MUST be called after SoulCatcher.app is assigned — each module does
    `from .. import app` at import time and registers @app.on_message
    handlers on that exact object.

    Pass reload=True on a retry to re-register handlers on the new client.
    """
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

    CRITICAL: on the first attempt, SoulCatcher.app is already the client
    that has all handlers registered on it. We call .start() on THAT object.

    Only on failure do we create a new client — and we also reload all
    modules so handlers re-bind to the new object.
    """
    import SoulCatcher

    wait = TG_RETRY_BASE_WAIT

    for attempt in range(1, TG_RETRY_ATTEMPTS + 1):

        client = SoulCatcher.app  # always use whatever is currently assigned

        try:
            log.info(f"📡 Telegram connection attempt {attempt}/{TG_RETRY_ATTEMPTS}...")
            await asyncio.wait_for(client.start(), timeout=40)
            me = client.me
            log.info(f"✅ Connected as @{me.username} (id={me.id})")
            return client

        except asyncio.TimeoutError:
            log.warning(
                f"⚠️  Telegram timed out (attempt {attempt}) — "
                "DC5 msg_id loop. Rebuilding client + reloading modules..."
            )
            # Do NOT call .stop() — client is wedged, it will hang too.
            new_client = _make_fresh_client(API_ID, API_HASH, BOT_TOKEN)
            SoulCatcher.app = new_client
            log.info("   Reloading modules onto new client...")
            load_modules(reload=True)

        except KeyError as exc:
            log.warning(f"⚠️  DC handshake KeyError (attempt {attempt}): {exc}")
            try:
                await client.stop()
            except Exception:
                pass
            SoulCatcher.app = _make_fresh_client(API_ID, API_HASH, BOT_TOKEN)
            load_modules(reload=True)

        except ConnectionError as exc:
            log.warning(f"⚠️  ConnectionError (attempt {attempt}): {exc}")
            try:
                await client.stop()
            except Exception:
                pass
            SoulCatcher.app = _make_fresh_client(API_ID, API_HASH, BOT_TOKEN)
            load_modules(reload=True)

        except OSError as exc:
            log.warning(f"⚠️  OSError (attempt {attempt}): {exc}")
            try:
                await client.stop()
            except Exception:
                pass
            SoulCatcher.app = _make_fresh_client(API_ID, API_HASH, BOT_TOKEN)
            load_modules(reload=True)

        except Exception as exc:
            log.warning(f"⚠️  app.start() error (attempt {attempt}): {type(exc).__name__}: {exc}")
            try:
                await client.stop()
            except Exception:
                pass
            SoulCatcher.app = _make_fresh_client(API_ID, API_HASH, BOT_TOKEN)
            load_modules(reload=True)

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

    log.info("Phase 1/4: Database connection")
    if not await init_db_with_retry(init_db):
        sys.exit(1)

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

    # ── Create the ONE client, assign it, THEN load modules ──────────────────
    # Modules do `from .. import app` — they bind to this exact object.
    # .start() will be called on this same object in Phase 4.
    log.info("Phase 3/4: Loading modules")
    SoulCatcher.app = _make_fresh_client(API_ID, API_HASH, BOT_TOKEN)
    loaded, failed = load_modules()
    if not loaded:
        log.critical("❌ No modules loaded — check SoulCatcher/modules/")
        sys.exit(1)

    # ── start_telegram_with_retry calls .start() on SoulCatcher.app ──────────
    # First attempt uses the same client handlers are registered on.
    # Only on retry does it build a fresh client + reload modules.
    log.info("Phase 4/4: Connecting to Telegram")
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
