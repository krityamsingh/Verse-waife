"""SoulCatcher/modules/wguess.py

Command:
  /wguess  —  Start YOUR OWN personal word guessing game.

PER-USER DESIGN:
  - Every user gets their OWN unique word — completely independent
  - Multiple users can play at the same time in the same chat
  - Each user has 15 seconds to guess THEIR word only
  - The bot reads every message and matches it against the sender's word
  - Only the player who started a round can win that round
  - 50–100 kakera reward per correct guess

FLOW:
  1. /wguess → inline: 🌸 4 Letters  |  🌸 5 Letters  |  ❌ Cancel
  2. Button click → bot DMs or replies with that user's masked word
  3. User types the word in chat → bot checks only against their word
  4. 15s timeout per user, independent of other players
"""
from __future__ import annotations

import asyncio
import logging
import random
import string

from pyrogram import filters, enums
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)

from .. import app
from ..database import get_or_create_user, add_balance

log  = logging.getLogger("SoulCatcher.wguess")
HTML = enums.ParseMode.HTML

# ── Word Lists ────────────────────────────────────────────────────────────────

_W4 = [
    "FIRE","WIND","RAIN","SOUL","DARK","MOON","STAR","ROSE","WOLF","KING",
    "GOLD","IRON","BLUE","LEAF","DAWN","DUSK","SAND","WAVE","SNOW","MIST",
    "JADE","RUBY","HALO","BOLT","CAGE","ECHO","FATE","GLOW","HUNT","IDOL",
    "KEEN","LORE","MAZE","NOVA","OATH","PACT","RAGE","SAGE","TALE","VEIL",
    "ZEAL","APEX","BANE","CLAW","EDGE","FANG","GALE","HAZE","ISLE","JOLT",
    "KNOT","LASH","MACE","NAIL","PIKE","RUNE","SCAR","URGE","VALE","WARD",
    "YOKE","ZONE","ACID","BARK","CORD","DIVE","EARL","FLUX","GRIP","HELM",
    "LAIR","MARK","NECK","OMEN","PATH","REED","SEED","TIDE","UNIT","VANE",
    "WHIP","YARN","ARCH","BONE","CAVE","DUKE","EPIC","FIST","GUST","HOOK",
    "IRIS","JAIL","KITE","LIME","MOSS","NODE","OPAL","PACE","REEF","SILK",
    "TUSK","VINE","WAKE","YELL","ZINC","ATOM","BULL","CROW","DUEL","FOAM",
    "GRIN","HOWL","ICON","JUMP","KICK","LINK","MYTH","NOON","OVAL","PUMP",
    "QUIZ","ROAR","STEM","TRAP","UNDO","AXLE","BLUR","CLAM","DRIP","ENVY",
    "FERN","HYMN","KEEL","LENS","MAST","NUMB","OGRE","PORE","RAFT","SLAM",
    "TWIN","WILT","YAWN","ZOOM","ALLY","BOLD","CALM","DASH","HIDE","INCH",
]

_W5 = [
    "FLAME","STORM","BLADE","GHOST","NIGHT","LIGHT","RAVEN","SWORD","TIGER",
    "ANGEL","BLOOD","CROWN","DEATH","EMBER","FROST","GRACE","HEART","HONOR",
    "IVORY","JEWEL","KARMA","LANCE","MAGIC","NYMPH","OCEAN","PEACE","QUEEN",
    "REALM","SHADE","THORN","UNION","VALOR","WITCH","YIELD","ABYSS","BLESS",
    "CHAOS","DREAD","EAGLE","FAIRY","GLOOM","HAVEN","JOKER","KNEEL","LOTUS",
    "MANOR","NERVE","ORBIT","PRISM","QUEST","SOLAR","TRUCE","ULTRA","VIPER",
    "WRATH","YEARN","ADEPT","BRAVE","CREEK","DEPOT","ELBOW","FLAIR","GLINT",
    "HASTE","IDEAL","JUDGE","KNACK","LUMEN","MIRTH","NOBLE","ONSET","PLUME",
    "RIVET","SCOUT","SWAMP","TALON","UNIFY","VIVID","WIELD","EXERT","ZILCH",
    "AROSE","BLAZE","CREST","DIRGE","FANGS","GUILE","HARSH","INFER","JOUST",
    "LYRIC","MUTED","NOTCH","OUTDO","PETAL","REGAL","SIGIL","TAUNT","UMBRA",
    "VERVE","VAULT","WHIRL","EXACT","YOUNG","AMASS","BOXER","DECOY","ELEGY",
    "FLINT","GRIPE","HAUNT","INPUT","KNAVE","NORTH","OZONE","PROWL","ROOST",
    "TIARA","VOILA","WOKEN","SCOUT","GRAIL","HOARD","FORGE","EVENT","DRAKE",
    "CRISP","BRINK","ABODE","ZESTY","YODEL","VIVID","TALON","SMITE","RIDGE",
    "QUILL","PULSE","OPTIC","NEXUS","LUSTY","JUMPY","INFER","HARSH","GUILE",
]

# Deduplicate
_W4 = list(set(_W4))
_W5 = list(set(_W5))

# ── Per-user game state ───────────────────────────────────────────────────────
# Key: (chat_id, user_id) → { word, masked, length, task, msg_id }
_games: dict[tuple[int, int], dict] = {}

# ── Masking ───────────────────────────────────────────────────────────────────

def _mask(word: str) -> str:
    """Hide 40–60% of letters randomly. Always ≥1 shown, ≥1 hidden."""
    n          = len(word)
    hide_n     = max(1, min(n - 1, round(n * random.uniform(0.4, 0.6))))
    hide_pos   = set(random.sample(range(n), hide_n))
    return "  ".join("_" if i in hide_pos else ch for i, ch in enumerate(word))


def _esc(t: str) -> str:
    return str(t).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")


def _mention(user) -> str:
    return f'<a href="tg://user?id={user.id}"><b>{_esc(user.first_name)}</b></a>'


# ── Keyboard ──────────────────────────────────────────────────────────────────

def _kb(user_id: int) -> InlineKeyboardMarkup:
    """Per-user callback data so only that user's click is processed."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌸  4 Letters", callback_data=f"wg:{user_id}:4"),
            InlineKeyboardButton("🌸  5 Letters", callback_data=f"wg:{user_id}:5"),
        ],
        [
            InlineKeyboardButton("❌  Cancel", callback_data=f"wg:{user_id}:cancel"),
        ],
    ])


# ── /wguess command ───────────────────────────────────────────────────────────

@app.on_message(filters.command("wguess"))
async def cmd_wguess(_, message: Message):
    user    = message.from_user
    chat_id = message.chat.id
    key     = (chat_id, user.id)

    if key in _games:
        g      = _games[key]
        masked = g["masked"]
        return await message.reply_text(
            f"🎮 {_mention(user)}, you already have an active round!\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔤 Your word: <code>{masked}</code>\n"
            f"⏳ <i>Keep guessing! Just type it in chat.</i>",
            parse_mode=HTML,
        )

    await message.reply_text(
        f"🌸 <b>WORD GUESS</b> — {_mention(user)}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Choose your word length to start your round!\n"
        f"⏳ You'll have <b>15 seconds</b> to guess.\n"
        f"💰 Correct = <b>50–100 🌸 kakera</b> reward!",
        parse_mode=HTML,
        reply_markup=_kb(user.id),
    )


# ── Callback: length selection ────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^wg:\d+:"))
async def wg_cb(client, cb: CallbackQuery):
    parts   = cb.data.split(":")          # ["wg", uid, action]
    owner   = int(parts[1])
    action  = parts[2]
    clicker = cb.from_user.id
    chat_id = cb.message.chat.id

    # Only the user who pressed /wguess can interact with their own buttons
    if clicker != owner:
        return await cb.answer("❌ This isn't your game!", show_alert=True)

    if action == "cancel":
        await cb.message.delete()
        return await cb.answer("Cancelled.", show_alert=False)

    if action not in ("4", "5"):
        return await cb.answer("Invalid.", show_alert=True)

    key = (chat_id, owner)
    if key in _games:
        return await cb.answer("⚠️ You already have an active round!", show_alert=True)

    length = int(action)
    pool   = _W4 if length == 4 else _W5
    word   = random.choice(pool)
    masked = _mask(word)
    shown  = masked.replace("  ", "").count
    blanks = masked.count("_")
    vis    = length - blanks

    game_text = (
        f"🎮 <b>YOUR WORD — {length} Letters</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔤 <code>{masked}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>{vis}/{length}</b> letters visible\n"
        f"⏳ <b>15 seconds</b> — just type it in chat!\n"
        f"💰 Correct = <b>50–100 🌸 kakera</b>!"
    )

    try:
        await cb.message.edit_text(game_text, parse_mode=HTML, reply_markup=None)
    except Exception:
        pass

    await cb.answer(f"Your {length}-letter word is ready! Type it in chat!", show_alert=False)

    # Register the game
    _games[key] = {
        "word":   word,
        "masked": masked,
        "length": length,
        "task":   None,
    }

    task = asyncio.create_task(_timeout(client, chat_id, owner, word, cb.from_user))
    _games[key]["task"] = task


# ── Message listener ──────────────────────────────────────────────────────────

@app.on_message(filters.group & filters.text & ~filters.command(["wguess"]))
async def wg_listener(_, message: Message):
    if not message.from_user:
        return

    user    = message.from_user
    chat_id = message.chat.id
    key     = (chat_id, user.id)

    if key not in _games:
        return  # This user has no active round

    game        = _games[key]
    guess_clean = "".join(c for c in message.text.strip().upper() if c in string.ascii_uppercase)

    if guess_clean != game["word"]:
        return  # Wrong — keep waiting silently

    # ── CORRECT ──────────────────────────────────────────────────────────────
    reward = random.randint(50, 100)

    if game.get("task") and not game["task"].done():
        game["task"].cancel()

    _games.pop(key, None)

    try:
        await get_or_create_user(
            user.id, user.username or "",
            user.first_name or "", getattr(user, "last_name", "") or "",
        )
        await add_balance(user.id, reward)
    except Exception as e:
        log.error("wguess reward error uid=%s: %s", user.id, e)

    await message.reply_text(
        f"🎉 <b>CORRECT!</b> — {_mention(user)}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ You guessed it!\n"
        f"🔤 The word was: <code>{game['word']}</code>\n"
        f"💰 <b>+{reward} 🌸 kakera</b> added!\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Use /wguess to play again!</i>",
        parse_mode=HTML,
    )


# ── Per-user timeout ──────────────────────────────────────────────────────────

async def _timeout(client, chat_id: int, user_id: int, word: str, user):
    try:
        await asyncio.sleep(15)
    except asyncio.CancelledError:
        return  # Guessed in time

    key = (chat_id, user_id)
    if key not in _games:
        return  # Already cleaned up (race-safe)

    _games.pop(key, None)

    revealed = "  ".join(list(word))
    name     = f'<a href="tg://user?id={user_id}"><b>{_esc(user.first_name)}</b></a>'

    try:
        await client.send_message(
            chat_id,
            f"⏰ <b>TIME'S UP!</b> — {name}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"😔 You didn't guess in time!\n"
            f"🔤 The word was: <code>{word}</code>\n"
            f"<code>{revealed}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>Use /wguess to try again!</i>",
            parse_mode=HTML,
        )
    except Exception as e:
        log.error("wguess timeout send error uid=%s: %s", user_id, e)
