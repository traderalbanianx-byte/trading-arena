# Trading Arena — Bot Setup Guide

## What this does
- `arena_bot.py` — watches all 4 Telegram channels 24/7, sends every trade post to Claude AI, extracts the trade data, saves it to `trades.json`
- `server.py` — serves `trades.json` to the website with the 48h delay applied automatically
- The website auto-refreshes every 5 minutes from the API

---

## Step 1 — Install Python dependencies

```bash
cd trading-arena-bot
pip install -r requirements.txt
```

---

## Step 2 — Get your Telegram API credentials

1. Go to **https://my.telegram.org**
2. Log in with the phone number that is a member of all 4 channels
3. Click **API Development Tools**
4. Create an app (name doesn't matter)
5. Copy **App api_id** and **App api_hash**

---

## Step 3 — Find your channel usernames or IDs

For each of the 4 channels, get the username:
- Open the channel in Telegram Web
- The URL will be `https://web.telegram.org/k/#@channelname`
- The part after `@` is the username

If the channel has no public username, use the numeric ID:
- Forward a message from the channel to **@userinfobot** on Telegram
- It will show the channel's numeric ID

---

## Step 4 — Set up your .env file

```bash
cp .env.example .env
```

Edit `.env` and fill in all values.

---

## Step 5 — First run (authenticate with Telegram)

```bash
python arena_bot.py
```

The first time, it will ask for your phone number and a verification code Telegram sends you. After that, the session is saved and you won't need to do this again.

---

## Step 6 — Import trade history (optional but recommended)

To backfill trades from the past 200 messages in each channel:

```bash
python arena_bot.py --import-history --limit 200
```

This runs once and then exits. Claude will parse every historical post.

---

## Step 7 — Run everything

Open **two terminal windows**:

**Terminal 1 — API server:**
```bash
cd trading-arena-bot
python server.py
```

**Terminal 2 — Telegram bot:**
```bash
cd trading-arena-bot
python arena_bot.py
```

Open the website at **http://localhost:5000**

---

## Running permanently (on a VPS/server)

Use `pm2` or `systemd` to keep both processes alive:

```bash
# Install pm2
npm install -g pm2

# Start both processes
pm2 start server.py --interpreter python3 --name arena-server
pm2 start arena_bot.py --interpreter python3 --name arena-bot

# Save and auto-restart on reboot
pm2 save
pm2 startup
```

---

## Admin API

The server has admin endpoints protected by your `ADMIN_SECRET`.

To get your admin token:
```python
import hashlib
secret = "your_ADMIN_SECRET_value"
token = hashlib.sha256(secret.encode()).hexdigest()
print(token)
```

Use it as a header: `X-Admin-Token: <token>`

| Endpoint | Method | Description |
|---|---|---|
| `/api/admin/trades` | GET | All trades, no delay |
| `/api/admin/trade` | POST | Add trade manually |
| `/api/admin/trade/<id>` | PATCH | Update a trade |
| `/api/admin/trade/<id>` | DELETE | Flag a trade (not deleted — audit preserved) |

---

## How the 48h delay works

- `arena_bot.py` saves trades with the **real timestamp** of when the Telegram message was posted
- `server.py` checks: `now - open_timestamp >= 48 hours`
- If yes → trade is served to the public website
- If no → trade exists in `trades.json` but the API does not return it yet
- The admin endpoint `/api/admin/trades` bypasses this and shows everything

---

## File structure

```
trading-arena-bot/
  arena_bot.py        ← Telegram listener + Claude parser
  server.py           ← Flask API with 48h delay
  requirements.txt
  .env                ← your credentials (never commit this)
  .env.example        ← template
  arena_session.session  ← created on first run (Telegram auth)

trades.json           ← trade database (in parent folder)
trading-arena.html    ← the website
```

---

## Cost estimate

- **Claude API**: ~$0.002–0.005 per trade post parsed (using claude-opus-4-6 for parsing + claude-haiku-4-5 for close detection)
- With 4 traders posting ~3–5 times/day = roughly **$0.05–0.10/day**
- Monthly cost: **under $5**
