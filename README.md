# 🌸 SoulCatcher — Anime Character Collecting Bot

> Collect, trade, and dominate the leaderboard with anime souls!

---

## ✨ Rarity System (7 Tiers + 3 Sub-Rarities)

| ID | Tier | Emoji | Sub-Rarity | Notes |
|----|------|-------|------------|-------|
| 1  | Common | ⚫ | — | Unlimited drops |
| 2  | Rare | 🔵 | — | No daily limit |
| 3  | Cosmos | 🌌 | — | 30/day, wishlist pings |
| 4  | Infernal | 🔥 | — | Announced, active group needed |
| 5  | Crystal | 💎 | 🌸 Seasonal (ID 51) | Max 5/user |
| 6  | Mythic | 🔴 | 🔮 Limited Edition (ID 61) | Not tradeable, max 3 |
| 7  | Eternal | ✨ | 🎠 Cartoon (ID 71) | VIDEO ONLY · max 1 |

> Sub-rarities have a **28% upgrade chance** when the parent tier rolls.  
> Tiers 6, 7 and their subs **cannot be traded or gifted**.  
> Tier 7 Eternal → 🎠 Cartoon = **rarest drop in the game** (video characters only).

---

## 🚀 Heroku Deployment

### One-Click
[![Deploy](https://www.herokucdn.com/deploy/button.svg)](https://heroku.com/deploy?template=https://github.com/youruser/SoulCatcher)

### Manual
```bash
git clone https://github.com/youruser/SoulCatcher
cd SoulCatcher
heroku create soulcatcher
heroku config:set API_ID=xxx API_HASH=xxx BOT_TOKEN=xxx MONGO_URI=xxx OWNER_IDS=xxx
git push heroku main
heroku ps:scale worker=1
```

---

## ⚙️ Config Vars (set in Heroku or .env)

| Var | Required | Description |
|-----|----------|-------------|
| `API_ID` | ✅ | Telegram API ID (my.telegram.org) |
| `API_HASH` | ✅ | Telegram API Hash |
| `BOT_TOKEN` | ✅ | From @BotFather |
| `MONGO_URI` | ✅ | MongoDB Atlas connection string |
| `OWNER_IDS` | ✅ | Your Telegram user ID(s), comma-separated |
| `LOG_CHANNEL_ID` | — | Channel for /start + join logs |
| `UPLOAD_CHANNEL_ID` | — | Where uploaded characters post |
| `UPLOAD_GC_ID` | — | Upload discussion group |
| `SUPPORT_GROUP` | — | Support group username |
| `UPDATE_CHANNEL` | — | Update channel username |
| `GIT_REPO_URL` | — | Full https://token@github.com/... URL for /gitpull |

---

## 📋 Commands

### 👥 Everyone
| Command | Description |
|---------|-------------|
| `/start` | Welcome + register (DM = rich, GC = short) |
| `/drop` | Force a character spawn |
| `/daily` | Claim daily kakera (streak bonuses!) |
| `/spin` | Spin wheel for kakera (1h cooldown) |
| `/pay <amt>` | Pay kakera to a user (reply) |
| `/harem` | Browse your collection |
| `/view <ID>` | View character card |
| `/burn <ID>` | Sell character for kakera |
| `/setfav <ID>` | Mark favourite ⭐ |
| `/sort <rarity\|name\|anime\|recent>` | Sort harem |
| `/wish <charID>` | Add to wishlist |
| `/wishlist` | View wishlist |
| `/unwish <charID>` | Remove from wishlist |
| `/trade <myID> <theirID>` | Propose a trade (reply) |
| `/gift <ID>` | Gift character (reply) |
| `/market [rarity]` | Browse market listings |
| `/sell <ID> <price>` | List on market |
| `/buy <listingID>` | Buy from market |
| `/marry` | Marry a random character |
| `/propose` | Propose to a character (3rd = guaranteed!) |
| `/epropose` | Cancel active proposal |
| `/basket <bet>` | 🏀 Basketball dice betting |
| `/status` | Full stats card |
| `/bal` | Balance check |
| `/profile` | Profile with photo |
| `/rank` | Your global rank |
| `/top` | Top 10 collectors |
| `/toprarity <name>` | Top collectors by rarity |
| `/richest` | Richest 10 players |
| `/rarityinfo` | Full rarity table |
| `/event` | Current game mode |

### 🔐 Sudo / Dev / Admin
| Command | Level | Description |
|---------|-------|-------------|
| `/addsudo` `/rmsudo` | Dev | Manage sudo users |
| `/adddev` `/rmdev` | Owner | Manage developers |
| `/adduploader` `/rmuploader` | Dev | Manage uploaders |
| `/sudolist` `/devlist` `/uploaderlist` | Sudo | View lists |
| `/gban` `/ungban` | Sudo | Global ban/unban |
| `/gmute` `/ungmute` | Sudo | Global mute/unmute |
| `/broadcast` | Dev | Broadcast + pin to all chats |
| `/transfer <from> <to>` | Sudo | Admin transfer all assets |
| `/addchar` | Sudo | Add character manually |
| `/delchar <id>` | Sudo | Disable character |
| `/setmode <mode>` | Sudo | Set game mode (normal/happy_hour/event/night/blitz) |
| `/forcedrop` | Sudo | Force spawn in current group |
| `/ban` `/unban` | Sudo | Ban/unban user from bot |
| `/eval` / `/shell` | Dev | Execute Python / shell |
| `/gitpull` | Dev | Pull latest code + restart |

### 📤 Uploader
| Command | Description |
|---------|-------------|
| `/upload anime \| name \| rarity_id` | Upload a character (reply to photo/video) |
| `/il <rarity_id>` | Auto-detect name & anime from spawn caption |
| `/uchar media <id>` | Update character media |
| `/uchar rarity <id> <rarity_id>` | Change rarity |
| `/uchar name <id> <new_name>` | Rename character |
| `/uchar anime <id> <new_anime>` | Update anime |

---

## 🗂️ Project Structure

```
SoulCatcher/
├── bot.py                     ← Entry point
├── requirements.txt
├── Procfile
├── runtime.txt
├── app.json                   ← Heroku one-click
├── .env.example
├── seed_characters.py         ← Optional DB seed script
└── SoulCatcher/
    ├── __init__.py            ← Pyrogram app + permission filters
    ├── config.py              ← All env vars
    ├── rarity.py              ← ★ 7-tier system, sub-rarities, all helpers
    ├── database.py            ← Full MongoDB async layer
    └── modules/
        ├── start.py           ← /start (DM + GC), help pages
        ├── spawn.py           ← Auto-spawn, /drop, claim
        ├── profile.py         ← /status /bal /profile /rank /top
        ├── economy.py         ← /daily /spin /pay
        ├── collection.py      ← /harem /trade /gift /market /wish
        ├── social.py          ← /marry /propose /basket
        ├── sudo.py            ← /addsudo /adddev /adduploader ...
        ├── admin.py           ← /gban /broadcast /transfer /eval /gitpull
        └── autouploader.py    ← /upload /il /uchar
```

---

## 🔧 Local Development

```bash
git clone https://github.com/youruser/SoulCatcher
cd SoulCatcher
pip install -r requirements.txt
cp .env.example .env
# Fill in .env values
python bot.py
```

---

## 📝 Editing the Rarity System

Everything lives in `SoulCatcher/rarity.py` — **no other file needs touching!**

- Add a new tier → add to `RARITIES` dict
- Add a sub-rarity → add to `SUB_RARITIES`, attach to parent's `.sub_rarities`
- Tweak drop rates, prices, claim windows → edit the `RarityTier` fields

---

*SoulCatcher 🌸 — Collect every soul in the anime universe.*
