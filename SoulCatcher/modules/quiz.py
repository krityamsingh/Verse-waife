"""SoulCatcher/modules/quiz.py — Anime character guessing quiz."""
from __future__ import annotations

import asyncio
import logging
import random

import SoulCatcher as _soul
from pyrogram import filters
from pyrogram.types import Message

from SoulCatcher.database import (
    get_or_create_user,
    get_active_quiz,
    create_quiz,
    end_quiz,
    delete_quiz,
    add_balance,
    add_xp,
    get_random_character,
)
from SoulCatcher.rarity import ECONOMY, roll_rarity

log = logging.getLogger("SoulCatcher.quiz")

QUIZ_TIMEOUT = 30  # seconds
_quiz_tasks: dict[int, asyncio.Task] = {}


async def _start_quiz_round(client, chat_id: int) -> None:
    # Pick a random character
    rarity = roll_rarity()
    char   = await get_random_character(rarity.name)
    if not char:
        try:
            await client.send_message(chat_id, "❌ No characters available for quiz.")
        except Exception:
            pass
        await delete_quiz(chat_id)
        return

    # Store in DB
    await delete_quiz(chat_id)
    await create_quiz({
        "chat_id":   chat_id,
        "char_id":   char["id"],
        "char_name": char["name"].lower(),
        "rarity":    rarity.name,
        "active":    True,
    })

    # Prepare hint (blank out last half)
    name      = char["name"]
    hint      = " ".join(
        w[:max(1, len(w) // 2)] + "?" * (len(w) - max(1, len(w) // 2))
        for w in name.split()
    )
    reward = ECONOMY["quiz_reward"]

    caption = (
        f"🎮 **Anime Quiz!** 🎮\n\n"
        f"Who is this character?\n"
        f"💡 Hint: **{hint}**\n\n"
        f"💰 Reward: `{reward}` kakera | ⏳ `{QUIZ_TIMEOUT}s`"
    )

    try:
        if char.get("img_url"):
            await client.send_photo(chat_id, char["img_url"], caption=caption)
        elif char.get("video_url"):
            await client.send_video(chat_id, char["video_url"], caption=caption)
        else:
            await client.send_message(chat_id, caption)
    except Exception as exc:
        log.warning("Quiz send error in %d: %s", chat_id, exc)

    # Timeout task
    task = asyncio.create_task(_quiz_timeout(client, chat_id, char["name"]))
    _quiz_tasks[chat_id] = task


async def _quiz_timeout(client, chat_id: int, char_name: str) -> None:
    await asyncio.sleep(QUIZ_TIMEOUT)
    quiz = await get_active_quiz(chat_id)
    if quiz:
        await end_quiz(chat_id)
        try:
            await client.send_message(
                chat_id,
                f"⌛ Time's up! The answer was **{char_name}**."
            )
        except Exception:
            pass


# ── /quiz ─────────────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command("quiz") & filters.group)
async def quiz_cmd(client, m: Message):
    chat_id = m.chat.id
    existing = await get_active_quiz(chat_id)
    if existing:
        await m.reply("❓ A quiz is already running! Guess the character.")
        return

    await m.reply("🎮 Starting quiz...")
    await _start_quiz_round(client, chat_id)


# ── Answer listener ───────────────────────────────────────────────────────────

@_soul.app.on_message(filters.group & filters.text & ~filters.command(""))
async def quiz_answer_listener(client, m: Message):
    if not m.from_user:
        return

    chat_id = m.chat.id
    quiz    = await get_active_quiz(chat_id)
    if not quiz:
        return

    guess     = m.text.strip().lower()
    char_name = quiz["char_name"]

    # Flexible match
    if guess == char_name or char_name in guess or guess in char_name:
        # Cancel timeout
        task = _quiz_tasks.pop(chat_id, None)
        if task:
            task.cancel()

        await end_quiz(chat_id)

        uid    = m.from_user.id
        u      = m.from_user
        await get_or_create_user(uid, u.username or "", u.first_name or "", u.last_name or "")

        reward = ECONOMY["quiz_reward"]
        await add_balance(uid, reward)
        await add_xp(uid, 20)

        await m.reply(
            f"🎉 **{u.first_name}** got it!\n"
            f"✅ **{quiz['char_name'].title()}**\n"
            f"💰 +{reward} kakera | ⭐ +20 XP"
        )


# ── /quizstop ─────────────────────────────────────────────────────────────────

@_soul.app.on_message(filters.command(["quizstop", "stopquiz"]) & filters.group & _soul.sudo_filter)
async def quiz_stop_cmd(_, m: Message):
    chat_id = m.chat.id
    task    = _quiz_tasks.pop(chat_id, None)
    if task:
        task.cancel()
    quiz = await get_active_quiz(chat_id)
    if quiz:
        await end_quiz(chat_id)
        await m.reply(f"🛑 Quiz stopped. Answer was: **{quiz['char_name'].title()}**")
    else:
        await m.reply("No active quiz.")
