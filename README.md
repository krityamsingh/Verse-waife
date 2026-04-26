# 🌸 SoulCatcher v2.0

A professional Telegram anime character collecting bot built with **Pyrogram** and **MongoDB**.

---

## ✨ Features

| Feature | Description |
|---|---|
| 🎴 **7 Rarity Tiers** | Common → Rare → Legendry → Elite → Seasonal → Mythic → ✨ Eternal |
| 🐉 **Dragon Ball Mini-Game** | Collect dragon balls, make wishes, battle other players |
| 🏪 **Stock Market** | List characters with stock counts & per-user limits |
| 🔄 **Trading** | Secure two-way character trades with confirmation |
| 💒 **Marriage System** | Propose, accept, divorce — with persistence checks |
| ⭐ **Wishlist** | 25-slot wishlist with spawn pings for rare characters |
| 📊 **Leaderboards** | 6 rotating boards (richest, collectors, level, married…) |
| 🎮 **Quiz Game** | Anime character guessing with kakera rewards |
| 🛡 **Global Ban/Mute** | Cross-group moderation system |
| ⚙️ **Group Settings** | Per-group spawn toggle, frequency, announcements |
| 📡 **Broadcast** | Message all users or all groups |
| 🔧 **Dev Tools** | Shell, reload, system info, game mode switching |

---

## 🚀 Quick Deploy

### Prerequisites
- Python 3.11+
- MongoDB Atlas account (free tier is fine)
- Telegram API credentials from [my.telegram.org](https://my.telegram.org/apps)
- A bot token from [@BotFather](https://t.me/BotFather)

### 1. Clone & Install

```bash
git clone <your-repo-url>
cd SoulCatcher-Pro
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
nano .env   # fill in your credentials
```

Required variables:
```
API_ID=      # from my.telegram.org
API_HASH=    # from my.telegram.org
BOT_TOKEN=   # from @BotFather
MONGO_URI=   # mongodb+srv://...
OWNER_IDS=   # your Telegram user ID
```

### 3. Run

```bash
python bot.py
```

---

## 🌐 Deploy to Railway / Heroku

1. Push your repo (without `.env`) to GitHub
2. Add environment variables in the dashboard
3. Set build command: `pip install -r requirements.txt`
4. Set start command: `python bot.py`
5. MongoDB Atlas: go to **Network Access → Add 0.0.0.0/0**

---

## 📁 Project Structure

```
SoulCatcher-Pro/
├── bot.py                          ← Entry point
├── requirements.txt
├── Procfile
├── .env.example
├── SoulCatcher/
│   ├── __init__.py                 ← Filters, caches, helpers
│   ├── config.py                   ← All settings from env vars
│   ├── database.py                 ← Full MongoDB data layer
│   ├── rarity.py                   ← All 7 rarity tiers + sub-rarities
│   └── modules/
│       ├── start.py                ← /start, /help, /about, /stats
│       ├── spawn.py                ← Character spawn & claim system
│       ├── profile.py              ← /profile, /daily, /spin, /pay, /level
│       ├── harem.py                ← /harem, /view, /burn, /setfav, /sort
│       ├── wishlist.py             ← /wish, /wishlist, /unwish
│       ├── trade.py                ← /trade, /gift
│       ├── marriage.py             ← /propose, /marry, /divorce, /couple
│       ├── market.py               ← /market, /list, /buy, /removelisting
│       ├── upload.py               ← /upload, /edit, /delete, /charinfo
│       ├── top.py                  ← /top, /topcollectors, /toprich
│       ├── quiz.py                 ← /quiz, /quizstop
│       ├── moderation.py           ← /gban, /gmute, /sudo, /dev, /uploader
│       ├── broadcast.py            ← /broadcast, /groupcast
│       ├── group_settings.py       ← /gsettings, /spawnon, /spawnfreq
│       ├── dragonball.py           ← Dragon Ball mini-game
│       └── dev.py                  ← /ping, /shell, /reload, /setmode
```

---

## 🎮 Commands

### 👤 User Commands

| Command | Description |
|---|---|
| `/start` | Welcome & quick links |
| `/help` | Full command list |
| `/profile` | View your stats |
| `/balance` | Check kakera |
| `/level` | XP & level progress |
| `/daily` | Claim daily reward (streak bonus!) |
| `/spin` | Spin for kakera (1h cooldown) |
| `/pay <amount>` | Pay someone (reply) |

### 🎴 Collection

| Command | Description |
|---|---|
| `/harem [page]` | Browse your characters |
| `/view <ID>` | View a character card |
| `/burn <ID>` | Sell for kakera |
| `/setfav <ID>` | Toggle favourite |
| `/sort <rarity\|name\|anime\|recent>` | Sort harem |
| `/note <ID> <text>` | Add a note |
| `/search <query>` | Search character database |

### 🌟 Wishlist

| Command | Description |
|---|---|
| `/wish <charID>` | Add to wishlist |
| `/wishlist` | View wishlist |
| `/unwish <charID>` | Remove from wishlist |

### 🔄 Social

| Command | Description |
|---|---|
| `/trade <myID> <theirID>` | Propose a trade (reply) |
| `/gift <ID>` | Gift a character (reply) |
| `/propose` | Propose marriage (reply) |
| `/marry` | Accept marriage (reply) |
| `/divorce` | End marriage |
| `/couple` | View marriage status |

### 🏪 Market

| Command | Description |
|---|---|
| `/market [rarity]` | Browse listings |
| `/list <ID> <price> [stock] [per_user]` | List a character |
| `/buy <listingID> [qty]` | Purchase |
| `/removelisting <listingID>` | Delist your listing |
| `/marketstats` | Market statistics |
| `/topselling` | Top selling characters |
| `/mylistings` | Your active listings |
| `/mypurchases` | Your purchase history |

### 🐉 Dragon Ball

| Command | Description |
|---|---|
| `/searchball` | Search for a dragon ball (1h cooldown) |
| `/dragonballs` | View your collection |
| `/wish` | Summon Shenron (all 7 balls) |
| `/powerlevel` | Check your power level |
| `/battle` | Battle another player (reply) |
| `/dbtop` | Battle leaderboard |

### 📊 Stats & Fun

| Command | Description |
|---|---|
| `/top` | Rotating leaderboard |
| `/quiz` | Start a character quiz (group) |
| `/rarities` | View rarity tier list |
| `/about` | Bot information |

### ⚙️ Group Admin

| Command | Description |
|---|---|
| `/gsettings` | View group settings |
| `/spawnon` / `/spawnoff` | Toggle character spawns |
| `/spawnfreq <n>` | Set spawn frequency |
| `/announcement on\|off` | Toggle spawn alerts |
| `/drop` | Force a spawn (sudo only) |

### 🔑 Sudo/Owner

| Command | Description |
|---|---|
| `/stats` | Bot statistics |
| `/gban <user>` | Global ban |
| `/gunban <user>` | Global unban |
| `/gmute <user>` | Global mute |
| `/gunmute <user>` | Global unmute |
| `/addsudo` / `/removesudo` | Manage sudo users |
| `/adduploader` / `/removeuploader` | Manage uploaders |
| `/broadcast` | Broadcast to all users |
| `/groupcast` | Broadcast to all groups |
| `/setmode <mode>` | Set game mode |
| `/cleanspawns` | Remove expired spawns |
| `/upload` | Upload a character (reply to media) |
| `/edit <ID> <field> <val>` | Edit a character |
| `/delete <ID>` | Delete a character |
| `/ping` | Latency check |
| `/uptime` | Bot uptime |
| `/sysinfo` | System information |
| `/shell <cmd>` | Run shell command (owner only) |
| `/reload <module>` | Hot-reload a module (dev only) |
| `/restart` | Restart the bot (owner only) |

---

## ✨ Rarity Tiers

| Tier | Emoji | Name | Drop Chance | Tradeable |
|---|---|---|---|---|
| 1 | ⚫ | Common | 55% | ✅ |
| 2 | 🔵 | Rare | 22% | ✅ |
| 3 | 🌌 | Legendry | 10% | ✅ |
| 4 | 🔥 | Elite | 5% | ✅ |
| 5 | 💎 | Seasonal | 2.5% | ✅ |
| — | 🌸 | Festival (sub) | 1.2% | ✅ |
| 6 | 💀 | Mythic | 0.8% | ❌ |
| — | 🔮 | Limited (sub) | 0.35% | ❌ |
| — | 🏆 | Sports (sub) | 0.30% | ❌ |
| — | 🧝 | Fantasy (sub) | 0.28% | ❌ |
| 7 | ✨ | Eternal | 0.10% | ❌ |
| — | 🎠 | Verse (sub) | 0.04% | ❌ VIDEO ONLY |

---

## 🔒 Security Notes

- **Never** hardcode credentials — use environment variables
- The `.env` file is gitignored
- Owner IDs are validated at startup
- All URLs are validated as HTTPS
- Atomic MongoDB operations prevent race conditions in market/trades

---

## 📜 License

MIT License. Build freely, contribute back! 🌸
