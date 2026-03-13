"""SoulCatcher/modules/wguess.py

Command:
  /wguess  —  Personal word guessing game (per-user, simultaneous).

FEATURES:
  - Each user gets their OWN word, fully independent of others
  - All words are simple, everyday English (nothing obscure)
  - 💡 Hint button: reveals one more hidden letter, costs 40 🌸 kakera
  - 15 second timer per user (resets on each /wguess)
  - 50–100 kakera reward on correct guess
  - Multiple users can play simultaneously in the same group
"""
from __future__ import annotations

import asyncio
import logging
import random
import string

from pyrogram import filters, enums
from pyrogram.handlers import MessageHandler
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)

from .. import app
from ..database import get_or_create_user, add_balance, get_balance, deduct_balance

log  = logging.getLogger("SoulCatcher.wguess")
HTML = enums.ParseMode.HTML

HINT_COST   = 20   # kakera deducted per hint
REWARD_MIN  = 50
REWARD_MAX  = 100
TIMEOUT_SEC = 15

# ── Easy Word Lists ───────────────────────────────────────────────────────────
# All common, everyday words — nothing rare or obscure

_W4 = [
    "BOOK","TREE","FISH","BIRD","CAKE","MILK","DOOR","HAND","FOOT","BALL",
    "BEAR","BELL","BIKE","BOAT","BONE","BOWL","CARD","CART","CLUB","COAT",
    "COIN","COOK","COOL","CORN","COST","COZY","CRAB","CROP","CROW","CUTE",
    "DARK","DAYS","DEAR","DEEP","DEER","DESK","DIRT","DISH","DOCK","DOVE",
    "DOWN","DRAW","DRUM","DUCK","DUMP","DUST","EARS","EGGS","EYES","FACE",
    "FACT","FALL","FARM","FAST","FIRE","FLAG","FLAT","FLEW","FLIP","FROG",
    "FULL","FUND","GAME","GATE","GIFT","GIRL","GLAD","GOAT","GOOD","GROW",
    "HAIR","HALL","HARD","HARM","HARP","HEAT","HEEL","HELP","HERO","HIGH",
    "HILL","HOME","HOOK","HORN","HOSE","HOST","HOUR","HUGE","HUMP","HUNT",
    "JUMP","KEEP","KICK","KIND","KISS","KITE","KNEE","LAMB","LAMP","LAND",
    "LANE","LAST","LATE","LAWN","LEAF","LEFT","LEMON","LIFE","LIME","LINE",
    "LION","LIST","LIVE","LOCK","LONG","LOOK","LOOP","LOVE","LUCK","MADE",
    "MAIL","MAKE","MANY","MARK","MEAL","MEAT","MICE","MILD","MILE","MINE",
    "MINT","MISS","MOLE","MORE","MOTH","MOVE","MUCH","MULE","MUST","NAME",
    "NEAT","NEST","NEWS","NEXT","NICE","NOTE","NOSE","OPEN","OVEN","OVER",
    "PARK","PART","PAST","PATH","PEAR","PICK","PILE","PINE","PINK","PIPE",
    "PLAN","PLAY","PLUM","PLUS","POOL","POOR","POST","PUSH","RACE","RAIN",
    "READ","REAL","RELY","REST","RICE","RICH","RIDE","RING","ROAD","ROCK",
    "ROLE","ROLL","ROOF","ROOM","ROOT","ROPE","ROSE","RULE","RUSH","RUST",
    "SAFE","SAIL","SALT","SAME","SAVE","SEAL","SHIP","SHOP","SILK","SING",
    "SINK","SIZE","SKIN","SKIP","SLOW","SLUG","SOFT","SOIL","SOME","SONG",
    "SORT","SOUP","SOUR","SPIN","STAR","STAY","STEM","STEP","STEW","STOP",
    "SUCH","SUIT","SWAN","SWIM","TALE","TALL","TAME","TANK","TAPE","TASK",
    "TEST","THAN","THEM","THEN","THIN","THIS","TIED","TILL","TIME","TINY",
    "TOAD","TOLD","TOLL","TONE","TOOL","TOWN","TRAM","TRAP","TRAY","TRIM",
    "TRIP","TRUE","TUNE","TURN","TWIN","TYPE","UPON","USER","VERY","VIEW",
    "VINE","VOTE","WALK","WALL","WARM","WASH","WASP","WAVE","WEAK","WEAR",
    "WEED","WEEK","WELL","WENT","WEST","WHEN","WIDE","WILD","WILL","WIND",
    "WING","WISE","WISH","WOLF","WOOD","WOOL","WORD","WORK","WORM","WRAP",
    "WREN","YEAR","YARD","YELL","YOLK","ZERO","ZOOM",
]

_W5 = [
    "APPLE","BEACH","BLACK","BRAVE","BREAD","BRICK","BRING","BROWN","BUILD",
    "CANDY","CATCH","CHAIR","CHEAP","CHECK","CHEEK","CHESS","CHEST","CHILD",
    "CHINA","CHIPS","CLAIM","CLAMP","CLANG","CLASS","CLAW","CLEAN","CLEAR",
    "CLIMB","CLOCK","CLOSE","CLOTH","CLOUD","COACH","COLOR","CORAL","COUNT",
    "COVER","CRACK","CRANE","CRASH","CRAZY","CREAM","CRIMP","CROSS","CROWD",
    "CROWN","CRUSH","CURVE","DANCE","DIARY","DIGIT","DIRTY","DIZZY","DOING",
    "DRAFT","DRAIN","DRAMA","DRINK","DRIVE","DROPS","DRUMS","DRYER","EARLY",
    "EARTH","ELBOW","EVERY","EVOKE","EXACT","FAIRY","FAITH","FANCY","FEAST",
    "FENCE","FIELD","FIFTH","FIGHT","FIXED","FLAME","FLASH","FLASK","FLEET",
    "FLESH","FLOOR","FLOUR","FLOWN","FLUID","FLUTE","FOCUS","FORGE","FORTH",
    "FOUND","FRAME","FRANK","FRESH","FRONT","FROZE","FRUIT","FUNNY","GAMES",
    "GHOST","GIANT","GIVEN","GLARE","GLASS","GLIDE","GLOBE","GLOOM","GLOVE",
    "GOING","GRADE","GRAPE","GRASS","GRAVY","GREAT","GREEN","GREET","GRILL",
    "GRIND","GROAN","GROVE","GROWN","GUARD","GUIDE","GUILD","GUISE","GULCH",
    "HAPPY","HEART","HEAVY","HELLO","HONEY","HORSE","HOTEL","HOUSE","HUMAN",
    "IGLOO","IMAGE","INBOX","INNER","INPUT","IRONY","JAPAN","JEWEL","JUICE",
    "JUMBO","KAYAK","KNIFE","KNEEL","KNOCK","LARGE","LASER","LAUGH","LEARN",
    "LEGAL","LEMON","LEVEL","LIGHT","LINER","LIVER","LOCAL","LODGE","LOGIC",
    "MAGIC","MARCH","MATCH","MAYOR","MEDAL","MERGE","MIGHT","MIXER","MODEL",
    "MONEY","MONTH","MORAL","MOUNT","MOUSE","MOUTH","MOVIE","MUSIC","NASTY",
    "NERVE","NEVER","NIGHT","NOBLE","NORTH","NURSE","OCCUR","OFFER","OFTEN",
    "ONION","ORDER","OTHER","OTTER","OUGHT","OUTER","OWNER","PAINT","PANEL",
    "PAPER","PARTY","PASTA","PATCH","PAUSE","PEACE","PEARL","PEDAL","PENNY",
    "PHONE","PHOTO","PIANO","PILOT","PIZZA","PLAIN","PLANE","PLANT","PLATE",
    "PLAZA","PLEAD","PLUCK","POINT","POLAR","POUND","POWER","PRESS","PRICE",
    "PRIDE","PRIME","PRINT","PRIOR","PRIZE","PROOF","PROUD","PROVE","PURSE",
    "QUEEN","QUEUE","QUIET","QUITE","QUOTA","QUOTE","RADAR","RADIO","RAISE",
    "RALLY","RANGE","RAPID","REACH","READY","REALM","REBEL","REFER","RELAX",
    "REPLY","RIDER","RISKY","RIVER","ROBOT","ROCKY","ROUND","ROUTE","ROYAL",
    "RUGBY","RULER","RURAL","SADLY","SAINT","SALAD","SAUCE","SCALE","SCARY",
    "SCENE","SCENT","SCORE","SCOUT","SCREW","SENSE","SERVE","SETUP","SEVEN",
    "SHADE","SHAKE","SHALL","SHAME","SHAPE","SHARE","SHARP","SHEEP","SHEER",
    "SHELF","SHELL","SHIFT","SHINE","SHIRT","SHOCK","SHOES","SHOOT","SHORT",
    "SHOUT","SIGHT","SINCE","SIXTH","SIXTY","SKILL","SKULL","SLEEP","SLICE",
    "SLIDE","SLOPE","SMALL","SMART","SMILE","SMOKE","SNAIL","SNAKE","SOLAR",
    "SOLID","SOLVE","SORRY","SOUTH","SPACE","SPARK","SPEAK","SPELL","SPEND",
    "SPICE","SPINE","SPITE","SPLIT","SPOKE","SPOON","SPORT","SPRAY","SQUAD",
    "STACK","STAFF","STAGE","STAIN","STAIR","STAMP","STAND","STARE","START",
    "STATE","STEAK","STEAL","STEAM","STEEP","STEER","STICK","STILL","STOCK",
    "STONE","STORE","STORM","STORY","STOVE","STRAP","STRAW","STUFF","SUGAR",
    "SUITE","SUNNY","SUPER","SWEAR","SWEET","SWIFT","SWORD","TABLE","TASTE",
    "TEACH","TEMPO","TENSE","THEIR","THERE","THICK","THING","THINK","THIRD",
    "THREE","THROW","THUMB","TIARA","TIGER","TIGHT","TIMER","TIRED","TITLE",
    "TOAST","TODAY","TOKEN","TOTAL","TOUCH","TOUGH","TOWEL","TOWER","TOXIC",
    "TRACK","TRADE","TRAIL","TRAIN","TRAIT","TREND","TROOP","TROUT","TRUCK",
    "TRULY","TRUST","TRUTH","TULIP","TUNER","TWIST","UNDER","UNION","UNTIL",
    "UPPER","UPSET","URBAN","USAGE","USUAL","UTTER","VALID","VALOR","VALUE",
    "VALVE","VIDEO","VIOLA","VIRAL","VISIT","VITAL","VIVID","VOCAL","VOICE",
    "WASTE","WATCH","WATER","WHALE","WHEAT","WHEEL","WHERE","WHICH","WHILE",
    "WHITE","WHOLE","WHOSE","WITCH","WOMEN","WORLD","WORSE","WORST","WORTH",
    "WOULD","WRITE","WRONG","YACHT","YIELD","YOUNG","YOURS","YOUTH","ZEBRA",
]

# Deduplicate and enforce length
_W4 = list({w for w in _W4 if len(w) == 4})
_W5 = list({w for w in _W5 if len(w) == 5})

# ── Per-user game state ───────────────────────────────────────────────────────
# (chat_id, user_id) → {word, revealed, length, task, hints_used}
# `revealed` is a list of bools: True = visible, False = hidden
_games: dict[tuple[int, int], dict] = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _esc(t: str) -> str:
    return str(t).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def _mention(user) -> str:
    return f'<a href="tg://user?id={user.id}"><b>{_esc(user.first_name)}</b></a>'

def _render(word: str, revealed: list[bool]) -> str:
    """Build display string like  F _ R E  from word + revealed mask."""
    return "  ".join(ch if revealed[i] else "_" for i, ch in enumerate(word))

def _make_revealed(word: str) -> list[bool]:
    """Initial mask: show ~40%, hide ~60%, always ≥1 shown & ≥1 hidden."""
    n       = len(word)
    show_n  = max(1, min(n - 1, round(n * random.uniform(0.35, 0.5))))
    show_p  = set(random.sample(range(n), show_n))
    return [i in show_p for i in range(n)]

def _hidden_positions(revealed: list[bool]) -> list[int]:
    return [i for i, v in enumerate(revealed) if not v]

def _game_kb(user_id: int, no_hints_left: bool = False) -> InlineKeyboardMarkup:
    hint_label = "💡 Hint  (–40 🌸)" if not no_hints_left else "💡 No hidden letters left"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(hint_label, callback_data=f"wgh:{user_id}"),
    ]])

def _start_kb(user_id: int) -> InlineKeyboardMarkup:
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
        g        = _games[key]
        rendered = _render(g["word"], g["revealed"])
        hidden   = _hidden_positions(g["revealed"])
        return await message.reply_text(
            f"🎮 {_mention(user)}, you already have an active round!\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔤 Your word: <code>{rendered}</code>\n"
            f"⏳ <i>Type it in chat to guess!</i>",
            parse_mode=HTML,
            reply_markup=_game_kb(user.id, no_hints_left=len(hidden) == 0),
        )

    await message.reply_text(
        f"🌸 <b>WORD GUESS</b> — {_mention(user)}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Choose your word length!\n"
        f"⏳ <b>15 seconds</b> to guess  •  💡 Hint costs <b>40 🌸</b>\n"
        f"💰 Correct = <b>50–100 🌸 kakera</b> reward!",
        parse_mode=HTML,
        reply_markup=_start_kb(user.id),
    )


# ── Callback: length selection ────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^wg:\d+:"))
async def wg_start_cb(client, cb: CallbackQuery):
    parts   = cb.data.split(":")
    owner   = int(parts[1])
    action  = parts[2]
    chat_id = cb.message.chat.id

    if cb.from_user.id != owner:
        return await cb.answer("❌ This isn't your game!", show_alert=True)

    if action == "cancel":
        await cb.message.delete()
        return await cb.answer("Cancelled.", show_alert=False)

    if action not in ("4", "5"):
        return await cb.answer("Invalid.", show_alert=True)

    key = (chat_id, owner)
    if key in _games:
        return await cb.answer("⚠️ You already have an active round!", show_alert=True)

    length   = int(action)
    word     = random.choice(_W4 if length == 4 else _W5)
    revealed = _make_revealed(word)
    rendered = _render(word, revealed)
    hidden   = _hidden_positions(revealed)
    vis      = length - len(hidden)

    await cb.message.edit_text(
        f"🎮 <b>YOUR WORD — {length} Letters</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔤 <code>{rendered}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>{vis}/{length}</b> letters visible\n"
        f"⏳ <b>15 seconds</b> — just type it in chat!\n"
        f"💡 Need help? Tap Hint below <i>(costs 40 🌸)</i>",
        parse_mode=HTML,
        reply_markup=_game_kb(owner),
    )
    await cb.answer(f"Your {length}-letter word is ready! Type it in chat!", show_alert=False)

    _games[key] = {
        "word":       word,
        "revealed":   revealed,
        "length":     length,
        "hints_used": 0,
        "msg":        cb.message,
        "task":       None,
    }
    task = asyncio.create_task(_timeout(client, chat_id, owner, word, cb.from_user))
    _games[key]["task"] = task


# ── Callback: hint button ─────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^wgh:\d+$"))
async def wg_hint_cb(client, cb: CallbackQuery):
    owner   = int(cb.data.split(":")[1])
    chat_id = cb.message.chat.id

    if cb.from_user.id != owner:
        return await cb.answer("❌ Not your game!", show_alert=True)

    key = (chat_id, owner)
    if key not in _games:
        return await cb.answer("⚠️ No active round found. Use /wguess to start!", show_alert=True)

    game   = _games[key]
    hidden = _hidden_positions(game["revealed"])

    if not hidden:
        return await cb.answer("No hidden letters left to reveal!", show_alert=True)

    # Check balance
    await get_or_create_user(
        owner, cb.from_user.username or "",
        cb.from_user.first_name or "", getattr(cb.from_user, "last_name", "") or "",
    )
    bal = await get_balance(owner)
    if bal < HINT_COST:
        return await cb.answer(
            f"❌ Not enough kakera! You need {HINT_COST} 🌸 but have {bal} 🌸.",
            show_alert=True
        )

    # Deduct and reveal one random hidden letter
    await deduct_balance(owner, HINT_COST)
    game["hints_used"] += 1

    reveal_pos = random.choice(hidden)
    game["revealed"][reveal_pos] = True

    hidden_left = _hidden_positions(game["revealed"])
    rendered    = _render(game["word"], game["revealed"])
    vis         = game["length"] - len(hidden_left)

    try:
        await cb.message.edit_text(
            f"🎮 <b>YOUR WORD — {game['length']} Letters</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔤 <code>{rendered}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>{vis}/{game['length']}</b> letters visible\n"
            f"💡 Hint used! <b>–{HINT_COST} 🌸</b>  •  Hints: <b>{game['hints_used']}</b>\n"
            f"⏳ Keep guessing — type it in chat!",
            parse_mode=HTML,
            reply_markup=_game_kb(owner, no_hints_left=len(hidden_left) == 0),
        )
    except Exception:
        pass

    new_bal = bal - HINT_COST
    await cb.answer(
        f"💡 Letter revealed! –{HINT_COST} 🌸  (Balance: {new_bal} 🌸)",
        show_alert=False
    )


# ── Message listener ──────────────────────────────────────────────────────────

async def wg_listener(_, message: Message):
    """Catch guesses — registered via add_handler at module bottom for reliable priority."""
    if not _games:
        return

    if not message.from_user or not message.text:
        return

    user    = message.from_user
    chat_id = message.chat.id
    key     = (chat_id, user.id)

    if key not in _games:
        return

    # Strip to letters only — "fire!", "FIRE", " fire " all match correctly
    guess = "".join(c for c in message.text.strip().upper() if c in string.ascii_uppercase)
    game  = _games[key]

    if guess != game["word"]:
        return

    # ── CORRECT ──────────────────────────────────────────────────────────────
    reward = random.randint(REWARD_MIN, REWARD_MAX)
    hints  = game["hints_used"]

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

    hint_note = f"  •  💡 Hints used: <b>{hints}</b>" if hints else ""
    await message.reply_text(
        f"🎉 <b>CORRECT!</b> — {_mention(user)}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ The word was: <code>{game['word']}</code>\n"
        f"💰 <b>+{reward} 🌸 kakera</b> added!{hint_note}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Use /wguess to play again!</i>",
        parse_mode=HTML,
    )


# ── Per-user 15s timeout ──────────────────────────────────────────────────────

async def _timeout(client, chat_id: int, user_id: int, word: str, user):
    try:
        await asyncio.sleep(TIMEOUT_SEC)
    except asyncio.CancelledError:
        return

    key = (chat_id, user_id)
    if key not in _games:
        return

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
        log.error("wguess timeout error uid=%s: %s", user_id, e)


# Register guess listener with group=-1 so it fires before spawn's group=0 counter
app.add_handler(MessageHandler(wg_listener, filters.group & filters.text), group=-1)
