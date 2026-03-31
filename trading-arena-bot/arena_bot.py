"""
Trading Arena — Telegram Bot + Claude AI Parser
================================================
Monitors all 4 Arena Telegram channels.
Uses Claude to extract trade data from every message.
Saves to trades.json with a 48-hour delay filter for the public website.

Run: python arena_bot.py
"""

import asyncio
import json
import os
import re
import logging
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.tl.types import Channel
import anthropic

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('arena_bot.log'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ─── CONFIG ──────────────────────────────────────────────────────────────────
API_ID       = int(os.getenv('TELEGRAM_API_ID', '0'))
API_HASH     = os.getenv('TELEGRAM_API_HASH', '')
PHONE        = os.getenv('TELEGRAM_PHONE', '')
ANTHROPIC_KEY = os.getenv('ANTHROPIC_API_KEY', '')

# Map Telegram channel username/ID → Arena trader name
# Fill these with your actual channel @usernames or numeric IDs
CHANNEL_MAP = {
    os.getenv('CHANNEL_CASH',  'channel_cash_username'):  'Cash',
    os.getenv('CHANNEL_GAULS', 'channel_gauls_username'): 'Gauls',
    os.getenv('CHANNEL_TITAN', 'channel_titan_username'): 'Titan',
    os.getenv('CHANNEL_BAMP',  'channel_bamp_username'):  'Bamp',
}

DATA_FILE = Path('../trades.json')
SESSION_FILE = 'arena_session'

# ─── CLAUDE PARSER ───────────────────────────────────────────────────────────
claude = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

PARSE_PROMPT = """
You are a crypto trade extractor for the Trading Arena system.

A trader posted this message in their Telegram channel:
<message>
{message}
</message>

Trader name: {trader}

Extract ALL trade setups from this message. A single post often contains both a LONG and a SHORT setup — extract both as separate trade objects.

Return a JSON array. Each object must follow this schema exactly:
{{
  "asset":        "BTC",           // ticker symbol, uppercase
  "market_type":  "futures",       // "spot" or "futures" (infer from context — if leveraged, futures)
  "side":         "long",          // "long" or "short"
  "entry":        64392.3,         // entry price as number
  "sl":           63744.9,         // stop loss as number
  "targets":      [66329, 69138],  // array of target prices as numbers
  "leverage":     null,            // string like "10x" or null if not mentioned
  "notes":        "brief thesis",  // 1-sentence summary of the thesis
  "status":       "open",          // always "open" for new posts
  "data_quality": "complete"       // "complete" or "missing_leverage" or "unconfirmed_execution"
}}

Rules:
- If no clear entry price is given, do not guess — return empty array []
- If leverage is not mentioned, set leverage to null and data_quality to "missing_leverage"
- If it's clearly a signal/idea but execution isn't confirmed, set data_quality to "unconfirmed_execution"
- Strip all emoji from notes
- Return ONLY the raw JSON array. No markdown, no explanation.
"""

async def parse_message_with_claude(message_text: str, trader: str) -> list[dict]:
    """Send message text to Claude, get back structured trade data."""
    try:
        response = claude.messages.create(
            model='claude-opus-4-6',
            max_tokens=1024,
            messages=[{
                'role': 'user',
                'content': PARSE_PROMPT.format(message=message_text, trader=trader)
            }]
        )
        raw = response.content[0].text.strip()
        # Strip markdown code fences if Claude wraps in ```json ... ```
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        trades = json.loads(raw)
        if not isinstance(trades, list):
            return []
        return trades
    except json.JSONDecodeError as e:
        log.warning(f'Claude returned invalid JSON for {trader}: {e}')
        return []
    except Exception as e:
        log.error(f'Claude parse error for {trader}: {e}')
        return []

# ─── TRADE STORAGE ───────────────────────────────────────────────────────────
def load_trades() -> list[dict]:
    if DATA_FILE.exists():
        with open(DATA_FILE) as f:
            return json.load(f)
    return []

def save_trades(trades: list[dict]):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, 'w') as f:
        json.dump(trades, f, indent=2, default=str)
    log.info(f'Saved {len(trades)} trades to {DATA_FILE}')

def next_id(trades: list[dict]) -> int:
    return max((t.get('id', 0) for t in trades), default=0) + 1

def build_trade_record(
    parsed: dict,
    trader: str,
    telegram_message_id: int,
    channel: str,
    raw_text: str
) -> dict:
    now_iso = datetime.now(timezone.utc).isoformat()
    trades = load_trades()
    return {
        'id':               next_id(trades),
        'trader':           trader,
        'asset':            parsed.get('asset', 'UNKNOWN').upper(),
        'market_type':      parsed.get('market_type', 'futures'),
        'side':             parsed.get('side', 'long'),
        'entry':            parsed.get('entry', 0),
        'current':          parsed.get('entry', 0),   # will be updated by price feed
        'exit':             None,
        'sl':               parsed.get('sl', 0),
        'targets':          parsed.get('targets', []),
        'leverage':         parsed.get('leverage'),
        'status':           'open',
        'pnl':              None,
        'notes':            parsed.get('notes', ''),
        'data_quality':     parsed.get('data_quality', 'complete'),
        'open_timestamp':   now_iso,
        'close_timestamp':  None,
        'source':           'Telegram',
        'channel':          channel,
        'telegram_msg_id':  telegram_message_id,
        'raw_text':         raw_text[:500],   # store first 500 chars for audit
    }

def is_duplicate(trades: list[dict], trader: str, asset: str, side: str, entry: float) -> bool:
    """Prevent double-logging the same setup."""
    for t in trades:
        if (t['trader'] == trader and
            t['asset'] == asset and
            t['side'] == side and
            t['status'] == 'open' and
            abs(t['entry'] - entry) < entry * 0.005):   # within 0.5% of entry
            return True
    return False

# ─── MESSAGE RELEVANCE CHECK ─────────────────────────────────────────────────
TRADE_KEYWORDS = [
    'entry', 'long', 'short', 'sl', 'stop', 'target', 'tp',
    'buy', 'sell', 'setup', 'position', 'trade', '📈', '🛑', '🎯',
]

def looks_like_trade(text: str) -> bool:
    """Quick pre-filter — only parse messages that look like trade calls."""
    text_lower = text.lower()
    # Need at least 2 trade keywords and a price-like pattern
    keyword_hits = sum(1 for kw in TRADE_KEYWORDS if kw in text_lower)
    has_price = bool(re.search(r'\d{3,6}\.?\d{0,2}', text))
    return keyword_hits >= 2 and has_price

# ─── CLOSE DETECTOR ──────────────────────────────────────────────────────────
CLOSE_KEYWORDS = ['closed', 'exit', 'tp hit', 'target hit', 'sl hit', 'stopped', 'out at', 'closed at', 'take profit']

CLOSE_PROMPT = """
A trader posted this Telegram message:
<message>
{message}
</message>

Does this message announce closing or updating an existing trade position?
If yes, extract:
{{
  "is_close": true,
  "asset": "BTC",
  "side": "long",
  "exit_price": 67100,
  "status": "closed",   // "closed", "stopped", or "partial"
  "pnl_percent": 4.2    // percent gain/loss if mentioned, else null
}}

If no:
{{"is_close": false}}

Return ONLY raw JSON.
"""

async def check_for_close(message_text: str, trader: str) -> dict | None:
    """Check if a message is closing an existing trade."""
    text_lower = message_text.lower()
    if not any(kw in text_lower for kw in CLOSE_KEYWORDS):
        return None
    try:
        response = claude.messages.create(
            model='claude-haiku-4-5-20251001',   # fast + cheap for classification
            max_tokens=256,
            messages=[{
                'role': 'user',
                'content': CLOSE_PROMPT.format(message=message_text)
            }]
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        data = json.loads(raw)
        return data if data.get('is_close') else None
    except Exception as e:
        log.warning(f'Close detection error for {trader}: {e}')
        return None

def apply_close(trades: list[dict], trader: str, close_data: dict) -> bool:
    """Find the matching open trade and mark it closed."""
    asset = close_data.get('asset', '').upper()
    side  = close_data.get('side', '').lower()
    for t in trades:
        if (t['trader'] == trader and
            t['asset'] == asset and
            t['side'] == side and
            t['status'] == 'open'):
            t['status'] = close_data.get('status', 'closed')
            t['exit'] = close_data.get('exit_price')
            t['pnl'] = close_data.get('pnl_percent')
            t['close_timestamp'] = datetime.now(timezone.utc).isoformat()
            log.info(f'Closed trade #{t["id"]} — {trader} {asset} {side} @ {t["exit"]}  PnL: {t["pnl"]}%')
            return True
    log.warning(f'No matching open trade found to close for {trader} {asset} {side}')
    return False

# ─── TELEGRAM CLIENT ─────────────────────────────────────────────────────────
# Client is created inside main() to avoid Python 3.10+ event loop issues
client = None

async def on_new_message(event):
    chat    = await event.get_chat()
    channel = getattr(chat, 'username', None) or str(chat.id)
    trader  = CHANNEL_MAP.get(channel) or CHANNEL_MAP.get(str(chat.id))

    if not trader:
        log.warning(f'Unknown channel: {channel}')
        return

    text = event.message.message or ''
    if not text.strip():
        return

    log.info(f'New message from {trader} ({channel}): {text[:80]}...')

    trades = load_trades()

    # ── 1. Check if it's closing an existing trade ───────────────────────────
    close_data = await check_for_close(text, trader)
    if close_data:
        if apply_close(trades, trader, close_data):
            save_trades(trades)
        return

    # ── 2. Quick relevance check before calling Claude ───────────────────────
    if not looks_like_trade(text):
        log.debug(f'Skipping non-trade message from {trader}')
        return

    # ── 3. Parse new trade setups with Claude ────────────────────────────────
    parsed_trades = await parse_message_with_claude(text, trader)
    if not parsed_trades:
        log.info(f'No trade setups extracted from {trader} message')
        return

    added = 0
    for parsed in parsed_trades:
        entry = parsed.get('entry', 0)
        asset = parsed.get('asset', '').upper()
        side  = parsed.get('side', '')

        if not entry or not asset or not side:
            log.warning(f'Incomplete parsed trade from {trader}: {parsed}')
            continue

        if is_duplicate(trades, trader, asset, side, entry):
            log.info(f'Duplicate trade skipped: {trader} {asset} {side} @ {entry}')
            continue

        record = build_trade_record(parsed, trader, event.message.id, channel, text)
        trades.append(record)
        added += 1
        log.info(f'Added trade #{record["id"]}: {trader} {asset} {side} @ {entry}')

    if added > 0:
        save_trades(trades)
        log.info(f'{added} new trade(s) saved from {trader}')

# ─── HISTORICAL IMPORT ───────────────────────────────────────────────────────
async def import_history(limit: int = 100, c=None):
    """
    One-time import of recent channel history.
    Run manually: python arena_bot.py --import-history
    """
    # Build a map of channel_id → entity by iterating dialogs first
    # This caches the access hashes Telethon needs for private channels
    log.info('Caching channel entities from dialogs...')
    entity_map = {}
    async for dialog in c.iter_dialogs():
        did = str(dialog.id)
        for ch_key in CHANNEL_MAP:
            if did == str(ch_key) or str(dialog.id) == str(ch_key):
                entity_map[ch_key] = dialog.entity
                log.info(f'Found entity for {CHANNEL_MAP[ch_key]}: {dialog.name}')

    log.info(f'Importing last {limit} messages per channel...')
    for channel, trader in CHANNEL_MAP.items():
        log.info(f'Processing {trader} ({channel})...')
        entity = entity_map.get(channel)
        if not entity:
            log.error(f'Could not find entity for {trader} ({channel}) — skipping')
            continue
        try:
            async for message in c.iter_messages(entity, limit=limit):
                if not message.message:
                    continue
                text = message.message
                if not looks_like_trade(text):
                    continue
                trades = load_trades()
                parsed_list = await parse_message_with_claude(text, trader)
                for parsed in parsed_list:
                    entry = parsed.get('entry', 0)
                    asset = parsed.get('asset', '').upper()
                    side  = parsed.get('side', '')
                    if not entry or not asset or not side:
                        continue
                    if is_duplicate(trades, trader, asset, side, entry):
                        continue
                    # Use message date for historical records
                    record = build_trade_record(parsed, trader, message.id, channel, text)
                    record['open_timestamp'] = message.date.isoformat()
                    trades.append(record)
                    save_trades(trades)
                    log.info(f'Imported: {trader} {asset} {side} @ {entry}')
                await asyncio.sleep(0.5)   # rate limit
        except Exception as e:
            log.error(f'History import error for {trader}: {e}')

# ─── MAIN ────────────────────────────────────────────────────────────────────
async def main():
    import sys
    global client
    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await client.start(phone=PHONE)
    log.info('Telegram client connected.')

    if '--import-history' in sys.argv:
        limit = 200
        for i, arg in enumerate(sys.argv):
            if arg == '--limit' and i + 1 < len(sys.argv):
                limit = int(sys.argv[i + 1])
        await import_history(limit=limit, c=client)
        log.info('History import complete.')
        await client.disconnect()
        return

    client.add_event_handler(
        on_new_message,
        events.NewMessage(chats=list(CHANNEL_MAP.keys()))
    )

    log.info('Arena Bot is live. Monitoring channels:')
    for ch, trader in CHANNEL_MAP.items():
        log.info(f'  {trader} → {ch}')

    await client.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())
