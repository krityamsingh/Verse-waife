"""
SoulCatcher/modules/menu.py

Auto-registers all bot commands into Telegram's menu button
immediately after the client connects (on the first raw update).

Scopes set:
  • BotCommandScopeAllPrivateChats  — commands usable in DM
  • BotCommandScopeAllGroupChats    — commands usable in groups
  • BotCommandScopeDefault          — fallback for everything else

Commands are split into three tiers based on who can see/use them:
  USER       — everyone
  SUDO/DEV   — sudo/dev users (set via chat scope when needed)
  OWNER      — owner only (not pushed to menu — too sensitive)

Design:
  - This module registers ONE handler on @app.on_message(filters.all)
    with the lowest priority (group=999) so it fires after everything
    else. On the first call it sets the menu then removes itself.
  - No manual call needed in bot.py — works purely via module import.
"""

from __future__ import annotations

import logging
from pyrogram import filters
from pyrogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeAllGroupChats,
    BotCommandScopeDefault,
    Message,
)

from .. import app

log = logging.getLogger("SoulCatcher.menu")

_menu_set = False   # run once guard


# ─────────────────────────────────────────────────────────────────────────────
#  COMMAND TABLES
#  Format: (command, description)
#  Keep descriptions ≤ 255 chars (Telegram hard limit).
#  Telegram shows at most 100 commands per scope.
# ─────────────────────────────────────────────────────────────────────────────

# Commands available to every user in private chat
PRIVATE_COMMANDS: list[tuple[str, str]] = [
    ("start",         "Open the main menu"),
    ("profile",       "Your profile card — XP, level, badges"),
    ("status",        "Full player stats — collection %, rank, economy"),
    ("bal",           "Check your kakera balance"),
    ("harem",         "Browse your character collection"),
    ("daily",         "Claim your daily kakera reward"),
    ("spin",          "Spin the wheel for bonus kakera"),
    ("pay",           "Transfer kakera to another user"),
    ("cheque",        "Issue a kakera cheque"),
    ("cashcheque",    "Cash a received cheque"),
    ("sell",          "Sell a character for kakera"),
    ("burn",          "Burn a character for guaranteed kakera"),
    ("gift",          "Gift a character to another user"),
    ("trade",         "Start a character trade with someone"),
    ("wish",          "Add a character to your wishlist"),
    ("wishlist",      "View your wishlist"),
    ("unwish",        "Remove a character from your wishlist"),
    ("setfav",        "Set your favourite character"),
    ("view",          "View your favourite character card"),
    ("sort",          "Change harem sort order"),
    ("market",        "Browse the player market"),
    ("buy",           "Buy a market listing"),
    ("marry",         "Marry another player"),
    ("propose",       "Send a marriage proposal"),
    ("epropose",      "Emergency propose (skip confirmation)"),
    ("basket",        "View your marriage basket"),
    ("wguess",        "Play the word guessing mini-game"),
    ("rarityinfo",    "Rarity tier info and drop rates"),
    ("check",         "Browse the character database"),
    ("all",           "Characters uploaded per rarity"),
    ("richest",       "Top 10 wealthiest players"),
    ("topcollector",  "Top 10 collectors by character count"),
    ("event",         "Current event mode and multipliers"),
    ("summon",        "Summon a random soul to duel"),
    ("exitsummon",    "Abandon your current summon"),
]

# Commands available to every user in group chats
GROUP_COMMANDS: list[tuple[str, str]] = [
    ("harem",         "Browse your character collection"),
    ("profile",       "Your profile card"),
    ("status",        "Your full player stats"),
    ("bal",           "Check your kakera balance"),
    ("daily",         "Claim your daily kakera"),
    ("spin",          "Spin the wheel for kakera"),
    ("pay",           "Transfer kakera to another user"),
    ("sell",          "Sell a character for kakera"),
    ("burn",          "Burn a character for kakera"),
    ("gift",          "Gift a character to another user"),
    ("trade",         "Start a character trade"),
    ("wish",          "Add a character to your wishlist"),
    ("wishlist",      "View your wishlist"),
    ("setfav",        "Set your favourite character"),
    ("view",          "View your favourite character card"),
    ("sort",          "Change harem sort order"),
    ("market",        "Browse the player market"),
    ("buy",           "Buy a market listing"),
    ("marry",         "Marry another player"),
    ("propose",       "Send a marriage proposal"),
    ("wguess",        "Play the word guessing mini-game"),
    ("check",         "Browse the character database"),
    ("rarityinfo",    "Rarity tier info"),
    ("richest",       "Top 10 wealthiest players"),
    ("topcollector",  "Top 10 collectors"),
    ("event",         "Current event and multipliers"),
    ("summon",        "Summon a random soul to duel"),
    ("exitsummon",    "Abandon your current summon"),
    ("drop",          "Force a character spawn (cooldown applies)"),
]


# ─────────────────────────────────────────────────────────────────────────────
#  REGISTRATION
# ─────────────────────────────────────────────────────────────────────────────

async def register_menu(client) -> None:
    """Push all command tables to Telegram. Called once on first update."""
    try:
        private_cmds = [BotCommand(cmd, desc) for cmd, desc in PRIVATE_COMMANDS]
        group_cmds   = [BotCommand(cmd, desc) for cmd, desc in GROUP_COMMANDS]

        # Private scope
        await client.set_bot_commands(
            private_cmds,
            scope=BotCommandScopeAllPrivateChats(),
        )
        log.info("Menu: private scope set (%d commands)", len(private_cmds))

        # Group scope
        await client.set_bot_commands(
            group_cmds,
            scope=BotCommandScopeAllGroupChats(),
        )
        log.info("Menu: group scope set (%d commands)", len(group_cmds))

        # Default fallback (what shows before user opens DM or group)
        await client.set_bot_commands(
            private_cmds,
            scope=BotCommandScopeDefault(),
        )
        log.info("Menu: default scope set")

        log.info("✅ Bot menu registered successfully")

    except Exception as e:
        log.error("Menu registration failed: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
#  TRIGGER — fires once on the first incoming update after bot connects
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.all, group=999)
async def _menu_on_first_update(client, message: Message) -> None:
    """
    Runs once on the very first message received after startup,
    registers the full bot menu, then removes itself so it never
    fires again for the rest of the session.
    """
    global _menu_set
    if _menu_set:
        return
    _menu_set = True

    # Unregister this handler immediately — we only need one shot
    try:
        from pyrogram.handlers import MessageHandler
        app.remove_handler(
            MessageHandler(_menu_on_first_update, filters.all),
            group=999,
        )
    except Exception:
        pass   # Not critical — _menu_set guards against re-running anyway

    log.info("First update received — registering bot menu…")
    await register_menu(client)
