"""SoulCatcher/modules/sudo.py — /addsudo /rmsudo /adddev /rmdev /adduploader /rmuploader /sudolist /devlist /uploaderlist"""
import logging
from datetime import datetime

from pyrogram import enums, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, Message

from .. import app, dev_filter, sudo_filter, capsify
from ..config import OWNER_IDS
from ..database import (
    add_sudo, remove_sudo, get_sudo_ids, is_sudo,
    add_dev, remove_dev, get_dev_ids, is_dev,
    add_uploader as _add_uploader, remove_uploader, get_uploader_ids, is_uploader,
)
from .. import refresh_sudo, refresh_dev, refresh_uploader

log = logging.getLogger("SoulCatcher.sudo")


# -----------------------------------------------------------------------------
#  Owner DM notification helper
# -----------------------------------------------------------------------------

async def _notify_owners(client, message, action: str,
                         target_id: int = None, target_name: str = None) -> None:
    """
    Silently DMs every owner when a sudo/dev user exercises a privileged action.
    Skips the DM if the actor is already an owner (no self-spam).

    Parameters
    ----------
    client      : Pyrogram client
    message     : the Message (or Message-like object) that triggered the command
    action      : short human-readable label  e.g.  "/addsudo — granted sudo"
    target_id   : Telegram ID of the user the action was performed ON (optional)
    target_name : Display name of that target user (optional)
    """
    actor = message.from_user
    if actor.id in OWNER_IDS:
        return  # owner already knows what they did; no self-spam

    chat = message.chat
    chat_str = (
        f"[{chat.title}](tg://user?id={chat.id}) `{chat.id}`"
        if getattr(chat, "title", None)
        else f"Private chat `{chat.id}`"
    )
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    actor_mention = f"[{actor.first_name}](tg://user?id={actor.id})"
    if getattr(actor, "username", None):
        actor_mention += f" @{actor.username}"

    target_line = ""
    if target_id and target_name:
        target_line = (
            f"\n👤 **Target:** [{target_name}](tg://user?id={target_id}) `{target_id}`"
        )

    text = (
        f"🔔 **Sudo Power Used**\n\n"
        f"👮 **Actor:** {actor_mention} `{actor.id}`\n"
        f"⚡ **Action:** `{action}`"
        f"{target_line}\n"
        f"💬 **Chat:** {chat_str}\n"
        f"🕐 **Time:** `{ts}`"
    )

    for owner_id in OWNER_IDS:
        try:
            await client.send_message(
                owner_id, text,
                parse_mode=enums.ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
        except Exception as exc:
            log.warning("_notify_owners: could not DM owner %d — %s", owner_id, exc)


# -----------------------------------------------------------------------------
#  Resolve target user helper
# -----------------------------------------------------------------------------

async def _resolve(client, message: Message):
    if message.reply_to_message:
        u = message.reply_to_message.from_user
        return u.id, u.first_name
    try:
        uid = int(message.text.split()[1])
        u   = await client.get_users(uid)
        return u.id, u.first_name
    except Exception:
        return None, None


# -- SUDO ---------------------------------------------------------------------

@app.on_message(filters.command("addsudo") & dev_filter)
async def cmd_addsudo(client, message: Message):
    uid, name = await _resolve(client, message)
    if not uid:
        return await message.reply_text(capsify("🚧 **Reply to a user or provide a valid ID, senpai!** 🎀"))
    if uid in OWNER_IDS:
        return await message.reply_text(capsify("🎀 **That's the owner — they already have supreme powers!**"))
    if await is_sudo(uid):
        return await message.reply_text(
            capsify(f"☘️ **Ehh?** `{name}` is already sudo! 🫧"),
            parse_mode=enums.ParseMode.MARKDOWN,
        )
    await add_sudo(uid)
    refresh_sudo([uid])
    await message.reply_text(
        capsify(f"🚧 **Woohoo!** `{name}` now has **sudo powers**! ✨"),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🗑 Remove Again", callback_data=f"rmsudo:{uid}")]]),
    )
    await _notify_owners(client, message, "/addsudo — granted sudo", uid, name)


@app.on_callback_query(filters.regex(r"^rmsudo:"))
async def cb_rmsudo(client, cb: CallbackQuery):
    uid = int(cb.data.split(":")[1])
    await remove_sudo(uid)
    await cb.message.edit_text(capsify(f"🎀 `{uid}` is no longer sudo!"), parse_mode=enums.ParseMode.MARKDOWN)
    # notify owners when a non-owner uses the inline remove button
    if cb.from_user.id not in OWNER_IDS:
        class _FakeMsg:
            from_user = cb.from_user
            chat      = cb.message.chat
        await _notify_owners(client, _FakeMsg(), f"/rmsudo (inline button) — revoked sudo from `{uid}`", uid, str(uid))


@app.on_message(filters.command("rmsudo") & dev_filter)
async def cmd_rmsudo(client, message: Message):
    uid, name = await _resolve(client, message)
    if not uid:
        return await message.reply_text(capsify("🚧 Reply to a user or provide a valid ID! 🎀"))
    if not await is_sudo(uid):
        return await message.reply_text(
            capsify(f"🚧 **Ehh~?!** `{name}` is not even sudo, senpai! 🎀"),
            parse_mode=enums.ParseMode.MARKDOWN,
        )
    await remove_sudo(uid)
    await message.reply_text(capsify(f"🎀 **Bye-bye!** `{name}` is no longer sudo~! 🚧"), parse_mode=enums.ParseMode.MARKDOWN)
    await _notify_owners(client, message, "/rmsudo — revoked sudo", uid, name)


@app.on_message(filters.command("sudolist") & sudo_filter)
async def cmd_sudolist(client, message: Message):
    ids = await get_sudo_ids()
    if not ids:
        return await message.reply_text(capsify("🎀 **No sudo users yet, senpai~!**"))
    lines = []
    for uid in ids:
        try:
            u = await client.get_users(uid)
            lines.append(f"[{u.first_name}](tg://user?id={u.id}) `{u.id}`")
        except Exception:
            lines.append(f"Unknown `{uid}`")
    text = f"🚀 **Sudo Users ({len(ids)})**\n\n" + "\n".join(lines)
    await message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Close", callback_data=f"closelist:{message.from_user.id}")]]),
    )
    await _notify_owners(client, message, "/sudolist — viewed sudo list")


# -- DEV ----------------------------------------------------------------------

@app.on_message(filters.command("adddev") & (dev_filter | filters.user(tuple(OWNER_IDS))))
async def cmd_adddev(client, message: Message):
    uid, name = await _resolve(client, message)
    if not uid:
        return await message.reply_text(capsify("🚧 Reply to a user or provide valid ID! 🎀"))
    if await is_dev(uid):
        return await message.reply_text(
            capsify(f"🎀 **Oops!** `{name}` is already a dev! 🚧"),
            parse_mode=enums.ParseMode.MARKDOWN,
        )
    await add_dev(uid)
    refresh_dev([uid])
    await message.reply_text(capsify(f"🎀 **Congrats!** `{name}` is now a **Developer!** 🚧"), parse_mode=enums.ParseMode.MARKDOWN)
    await _notify_owners(client, message, "/adddev — granted dev", uid, name)


@app.on_message(filters.command("rmdev") & dev_filter)
async def cmd_rmdev(client, message: Message):
    uid, name = await _resolve(client, message)
    if not uid:
        return await message.reply_text(capsify("🚧 Invalid! 🎀"))
    if not await is_dev(uid):
        return await message.reply_text(
            capsify(f"🚧 `{name}` is not a dev! 🎀"),
            parse_mode=enums.ParseMode.MARKDOWN,
        )
    await remove_dev(uid)
    await message.reply_text(capsify(f"🚧 **Farewell!** `{name}` is no longer a dev! 🎀"), parse_mode=enums.ParseMode.MARKDOWN)
    await _notify_owners(client, message, "/rmdev — revoked dev", uid, name)


@app.on_message(filters.command("devlist") & dev_filter)
async def cmd_devlist(client, message: Message):
    ids = await get_dev_ids()
    if not ids:
        return await message.reply_text(capsify("🎀 **No devs found!**"))
    lines = []
    for uid in ids:
        try:
            u = await client.get_users(uid)
            lines.append(f"[{u.first_name}](tg://user?id={u.id}) `{u.id}`")
        except Exception:
            lines.append(f"Unknown `{uid}`")
    await message.reply_text(
        f"🛠 **Developers ({len(ids)})**\n\n" + "\n".join(lines),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Close", callback_data=f"closelist:{message.from_user.id}")]]),
    )
    await _notify_owners(client, message, "/devlist — viewed dev list")


# -- UPLOADER -----------------------------------------------------------------

@app.on_message(filters.command("adduploader") & dev_filter)
async def cmd_adduploader(client, message: Message):
    uid, name = await _resolve(client, message)
    if not uid:
        return await message.reply_text(capsify("🎀 Reply to user or give valid ID! 🚧"))
    if await is_uploader(uid):
        return await message.reply_text(
            capsify(f"🎀 `{name}` is already an uploader! 🚧"),
            parse_mode=enums.ParseMode.MARKDOWN,
        )
    await _add_uploader(uid)
    refresh_uploader([uid])
    await message.reply_text(capsify(f"🚧 **Congrats!** `{name}` is now an **Uploader!** 🎀"), parse_mode=enums.ParseMode.MARKDOWN)
    await _notify_owners(client, message, "/adduploader — granted uploader", uid, name)


@app.on_message(filters.command("rmuploader") & sudo_filter)
async def cmd_rmuploader(client, message: Message):
    uid, name = await _resolve(client, message)
    if not uid:
        return await message.reply_text(capsify("🚧 Invalid! 🎀"))
    if not await is_uploader(uid):
        return await message.reply_text(
            capsify(f"🎀 `{name}` is not an uploader! 🚧"),
            parse_mode=enums.ParseMode.MARKDOWN,
        )
    await remove_uploader(uid)
    await message.reply_text(capsify(f"🚧 **Done!** `{name}` is no longer an uploader! 🎀"), parse_mode=enums.ParseMode.MARKDOWN)
    await _notify_owners(client, message, "/rmuploader — revoked uploader", uid, name)


@app.on_message(filters.command("uploaderlist") & sudo_filter)
async def cmd_uploaderlist(client, message: Message):
    ids = await get_uploader_ids()
    if not ids:
        return await message.reply_text(capsify("🎀 **No uploaders yet!**"))
    lines = []
    for uid in ids:
        try:
            u = await client.get_users(uid)
            lines.append(f"[{u.first_name}](tg://user?id={u.id}) `{u.id}`")
        except Exception:
            lines.append(f"Unknown `{uid}`")
    await message.reply_text(
        f"📤 **Uploaders ({len(ids)})**\n\n" + "\n".join(lines),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Close", callback_data=f"closelist:{message.from_user.id}")]]),
    )
    await _notify_owners(client, message, "/uploaderlist — viewed uploader list")


# -- SHARED CLOSE -------------------------------------------------------------

@app.on_callback_query(filters.regex(r"^closelist:"))
async def cb_closelist(_, cb: CallbackQuery):
    uid = int(cb.data.split(":")[1])
    if cb.from_user.id != uid:
        return await cb.answer("🚨 This isn't for you, baka! ❗", show_alert=True)
    await cb.message.delete()
