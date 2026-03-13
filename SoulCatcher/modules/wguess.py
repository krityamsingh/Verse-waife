"""SoulCatcher/modules/wguess.py

Command:
  /wguess  —  Start a word guessing game in the group.

HOW IT WORKS:
  1. /wguess → inline keyboard asks: 4-letter words or 5-letter words
  2. After selection, a word is picked randomly and shown with letters masked
     (random positions hidden, at least 40% of letters shown as hints)
  3. Every group message is scanned — first correct guess wins
  4. Winner gets 50–100 random kakera reward
  5. If nobody guesses in 15 seconds → reveal the word, game ends
  6. Only one active game per chat at a time

MASKING STYLE (mixed):
  - Some letters shown as-is (uppercase)
  - Missing letters shown as _ with a space: K _ L L _ R
  - At least 1 letter always revealed, at least 1 always hidden
  - Pattern is randomly chosen each round for variety
"""
from __future__ import annotations

import asyncio
import logging
import random
import string
from typing import Optional

from pyrogram import filters
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)

from .. import app
from ..database import get_or_create_user, add_balance

log = logging.getLogger("SoulCatcher.wguess")

# ── Word Lists ─────────────────────────────────────────────────────────────────

WORDS_4 = [
    "fire", "wind", "rain", "soul", "dark", "moon", "star", "rose", "wolf",
    "king", "gold", "iron", "blue", "leaf", "dawn", "dusk", "sand", "wave",
    "snow", "mist", "jade", "ruby", "halo", "bolt", "cage", "echo", "fate",
    "glow", "hunt", "idol", "jest", "keen", "lore", "maze", "nova", "oath",
    "pact", "rage", "sage", "tale", "veil", "wrath", "zeal", "apex", "bane",
    "claw", "dusk", "edge", "fang", "gale", "haze", "isle", "jolt", "knot",
    "lash", "mace", "nail", "orbs", "pike", "rune", "scar", "thorn", "urge",
    "vale", "ward", "yoke", "zone", "acid", "bark", "cord", "dive", "earl",
    "flux", "grip", "helm", "lair", "mark", "neck", "omen", "path", "quit",
    "reed", "seed", "tide", "unit", "vane", "whip", "yarn", "zero", "arch",
    "bone", "cave", "duke", "epic", "fist", "gust", "hook", "iris", "jail",
    "kite", "lime", "moss", "node", "opal", "pace", "reef", "silk", "tusk",
    "vine", "wake", "xray", "yell", "zinc", "atom", "bull", "crow", "duel",
    "elm", "foam", "grin", "howl", "icon", "jump", "kick", "link", "myth",
    "noon", "oval", "pump", "quiz", "roar", "stem", "trap", "undo", "vamp",
    "waltz", "axle", "blur", "clam", "drip", "envy", "fern", "glow", "hymn",
    "icy", "jab", "keel", "lens", "mast", "numb", "ogre", "pore", "raft",
    "slam", "twin", "upon", "vow", "wilt", "yawn", "zoom", "ally", "bold",
    "calm", "dash", "erupt", "flare", "gran", "hide", "inch", "joy",
]

WORDS_5 = [
    "flame", "storm", "blade", "ghost", "night", "light", "raven", "sword",
    "tiger", "angel", "blood", "crown", "death", "ember", "frost", "grace",
    "heart", "honor", "ivory", "jewel", "karma", "lance", "magic", "nymph",
    "ocean", "peace", "queen", "realm", "shade", "thorn", "union", "valor",
    "witch", "xenon", "yield", "zephyr", "abyss", "bless", "chaos", "dread",
    "eagle", "fairy", "gloom", "haven", "illum", "joker", "kneel", "lotus",
    "manor", "nerve", "orbit", "prism", "quest", "reaper", "solar", "truce",
    "ultra", "viper", "wrath", "xenon", "yearn", "zonal", "adept", "brave",
    "creek", "depot", "elbow", "flair", "glint", "haste", "ideal", "judge",
    "knack", "lumen", "mirth", "noble", "onset", "plume", "rivet", "scout",
    "swamp", "talon", "unify", "vivid", "wield", "exert", "yodel", "zesty",
    "abode", "brink", "crisp", "drake", "event", "forge", "grail", "hoard",
    "infer", "joust", "karma", "lusty", "mantle", "nexus", "optic", "pulse",
    "quill", "ridge", "smite", "towel", "ultra", "vault", "whirl", "exact",
    "young", "zilch", "arose", "blaze", "crest", "dirge", "eclat", "fangs",
    "guile", "harsh", "inter", "jumpy", "kinky", "lunar", "monge", "north",
    "ozone", "prowl", "regal", "sigil", "taunt", "umbra", "verve", "woeful",
    "xenon", "yacht", "amass", "boxer", "cobalt", "decoy", "elegy", "flint",
    "gripe", "haunt", "input", "jokey", "knave", "lyric", "muted", "notch",
    "outdo", "petal", "roost", "shroud", "tiara", "upheaval", "voila", "woken",
]

# Deduplicate and ensure correct lengths
WORDS_4 = list({w.upper() for w in WORDS_4 if len(w) == 4})
WORDS_5 = list({w.upper() for w in WORDS_5 if len(w) == 5})

# ── Active Games Store ────────────────────────────────────────────────────────
# chat_id → { word, masked, task, message_id, length }
_active: dict[int, dict] = {}

# ── Masking Logic ─────────────────────────────────────────────────────────────

def _mask_word(word: str) -> str:
    """
    Randomly hide some letters of the word.
    - Always reveals at least 1 letter
    - Always hides at least 1 letter
    - Randomly picks which positions to hide (40–60% hidden)
    - Returns formatted string like: K _ L L _ R
    """
    n = len(word)
    # Decide how many to hide: between 40% and 60% of letters, min 1, max n-1
    hide_count = max(1, min(n - 1, round(n * random.uniform(0.4, 0.6))))
    hide_positions = set(random.sample(range(n), hide_count))
    parts = []
    for i, ch in enumerate(word):
        if i in hide_positions:
            parts.append("_")
        else:
            parts.append(ch)
    return "  ".join(parts)


# ── Inline Keyboard ───────────────────────────────────────────────────────────

def _main_kb() -> InlineKeyboardMarkup:
    """Light pink styled buttons for word length selection."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌸  4 Letters", callback_data="wg:4"),
            InlineKeyboardButton("🌸  5 Letters", callback_data="wg:5"),
        ],
        [
            InlineKeyboardButton("❌  Cancel",    callback_data="wg:cancel"),
        ],
    ])


# ── /wguess command ───────────────────────────────────────────────────────────

@app.on_message(filters.command("wguess"))
async def cmd_wguess(_, message: Message):
    chat_id = message.chat.id
    if chat_id in _active:
        word   = _active[chat_id]["word"]
        masked = _active[chat_id]["masked"]
        return await message.reply_text(
            f"🎮 <b>A game is already running!</b>\n"
            f"<code>{'  '.join(masked)}</code>\n"
            f"<i>Guess the word before time runs out!</i>",
            parse_mode="html",
        )

    await message.reply_text(
        "🌸 <b>WORD GUESS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Choose the word length to begin!\n"
        "<i>You have <b>15 seconds</b> to guess each word.</i>\n"
        "💰 Correct guess = <b>50–100 kakera</b> reward!",
        parse_mode="html",
        reply_markup=_main_kb(),
    )


# ── Callback: word length selection ──────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^wg:"))
async def wg_callback(client, cb: CallbackQuery):
    chat_id = cb.message.chat.id
    data    = cb.data.split(":")[1]

    # Only the person who triggered /wguess can pick (or anyone — we allow all)
    if data == "cancel":
        await cb.message.delete()
        return await cb.answer("Game cancelled.", show_alert=False)

    if data not in ("4", "5"):
        return await cb.answer("Invalid option.", show_alert=True)

    if chat_id in _active:
        return await cb.answer("⚠️ A game is already running!", show_alert=True)

    length = int(data)
    pool   = WORDS_4 if length == 4 else WORDS_5
    word   = random.choice(pool)
    masked = _mask_word(word)

    # Build the hint display
    hint_display = f"<code>{masked}</code>"
    blanks       = masked.count("_")
    shown        = length - blanks

    # Edit the selection message into the game message
    game_text = (
        f"🎮 <b>WORD GUESS — {length} Letters</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔤 {hint_display}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>{shown}/{length}</b> letters revealed\n"
        f"⏳ <b>15 seconds</b> to guess!\n"
        f"💰 First correct answer wins <b>50–100 🌸 kakera</b>!\n"
        f"<i>Just type the word in chat!</i>"
    )

    try:
        await cb.message.edit_text(game_text, parse_mode="html", reply_markup=None)
    except Exception:
        pass

    await cb.answer(f"Game started! {length}-letter word. Go!", show_alert=False)

    # Store game state
    _active[chat_id] = {
        "word":       word,
        "masked":     masked,
        "message_id": cb.message.id,
        "length":     length,
        "task":       None,
    }

    # Start the 15-second timeout
    task = asyncio.create_task(_timeout(client, chat_id, word, cb.message))
    _active[chat_id]["task"] = task


# ── Message listener for guesses ──────────────────────────────────────────────

@app.on_message(filters.group & filters.text & ~filters.command(["wguess"]))
async def wg_guess_listener(client, message: Message):
    chat_id = message.chat.id
    if chat_id not in _active:
        return  # No active game, ignore

    game = _active[chat_id]
    guess = message.text.strip().upper()

    # Must match exactly (letters only, ignore punctuation/spaces in guess)
    guess_clean = "".join(c for c in guess if c in string.ascii_uppercase)

    if guess_clean != game["word"]:
        return  # Wrong guess — keep listening silently

    # ── CORRECT GUESS ────────────────────────────────────────────────────────
    user   = message.from_user
    reward = random.randint(50, 100)

    # Cancel the timeout task
    if game.get("task") and not game["task"].done():
        game["task"].cancel()

    # Remove from active before any awaits to prevent race
    _active.pop(chat_id, None)

    # Credit the winner
    try:
        await get_or_create_user(
            user.id,
            user.username or "",
            user.first_name or "",
            getattr(user, "last_name", "") or "",
        )
        await add_balance(user.id, reward)
    except Exception as e:
        log.error("wguess reward error uid=%s: %s", user.id, e)

    # Announce winner
    name = f'<a href="tg://user?id={user.id}"><b>{_esc(user.first_name)}</b></a>'
    await message.reply_text(
        f"🎉 <b>CORRECT!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ {name} guessed it!\n"
        f"🔤 The word was: <code>{game['word']}</code>\n"
        f"💰 Reward: <b>+{reward} 🌸 kakera</b>!\n"
        f"━━━━━━━━━━━━━━━━━━━━",
        parse_mode="html",
    )


# ── 15-second timeout ─────────────────────────────────────────────────────────

async def _timeout(client, chat_id: int, word: str, game_msg):
    try:
        await asyncio.sleep(15)
    except asyncio.CancelledError:
        return  # Someone guessed correctly — task was cancelled

    # Time's up — nobody guessed
    _active.pop(chat_id, None)

    # Reveal the full word letter by letter
    revealed = "  ".join(list(word))

    try:
        await client.send_message(
            chat_id,
            f"⏰ <b>TIME'S UP!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"😔 Nobody guessed the word!\n"
            f"🔤 The answer was: <code>{word}</code>\n"
            f"<code>{revealed}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>Use /wguess to play again!</i>",
            parse_mode="html",
        )
    except Exception as e:
        log.error("wguess timeout send error chat=%s: %s", chat_id, e)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _esc(t: str) -> str:
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
