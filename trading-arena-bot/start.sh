#!/bin/bash
# Starts both the API server and the Telegram bot together on Railway
echo "Starting Trading Arena API server..."
python3 server.py &

echo "Starting Arena Telegram bot..."
python3 arena_bot.py
