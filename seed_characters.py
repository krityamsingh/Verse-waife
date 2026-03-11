"""
seed_characters.py
──────────────────────────────────────────────────────────────────────
Optional one-time script to populate the database with sample characters.
Run: python seed_characters.py

Edit the SAMPLE_CHARS list to add your actual characters before running.
"""

import asyncio
from SoulCatcher.database import init_db, insert_character, count_characters

SAMPLE_CHARS = [
    # (name,          anime,              rarity_name,       img_url)
    ("Naruto Uzumaki",    "Naruto",           "common",     "https://files.catbox.moe/sample1.jpg"),
    ("Sakura Haruno",     "Naruto",           "common",     "https://files.catbox.moe/sample2.jpg"),
    ("Itachi Uchiha",     "Naruto",           "rare",       "https://files.catbox.moe/sample3.jpg"),
    ("Goku",              "Dragon Ball Z",    "cosmos",     "https://files.catbox.moe/sample4.jpg"),
    ("Vegeta",            "Dragon Ball Z",    "infernal",   "https://files.catbox.moe/sample5.jpg"),
    ("Rem",               "Re:Zero",          "crystal",    "https://files.catbox.moe/sample6.jpg"),
    ("Zero Two",          "Darling in FranXX","mythic",     "https://files.catbox.moe/sample7.jpg"),
    ("Saitama",           "One Punch Man",    "eternal",    ""),  # set video_url instead
]

async def main():
    await init_db()
    existing = await count_characters()
    print(f"Current char count: {existing}")

    for name, anime, rarity, img_url in SAMPLE_CHARS:
        doc = {
            "name":      name,
            "anime":     anime,
            "rarity":    rarity,
            "img_url":   img_url,
            "video_url": "",
            "added_by":  0,
            "mention":   "seed_script",
        }
        cid = await insert_character(doc)
        print(f"  ✅ [{cid}] {name} ({rarity})")

    total = await count_characters()
    print(f"\nTotal characters now: {total}")

if __name__ == "__main__":
    asyncio.run(main())
