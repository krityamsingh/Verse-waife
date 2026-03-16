"""
SoulCatcher/modules/burn.py
════════════════════════════════════════════════════════════════════
/burn <number>     burn that many characters from your harem
/burn <id>         burn one specific character by ID
/delh <user_id>    (owner only) delete a user's entire harem
════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
import logging
from html import escape

from pyrogram import filters, enums
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup as IKM,
    InlineKeyboardButton as IKB,
)

from .. import app
from ..rarity import get_rarity, get_kakera_reward
from ..database import _col, add_balance

log = logging.getLogger("SoulCatcher.burn")

OWNER_ID = 123456789   # ← replace with your Telegram user ID


# ═══════════════════════════════════════════════════════════════════════════════
# /burn <number>   or   /burn <id>
# ═══════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("burn"))
async def cmd_burn(_, message: Message):
    uid  = message.from_user.id
    args = message.command

    if len(args) < 2:
        return await message.reply_text(
            "🔥 <b>Burn Characters</b>\n\n"
            "By count : <code>/burn 200</code>  — burn 200 chars from your harem\n"
            "By ID    : <code>/burn A1B2</code>  — burn one specific character\n\n"
            "Oldest characters are burned first when using a count.",
            parse_mode=enums.ParseMode.HTML,
        )

    arg = args[1].strip()

    # ── /burn <number> ────────────────────────────────────────────────────────
    if arg.isdigit():
        count = int(arg)
        if count <= 0:
            return await message.reply_text("❌ Number must be greater than 0.")

        total_owned = await _col("user_characters").count_documents({"user_id": uid})
        if total_owned == 0:
            return await message.reply_text("❌ Your harem is empty.")

        if count > total_owned:
            return await message.reply_text(
                f"❌ You only have <b>{total_owned}</b> characters.",
                parse_mode=enums.ParseMode.HTML,
            )

        # Confirm before bulk burn
        markup = IKM([[
            IKB(f"🔥 Burn {count}", callback_data=f"burn_count:{uid}:{count}"),
            IKB("❌ Cancel",        callback_data=f"burn_cancel:{uid}"),
        ]])

        return await message.reply_text(
            f"🔥 <b>Burn {count} characters?</b>\n\n"
            f"Your harem has <b>{total_owned}</b> characters.\n"
            f"Oldest <b>{count}</b> will be burned first.\n\n"
            f"<b>This cannot be undone!</b>",
            reply_markup=markup,
            parse_mode=enums.ParseMode.HTML,
        )

    # ── /burn <id> ────────────────────────────────────────────────────────────
    cid  = arg
    char = await _col("user_characters").find_one({
        "user_id": uid,
        "$or": [{"instance_id": cid}, {"char_id": cid}],
    })

    if not char:
        return await message.reply_text(
            f"❌ No character with ID <code>{escape(cid)}</code> in your harem.",
            parse_mode=enums.ParseMode.HTML,
        )

    tier    = get_rarity(char.get("rarity") or "common")
    r_emoji = tier.emoji if tier else "❓"
    reward  = get_kakera_reward(char.get("rarity") or "common")
    iid     = char.get("instance_id") or cid

    markup = IKM([[
        IKB("🔥 Burn", callback_data=f"burn_one:{uid}:{iid}:{reward}"),
        IKB("❌ Cancel", callback_data=f"burn_cancel:{uid}"),
    ]])

    await message.reply_text(
        f"🔥 <b>Burn {r_emoji} {escape(char.get('name', '?'))}?</b>\n\n"
        f"Anime  : {escape(char.get('anime', '?'))}\n"
        f"Reward : <b>{reward} kakera</b>",
        reply_markup=markup,
        parse_mode=enums.ParseMode.HTML,
    )


# ── Confirm burn by count ─────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^burn_count:"))
async def burn_count_cb(_, cb):
    _, uid_s, count_s = cb.data.split(":")
    uid   = int(uid_s)
    count = int(count_s)

    if cb.from_user.id != uid:
        return await cb.answer("Not your action!", show_alert=True)

    # Fetch oldest 'count' characters (sorted by obtained_at ascending)
    chars = await _col("user_characters").find(
        {"user_id": uid}
    ).sort("obtained_at", 1).limit(count).to_list(count)

    if not chars:
        return await cb.answer("Nothing to burn.", show_alert=True)

    total_kakera = sum(get_kakera_reward(c.get("rarity") or "common") for c in chars)
    ids_to_delete = [c.get("instance_id") or c.get("char_id") for c in chars if c.get("instance_id") or c.get("char_id")]

    await _col("user_characters").delete_many({
        "user_id": uid,
        "instance_id": {"$in": ids_to_delete},
    })
    await add_balance(uid, total_kakera)

    await cb.answer(f"🔥 Burned {len(chars)}! +{total_kakera} kakera")
    try:
        await cb.message.edit_text(
            f"🔥 <b>Burned {len(chars)} characters!</b>\n\n"
            f"💰 <b>+{total_kakera} kakera</b> added to your balance.",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception:
        pass


# ── Confirm burn single by ID ─────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^burn_one:"))
async def burn_one_cb(_, cb):
    _, uid_s, iid, reward_s = cb.data.split(":")
    uid = int(uid_s)

    if cb.from_user.id != uid:
        return await cb.answer("Not your action!", show_alert=True)

    char = await _col("user_characters").find_one({"user_id": uid, "instance_id": iid})
    if not char:
        await cb.answer("Already gone.", show_alert=True)
        try:   await cb.message.delete()
        except Exception: pass
        return

    await _col("user_characters").delete_one({"user_id": uid, "instance_id": iid})
    await add_balance(uid, int(reward_s))

    tier    = get_rarity(char.get("rarity") or "common")
    r_emoji = tier.emoji if tier else "❓"

    await cb.answer(f"🔥 +{reward_s} kakera")
    try:
        await cb.message.edit_text(
            f"🔥 {r_emoji} <b>{escape(char.get('name', '?'))}</b> burned.\n"
            f"💰 <b>+{reward_s} kakera</b>",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception:
        pass


# ── Cancel ────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^burn_cancel:"))
async def burn_cancel_cb(_, cb):
    uid = int(cb.data.split(":")[1])
    if cb.from_user.id != uid:
        return await cb.answer("Not your action!", show_alert=True)
    await cb.answer("Cancelled.")
    try:   await cb.message.delete()
    except Exception: pass


# ═══════════════════════════════════════════════════════════════════════════════
# /delh <user_id>  — owner only
# ═══════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("delh"))
async def cmd_delh(_, message: Message):
    if message.from_user.id != OWNER_ID:
        return

    args = message.command
    if len(args) < 2:
        return await message.reply_text(
            "Usage: <code>/delh &lt;user_id&gt;</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    try:
        target_uid = int(args[1])
    except ValueError:
        return await message.reply_text("❌ Invalid user ID.")

    count = await _col("user_characters").count_documents({"user_id": target_uid})
    if count == 0:
        return await message.reply_text(
            f"❌ User <code>{target_uid}</code> has no characters.",
            parse_mode=enums.ParseMode.HTML,
        )

    await _col("user_characters").delete_many({"user_id": target_uid})

    user_doc = await _col("users").find_one({"user_id": target_uid})
    name     = user_doc.get("first_name", str(target_uid)) if user_doc else str(target_uid)

    await message.reply_text(
        f"✅ Harem of <b>{escape(name)}</b> (<code>{target_uid}</code>) deleted.\n"
        f"Removed <b>{count}</b> characters.",
        parse_mode=enums.ParseMode.HTML,
    )
    log.warning("Owner deleted harem of %d (%s) — %d chars", target_uid, name, count)
