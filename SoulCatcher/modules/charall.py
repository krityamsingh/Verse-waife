"""SoulCatcher/modules/charall.py

  /all   — full breakdown of uploaded characters by rarity + sub-rarity
"""

from __future__ import annotations
import logging

from pyrogram import filters
from pyrogram.types import Message

from .. import app
from ..database import _col
from ..rarity import RARITIES, SUB_RARITIES

log = logging.getLogger("SoulCatcher.charall")


def _fmt(n) -> str:
    return f"{int(n):,}"


# Ordered tier display: main rarities by weight desc, then their sub-rarities
TIER_ORDER = [
    # (rarity_key, is_sub, parent_key_or_None)
    ("common",          False, None),
    ("rare",            False, None),
    ("Legendry",        False, None),
    ("Elite",           False, None),
    ("seasonal",        False, None),
    ("festival",        True,  "seasonal"),
    ("mythic",          False, None),
    ("limited_edition", True,  "mythic"),
    ("sports",          True,  "mythic"),
    ("fantasy",         True,  "mythic"),
    ("eternal",         False, None),
    ("cartoon",         True,  "eternal"),   # Verse
]


@app.on_message(filters.command("all"))
async def cmd_charall(_, message: Message):
    wait = await message.reply_text("⏳ Loading character data...")

    # Pull counts per rarity from DB in one aggregation
    pipeline = [
        {"$match": {"enabled": True}},
        {"$group": {"_id": "$rarity", "count": {"$sum": 1}}},
    ]
    rows = await _col("characters").aggregate(pipeline).to_list(50)
    counts: dict[str, int] = {r["_id"]: r["count"] for r in rows}

    total_enabled  = sum(counts.values())
    total_disabled = await _col("characters").count_documents({"enabled": False})
    grand_total    = total_enabled + total_disabled

    lines = [
        "〔 📚  ᴄʜᴀʀᴀᴄᴛᴇʀ  ʟɪʙʀᴀʀʏ  〕\n",
        f"📦  Total uploaded: **{_fmt(grand_total)}**  "
        f"( ✅ {_fmt(total_enabled)} active  ·  🚫 {_fmt(total_disabled)} disabled )\n",
        "╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌\n",
    ]

    all_rarities = {**RARITIES, **SUB_RARITIES}

    for rarity_key, is_sub, parent in TIER_ORDER:
        tier = all_rarities.get(rarity_key)
        if not tier:
            continue

        count = counts.get(rarity_key, 0)
        indent = "   └ " if is_sub else ""
        label  = f"_{tier.display_name}_" if is_sub else f"**{tier.display_name}**"
        bar    = f"`{_fmt(count)}`"

        if is_sub:
            lines.append(f"{indent}{tier.emoji} {label}  —  {bar}")
        else:
            lines.append(f"\n{tier.emoji} {label}  —  {bar}")

    lines.append("\n╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌")

    text = "\n".join(lines)

    try:
        await wait.edit_text(text)
    except Exception:
        await message.reply_text(text)
