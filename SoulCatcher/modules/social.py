"""SoulCatcher/modules/social.py — /marry /propose /epropose /basket"""
import asyncio, random, time
from datetime import datetime, timedelta
from pyrogram import enums, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from .. import app
from ..database import (
    get_or_create_user, get_user, add_balance, deduct_balance,
    get_balance, add_to_harem, get_random_character, add_xp,
)
from ..rarity import ECONOMY

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  RARITY POOL
#  Rarity names must match whatever strings your DB uses.
#  Adjust names to match your schema (e.g. "legendary", "divine", etc.).
#  The weights here implement:
#    • common        → 60 %
#    • uncommon      → 20 %
#    • rare          → 12 %
#    • epic          →  5 %
#    • legendary     →  2 %   ← rarity tier 5
#    • cosmos        →  1 %   ← rarity tier 6
#    • divine        →  0.5 % ← rarity tier 7
#  Tiers 5-7 total ≈ 3.5 %, well within the requested 1-2 % each.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RARITY_POOL: list[tuple[str, float]] = [
    ("common",    60.0),
    ("uncommon",  20.0),
    ("rare",      12.0),
    ("epic",       5.0),
    ("legendary",  2.0),   # tier 5 – ~2 %
    ("cosmos",     1.0),   # tier 6 – ~1 %
    ("divine",     0.5),   # tier 7 – ~0.5 %
]
_RARITY_NAMES  = [r for r, _ in RARITY_POOL]
_RARITY_WEIGHTS = [w for _, w in RARITY_POOL]

# Fallback order when the weighted pick returns nothing from DB
_RARITY_FALLBACK = ["common", "uncommon", "rare", "epic",
                    "legendary", "cosmos", "divine"]


async def _pick_character_weighted() -> dict | None:
    """Pick a character using the weighted rarity pool; fall back gracefully."""
    order = random.choices(_RARITY_NAMES, weights=_RARITY_WEIGHTS, k=len(_RARITY_NAMES))
    seen  = set()
    for rarity in order:
        if rarity in seen:
            continue
        seen.add(rarity)
        char = await get_random_character(rarity)
        if char:
            return char
    # Last-resort sweep
    for rarity in _RARITY_FALLBACK:
        char = await get_random_character(rarity)
        if char:
            return char
    return None


async def _marry_char() -> dict | None:
    """For /marry: also uses weighted pool but biased toward common/rare."""
    for rarity in ["rare", "cosmos", "common", "uncommon", "epic"]:
        char = await get_random_character(rarity)
        if char:
            return char
    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _delete_after(msg, delay: int = 600):
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except Exception:
        pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /marry
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_marry_cds:     dict[int, float] = {}
_marry_streaks: dict[int, int]   = {}

MARRY_SUCCESS = [
    "**{mention}** and **{name}** from *{anime}* are now together under the stars! 💞",
    "**{mention}** got a yes from **{name}**! True love found! 🌸",
    "**{mention}** and **{name}** walked into the sunset together. Love wins! 🌅",
    "A shooting star — **{name}** from *{anime}* said yes to **{mention}**! 💖",
]
MARRY_FAIL = [
    "**{name}** from *{anime}* turned **{mention}** down gently... 💔",
    "**{mention}**, **{name}** sees you more as a friend. 🙃",
    "**{name}** smiled and walked away from **{mention}**... 😢",
]
STREAK_MSGS = {
    5:  "🔥 **5 marriages!** You're becoming a romance legend!",
    10: "💫 **10 streak!** The anime world loves you!",
    20: "👑 **20 streak!** You are the ultimate soul collector!",
}


@app.on_message(filters.command("marry"))
async def cmd_marry(_, message: Message):
    user      = message.from_user
    uid       = user.id
    now       = time.time()
    remaining = 60 - (now - _marry_cds.get(uid, 0))

    if remaining > 0:
        m = await message.reply_text(
            f"⏳ Wait **{int(remaining)}s** before proposing again!"
        )
        asyncio.create_task(_delete_after(message))
        asyncio.create_task(_delete_after(m))
        return

    _marry_cds[uid] = now
    roll = random.randint(1, 6)

    if roll in [1, 3, 6]:
        char = await _marry_char()
        if not char:
            m = await message.reply_text("🌌 No eligible characters. Try later!")
            asyncio.create_task(_delete_after(message))
            asyncio.create_task(_delete_after(m))
            return

        await add_to_harem(uid, char)
        from ..database import update_user
        await update_user(uid, {"$inc": {"marriage_count": 1, "total_married": 1}})

        _marry_streaks[uid] = _marry_streaks.get(uid, 0) + 1
        streak  = _marry_streaks[uid]
        caption = random.choice(MARRY_SUCCESS).format(
            mention=user.mention,
            name=char["name"],
            anime=char.get("anime", "?"),
        )

        pm = (
            await message.reply_photo(char["img_url"], caption=caption)
            if char.get("img_url")
            else await message.reply_text(caption)
        )
        asyncio.create_task(_delete_after(pm))
        asyncio.create_task(_delete_after(message))

        if streak in STREAK_MSGS:
            sm = await message.reply_text(f"{user.mention} {STREAK_MSGS[streak]}")
            asyncio.create_task(_delete_after(sm))
    else:
        _marry_streaks[uid] = 0
        char = await _marry_char()
        fail = random.choice(MARRY_FAIL).format(
            mention=user.mention,
            name=char["name"]         if char else "?",
            anime=char.get("anime","?") if char else "?",
        )
        fm = await message.reply_text(fail)
        asyncio.create_task(_delete_after(fm))
        asyncio.create_task(_delete_after(message))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /propose  (guaranteed on 4th attempt, weighted rarity pool)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_propose_cds:      dict[int, datetime] = {}
_propose_attempts: dict[int, dict]     = {}
_active_proposals: dict[int, dict]     = {}

PROPOSE_CD      = timedelta(minutes=5)
PROPOSE_GUARANTEE = 4          # ← guaranteed after this many failed attempts

LOVE_SUCCESS = [
    "✨ **{name} blushed deeply...** *\"I've been waiting for you\"* ❤️",
    "💫 **{name}'s eyes sparkled...** *\"I accept your heart\"* 💞",
    "🌸 **Petals swirled as {name} whispered...** *\"Yes, forever\"* 💍",
    "🌠 **{name} kissed your cheek...** *\"My answer is yes\"* 💘",
]
LOVE_FAIL = [
    "🍂 **{name} looked away...** *\"My heart belongs to another\"* 💔",
    "🌧️ **{name} shook their head...** *\"Not this time\"* ☔",
    "❄️ **\"You deserve better\"** {name} said before disappearing... 🌨️",
]


@app.on_message(filters.command("propose"))
async def cmd_propose(_, message: Message):
    user = message.from_user
    uid  = user.id
    now  = datetime.now()

    if uid in _active_proposals:
        return await message.reply_text("🌹 **Finish your current encounter first!**")

    if uid in _propose_cds:
        rem = PROPOSE_CD - (now - _propose_cds[uid])
        if rem.total_seconds() > 0:
            m, s = divmod(int(rem.total_seconds()), 60)
            return await message.reply_text(
                f"⏳ **Rest your heart...** `{m}m {s}s`",
                parse_mode=enums.ParseMode.MARKDOWN,
            )

    # Weighted rarity pick
    char = await _pick_character_weighted()
    if not char:
        return await message.reply_text("🌌 No candidates found. Try later!")

    _active_proposals[uid] = char

    # Track daily attempts
    _propose_attempts.setdefault(uid, {"date": now.date(), "count": 0})
    if _propose_attempts[uid]["date"] != now.date():
        _propose_attempts[uid] = {"date": now.date(), "count": 0}

    attempts_left = PROPOSE_GUARANTEE - _propose_attempts[uid]["count"]
    footer = (
        f"\n\n⚡ *Guaranteed in {attempts_left} attempt(s)!*"
        if attempts_left <= 2
        else ""
    )

    caption = (
        f"🌠 **A Fateful Encounter...**\n\n"
        f"💖 **{char['name']}** stands before you\n"
        f"_{char.get('anime', 'Unknown')}_"
        f"\n\n**Will you confess?**{footer}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💌 Confess",     callback_data=f"propose:yes:{uid}")],
        [InlineKeyboardButton("🌌 Walk Away",   callback_data=f"propose:no:{uid}")],
    ])

    if char.get("img_url"):
        await message.reply_photo(char["img_url"], caption=caption, reply_markup=kb)
    else:
        await message.reply_text(caption, reply_markup=kb)


@app.on_callback_query(filters.regex(r"^propose:"))
async def propose_cb(_, cb):
    _, action, uid_str = cb.data.split(":")
    uid = int(uid_str)

    if cb.from_user.id != uid:
        return await cb.answer("🔞 Not your encounter!", show_alert=True)

    char = _active_proposals.pop(uid, None)
    if not char:
        return await cb.message.edit_caption("⏳ The moment has passed...")

    _propose_cds[uid] = datetime.now()

    if action == "no":
        try:
            await cb.message.delete()
        except Exception:
            pass
        return await cb.message.reply_text("🌫️ You walked away silently...")

    # ── resolve outcome ───────────────────────────────────────────────────────
    _propose_attempts[uid]["count"] += 1
    attempts   = _propose_attempts[uid]["count"]
    guaranteed = attempts >= PROPOSE_GUARANTEE
    outcome    = "yes" if guaranteed else random.choices(
        ["yes", "no"], weights=[65, 35]
    )[0]
    name = char.get("name", "?")

    if outcome == "yes":
        await add_to_harem(uid, char)
        _propose_attempts[uid]["count"] = 0      # reset streak on success
        resp = random.choice(LOVE_SUCCESS).format(name=name)
        resp += f"\n\n💞 **{name} added to your collection!**"
    else:
        remaining_needed = PROPOSE_GUARANTEE - attempts
        resp  = random.choice(LOVE_FAIL).format(name=name)
        resp += (
            f"\n\n💫 *Guaranteed in **{remaining_needed}** more attempt(s)!*"
            if remaining_needed > 0
            else "\n\n💫 *Try once more — it's guaranteed!*"
        )

    try:
        await cb.message.edit_caption(resp, reply_markup=None)
    except Exception:
        pass


@app.on_message(filters.command("epropose"))
async def cmd_epropose(_, message: Message):
    if _active_proposals.pop(message.from_user.id, None):
        await message.reply_text("🌪️ Encounter cancelled.")
    else:
        await message.reply_text("🌌 No active encounter.")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /basket  (bet deducted FIRST; winnings/partial-refunds applied after)
#
#  Dice value outcomes:
#   6        → SUPER DUNK   → net +200 % of bet  (lose bet, gain bet×3)
#   4 or 5   → NICE SHOT    → net +50 % of bet   (lose bet, gain bet×1.5)
#   2 or 3   → CLOSE MISS   → net -50 % of bet   (lose bet, refund bet×0.5)
#   1        → AIRBALL      → net -100 % of bet  (lose full bet, no refund)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_basket_cds: dict[int, float] = {}


@app.on_message(filters.command(["basket", "basketball"]))
async def cmd_basket(client, message: Message):
    uid = message.from_user.id
    now = time.time()

    # ── cooldown check ────────────────────────────────────────────────────────
    last = _basket_cds.get(uid)
    if last and now - last < ECONOMY["basket_cooldown"]:
        wait = int(ECONOMY["basket_cooldown"] - (now - last))
        return await message.reply_text(
            f"⏳ **Too fast!** Wait `{wait}s`・o・",
            parse_mode=enums.ParseMode.MARKDOWN,
        )

    # ── parse bet ─────────────────────────────────────────────────────────────
    try:
        bet = int(message.command[1])
        if bet <= 0:
            raise ValueError
    except (IndexError, ValueError):
        return await message.reply_text(
            "❌ Use: `/basket <amount>`",
            parse_mode=enums.ParseMode.MARKDOWN,
        )

    # ── balance checks ────────────────────────────────────────────────────────
    balance = await get_balance(uid)
    if balance is None:
        return await message.reply_text("⚠️ Use /start first.")

    min_bet = max(50, int(balance * ECONOMY["basket_min_bet_pct"]))
    if bet < min_bet:
        return await message.reply_text(
            f"💢 Min bet: `{min_bet}` coins",
            parse_mode=enums.ParseMode.MARKDOWN,
        )
    if bet > balance:
        return await message.reply_text("💸 Not enough coins!")

    # ── deduct bet BEFORE rolling ─────────────────────────────────────────────
    deducted = await deduct_balance(uid, bet)
    if not deducted:
        return await message.reply_text("💸 Could not deduct coins. Try again!")

    # ── roll dice ─────────────────────────────────────────────────────────────
    dice           = await client.send_dice(message.chat.id, "🏀")
    val            = dice.dice.value
    _basket_cds[uid] = now

    # ── resolve outcome ───────────────────────────────────────────────────────
    if val == 6:
        # Win 2× the bet on top of getting it back → add bet × 3
        refund = bet * 3
        await add_balance(uid, refund)
        await add_xp(uid, 5)
        net = bet * 2
        await message.reply_text(
            f"✨ **SUPER SLAM DUNK!!**\n"
            f"╰┈➤ 🏆 +`{net:,}` coins\n"
            f"╰┈➤ 🌟 +5 xp\n\n"
            f"Legendary! (•̀ᴗ•́)و",
            parse_mode=enums.ParseMode.MARKDOWN,
        )

    elif val in [4, 5]:
        # Win 50 % of bet on top of getting it back → add bet × 1.5
        refund = int(bet * 1.5)
        await add_balance(uid, refund)
        await add_xp(uid, 3)
        net = refund - bet          # = int(bet * 0.5) profit
        await message.reply_text(
            f"🎯 **Nice Shot!**\n"
            f"╰┈➤ 💰 +`{net:,}` coins\n"
            f"╰┈➤ ✨ +3 xp\n\n"
            f"Keep going! ٩(◕‿◕｡)۶",
            parse_mode=enums.ParseMode.MARKDOWN,
        )

    elif val in [2, 3]:
        # Partial refund of 50 % → add back half the bet (net -50 %)
        refund = int(bet * 0.5)
        await add_balance(uid, refund)
        await add_xp(uid, -2)
        net_loss = bet - refund
        await message.reply_text(
            f"💢 **Close Miss!**\n"
            f"╰┈➤ 🩹 -`{net_loss:,}` coins\n"
            f"╰┈➤ 📉 -2 xp\n\n"
            f"Next time! (╥﹏╥)",
            parse_mode=enums.ParseMode.MARKDOWN,
        )

    else:  # val == 1 – AIRBALL, full loss (bet already deducted, no refund)
        await add_xp(uid, -3)
        await message.reply_text(
            f"💀 **AIRBALL!**\n"
            f"╰┈➤ ☠️ -`{bet:,}` coins\n"
            f"╰┈➤ ❌ -3 xp\n\n"
            f"Disaster lol (≧﹏≦)",
            parse_mode=enums.ParseMode.MARKDOWN,
        )
