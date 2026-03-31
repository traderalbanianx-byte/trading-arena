#!/bin/bash
# Restore Telegram session from env variable (avoids login prompt on Railway)
if [ -n "$TELEGRAM_SESSION_B64" ]; then
  echo "Restoring Telegram session..."
  echo "$TELEGRAM_SESSION_B64" | base64 -d > arena_session.session
fi

echo "Starting Trading Arena API server..."
python3 server.py &

echo "Starting Arena Telegram bot..."
python3 arena_bot.py
