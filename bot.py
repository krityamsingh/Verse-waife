"""bot.py — SoulCatcher entry point."""

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


# ── Module loader ─────────────────────────────────────────────────────────────

def load_modules() -> tuple[list[str], list[str]]:
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


# ── Fresh client factory ──────────────────────────────────────────────────────

def _make_fresh_client(API_ID, API_HASH, BOT_TOKEN):
    """
    Return a brand-new Pyrogram Client instance.
    Called on every retry so there's zero stale internal state
    from the previous failed attempt.
    """
    from pyrogram import Client
    return Client(
        "SoulCatcher",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        sleep_threshold=60,
        in_memory=True,
    )


# ── Telegram app.start() with retry ──────────────────────────────────────────

async def start_telegram_with_retry(API_ID, API_HASH, BOT_TOKEN) -> object | None:
    """
    Retry connecting to Telegram up to TG_RETRY_ATTEMPTS times.

    Root cause of the loop: Pyrogram connects to DC2, completes auth,
    Telegram redirects to DC5. DC5 rejects every MTProto packet with
    'msg_id is lower than all stored values' (stale clock / server salt),
    causing an internal disconnect → reconnect → reject loop that never
    raises an exception back to us.

    Fix: hard 40s timeout on app.start(). On timeout we DISCARD the
    client entirely and build a fresh one — this resets all of Pyrogram's
    internal state (server salts, msg_id counter, DC cache) so the next
    attempt starts completely clean.
    """
    import SoulCatcher  # need to patch app reference after rebuild

    wait = TG_RETRY_BASE_WAIT
    for attempt in range(1, TG_RETRY_ATTEMPTS + 1):
        client = _make_fresh_client(API_ID, API_HASH, BOT_TOKEN)
        # Patch the module-level reference so handlers still work
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
                f"DC5 msg_id rejection loop. Discarding client and retrying..."
            )
            # Don't call client.stop() — the client is wedged inside the loop.
            # Just let it get garbage-collected and build a fresh one next round.

        except KeyError as exc:
            log.warning(
                f"⚠️  DC handshake KeyError (attempt {attempt}): {exc} — retrying..."
            )
            try:
                await client.stop()
            except Exception:
                pass

        except ConnectionError as exc:
            log.warning(f"⚠️  ConnectionError (attempt {attempt}): {exc}")
            try:
                await client.stop()
            except Exception:
                pass

        except OSError as exc:
            log.warning(f"⚠️  OSError (attempt {attempt}): {exc}")

        except Exception as exc:
            log.warning(f"⚠️  app.start() error (attempt {attempt}): {type(exc).__name__}: {exc}")
            try:
                await client.stop()
            except Exception:
                pass

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

    log.info("Phase 3/4: Loading modules")
    loaded, failed = load_modules()
    if not loaded:
        log.critical("❌ No modules loaded — check SoulCatcher/modules/")
        sys.exit(1)

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
