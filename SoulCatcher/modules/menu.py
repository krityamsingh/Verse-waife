"""
SoulCatcher/modules/menu.py

Auto-registers all bot commands into Telegram's menu button
immediately after the client connects (on the first raw update).

Scopes set:
  • BotCommandScopeAllPrivateChats  — commands usable in DM
  • BotCommandScopeAllGroupChats    — commands usable in groups
  • BotCommandScopeDefault          — fallback for everything else

NOTE: Every command listed here MUST have a matching @app.on_message
handler. Phantom / unregistered commands have been removed and real
commands that were previously missing have been added.
"""

from __future__ import annotations

import logging
from pyrogram import filters
from pyrogram.handlers import MessageHandler
from pyrogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeAllGroupChats,
    BotCommandScopeDefault,
    Message,
)

from .. import app

log = logging.getLogger("SoulCatcher.menu")

_menu_set = False


# ─────────────────────────────────────────────────────────────────────────────
#  COMMAND TABLES
#  Only commands with a live @app.on_message handler are listed here.
#  Admin/sudo/dev/uploader commands are intentionally omitted — they are
#  power-user commands that clutter the public menu.
# ─────────────────────────────────────────────────────────────────────────────

PRIVATE_COMMANDS: list[tuple[str, str]] = [
    # ── Core ─────────────────────────────────────────────────────────────────
    ("start",         "Open the main menu"),
    ("profile",       "Your profile card — XP, level, badges"),
    ("status",        "Full player stats — collection %, rank, economy"),
    ("bal",           "Check your kakera balance"),

    # ── Collection ────────────────────────────────────────────────────────────
    ("harem",         "Browse your character collection"),
    ("fav",           "Set or view your favourite (harem cover) character"),
    ("check",         "Look up a character card by ID — /check <id>"),
    ("all",           "Characters uploaded per rarity tier"),
    ("claim",         "Claim your free daily character"),
    ("claiminfo",     "Check your daily claim cooldown"),

    # ── Economy ───────────────────────────────────────────────────────────────
    ("daily",         "Claim your daily kakera reward"),
    ("spin",          "Spin the wheel for bonus kakera"),
    ("pay",           "Transfer kakera to another user (reply + amount)"),
    ("cheque",        "Issue a kakera cheque card (reply + amount)"),
    ("cashcheque",    "Cash a received cheque — /cashcheque <id>"),

    # ── Trading & Transfers ───────────────────────────────────────────────────
    ("sell",          "Sell a character for kakera — /sell <instance_id>"),
    ("burn",          "Burn characters for guaranteed kakera"),
    ("gift",          "Gift a character to someone — /gift <instance_id>"),
    ("trade",         "Propose a character swap — /trade <my_id> <their_id>"),

    # ── Wishlist ──────────────────────────────────────────────────────────────
    ("wish",          "Add a character to your wishlist — /wish <char_id>"),
    ("wishlist",      "View your wishlist (max 25 entries)"),
    ("unwish",        "Remove a character from your wishlist"),

    # ── Social ────────────────────────────────────────────────────────────────
    ("marry",         "Marry a random character (60 s cooldown)"),
    ("propose",       "Propose to a character — guaranteed after 4 attempts"),
    ("epropose",      "Cancel your current propose encounter"),

    # ── Mini-game ─────────────────────────────────────────────────────────────
    ("wguess",        "Play the word guessing mini-game"),

    # ── Leaderboards & Info ───────────────────────────────────────────────────
    ("richest",       "Top 10 wealthiest players by kakera"),
    ("topcollector",  "Top 10 collectors by character count"),
    ("rarityinfo",    "Full rarity tier table with drop rates"),
    ("event",         "Current game mode and spawn multipliers"),

    # ── Summon ────────────────────────────────────────────────────────────────
    ("summon",        "Summon a random soul to duel — group only"),
    ("exitsummon",    "Abandon your current summon ritual"),
]

GROUP_COMMANDS: list[tuple[str, str]] = [
    # ── Core ─────────────────────────────────────────────────────────────────
    ("harem",         "Browse your character collection"),
    ("profile",       "Your profile card"),
    ("status",        "Your full player stats"),
    ("bal",           "Check your kakera balance"),
    ("fav",           "Set your favourite character"),

    # ── Spawns ────────────────────────────────────────────────────────────────
    ("drop",          "Force a character spawn (group cooldown applies)"),
    ("claim",         "Claim your free daily character"),
    ("claiminfo",     "Check your daily claim cooldown"),

    # ── Economy ───────────────────────────────────────────────────────────────
    ("daily",         "Claim your daily kakera"),
    ("spin",          "Spin the wheel for kakera"),
    ("pay",           "Transfer kakera to another user"),
    ("cheque",        "Issue a kakera cheque"),
    ("cashcheque",    "Cash a received cheque"),

    # ── Collection actions ────────────────────────────────────────────────────
    ("sell",          "Sell a character for kakera"),
    ("burn",          "Burn characters for kakera"),
    ("gift",          "Gift a character to another user"),
    ("trade",         "Start a character trade"),

    # ── Wishlist ──────────────────────────────────────────────────────────────
    ("wish",          "Add a character to your wishlist"),
    ("wishlist",      "View your wishlist"),
    ("unwish",        "Remove a character from your wishlist"),

    # ── Social ────────────────────────────────────────────────────────────────
    ("marry",         "Marry a random character"),
    ("propose",       "Propose to a character"),
    ("epropose",      "Cancel your current propose"),

    # ── Mini-game ─────────────────────────────────────────────────────────────
    ("wguess",        "Play the word guessing mini-game"),

    # ── Info & Leaderboards ───────────────────────────────────────────────────
    ("check",         "Look up a character card — /check <id>"),
    ("all",           "Characters uploaded per rarity tier"),
    ("rarityinfo",    "Full rarity tier info"),
    ("richest",       "Top 10 wealthiest players"),
    ("topcollector",  "Top 10 collectors"),
    ("event",         "Current event mode and multipliers"),

    # ── Summon ────────────────────────────────────────────────────────────────
    ("summon",        "Summon a random soul to duel"),
    ("exitsummon",    "Abandon your current summon"),
]


# ─────────────────────────────────────────────────────────────────────────────
#  REGISTRATION
# ─────────────────────────────────────────────────────────────────────────────

async def register_menu(client) -> None:
    try:
        private_cmds = [BotCommand(cmd, desc) for cmd, desc in PRIVATE_COMMANDS]
        group_cmds   = [BotCommand(cmd, desc) for cmd, desc in GROUP_COMMANDS]

        await client.set_bot_commands(
            private_cmds,
            scope=BotCommandScopeAllPrivateChats(),
        )
        log.info("Menu: private scope — %d commands", len(private_cmds))

        await client.set_bot_commands(
            group_cmds,
            scope=BotCommandScopeAllGroupChats(),
        )
        log.info("Menu: group scope — %d commands", len(group_cmds))

        await client.set_bot_commands(
            private_cmds,
            scope=BotCommandScopeDefault(),
        )
        log.info("Menu: default scope set")
        log.info("✅ Bot menu registered")

    except Exception as e:
        log.error("Menu registration failed: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
#  TRIGGER  — fires once on the first incoming update, then removes itself
#
#  FIX: store the handler as a module-level variable so remove_handler()
#       gets the EXACT same object that was added. Creating a new
#       MessageHandler(...) inside the callback gives a different object
#       and causes  ValueError: list.remove(x): x not in list
# ─────────────────────────────────────────────────────────────────────────────

async def _menu_trigger(client, message: Message) -> None:
    global _menu_set
    if _menu_set:
        return
    _menu_set = True

    # Remove THIS handler using the module-level reference
    try:
        app.remove_handler(_menu_handler, group=999)
    except Exception as e:
        log.debug("remove_handler (non-fatal): %s", e)

    log.info("First update — registering bot menu…")
    await register_menu(client)


# Module-level reference — must exist before add_handler is called
_menu_handler = MessageHandler(_menu_trigger, filters.all)
app.add_handler(_menu_handler, group=999)
