"""
SoulCatcher/modules/menu.py

Auto-registers all bot commands into Telegram's menu button
immediately after the client connects (on the first raw update).

Scopes set:
  • BotCommandScopeAllPrivateChats  — commands usable in DM
  • BotCommandScopeAllGroupChats    — commands usable in groups
  • BotCommandScopeDefault          — fallback for everything else
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
# ─────────────────────────────────────────────────────────────────────────────

PRIVATE_COMMANDS: list[tuple[str, str]] = [
    ("start",        "Open the main menu"),
    ("profile",      "Your profile card — XP, level, badges"),
    ("status",       "Full player stats — collection %, rank, economy"),
    ("bal",          "Check your kakera balance"),
    ("harem",        "Browse your character collection"),
    ("daily",        "Claim your daily kakera reward"),
    ("spin",         "Spin the wheel for bonus kakera"),
    ("pay",          "Transfer kakera to another user"),
    ("cheque",       "Issue a kakera cheque"),
    ("cashcheque",   "Cash a received cheque"),
    ("sell",         "Sell a character for kakera"),
    ("burn",         "Burn a character for guaranteed kakera"),
    ("gift",         "Gift a character to another user"),
    ("trade",        "Start a character trade with someone"),
    ("wish",         "Add a character to your wishlist"),
    ("wishlist",     "View your wishlist"),
    ("unwish",       "Remove a character from your wishlist"),
    ("setfav",       "Set your favourite character"),
    ("view",         "View your favourite character card"),
    ("sort",         "Change harem sort order"),
    ("market",       "Browse the player market"),
    ("buy",          "Buy a market listing"),
    ("marry",        "Marry another player"),
    ("propose",      "Send a marriage proposal"),
    ("epropose",     "Emergency propose — skip confirmation"),
    ("basket",       "View your marriage basket"),
    ("wguess",       "Play the word guessing mini-game"),
    ("rarityinfo",   "Rarity tier info and drop rates"),
    ("check",        "Browse the character database"),
    ("all",          "Characters uploaded per rarity"),
    ("richest",      "Top 10 wealthiest players"),
    ("topcollector", "Top 10 collectors by character count"),
    ("event",        "Current event mode and multipliers"),
    ("summon",       "Summon a random soul to duel"),
    ("exitsummon",   "Abandon your current summon"),
]

GROUP_COMMANDS: list[tuple[str, str]] = [
    ("harem",        "Browse your character collection"),
    ("profile",      "Your profile card"),
    ("status",       "Your full player stats"),
    ("bal",          "Check your kakera balance"),
    ("daily",        "Claim your daily kakera"),
    ("spin",         "Spin the wheel for kakera"),
    ("pay",          "Transfer kakera to another user"),
    ("sell",         "Sell a character for kakera"),
    ("burn",         "Burn a character for kakera"),
    ("gift",         "Gift a character to another user"),
    ("trade",        "Start a character trade"),
    ("wish",         "Add a character to your wishlist"),
    ("wishlist",     "View your wishlist"),
    ("setfav",       "Set your favourite character"),
    ("view",         "View your favourite character card"),
    ("sort",         "Change harem sort order"),
    ("market",       "Browse the player market"),
    ("buy",          "Buy a market listing"),
    ("marry",        "Marry another player"),
    ("propose",      "Send a marriage proposal"),
    ("wguess",       "Play the word guessing mini-game"),
    ("check",        "Browse the character database"),
    ("rarityinfo",   "Rarity tier info"),
    ("richest",      "Top 10 wealthiest players"),
    ("topcollector", "Top 10 collectors"),
    ("event",        "Current event and multipliers"),
    ("summon",       "Summon a random soul to duel"),
    ("exitsummon",   "Abandon your current summon"),
    ("drop",         "Force a character spawn"),
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
