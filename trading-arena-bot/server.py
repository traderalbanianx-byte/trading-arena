"""
Trading Arena — API Server
==========================
Serves trade data to the public website.
Applies the 48-hour delay automatically.
Admin endpoint bypasses the delay.

Run: python server.py
"""

import json
import os
import hashlib
import hmac
from datetime import datetime, timezone, timedelta
from pathlib import Path
from functools import wraps
from flask import Flask, jsonify, request, send_from_directory, abort
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

app  = Flask(__name__, static_folder='..', static_url_path='')
CORS(app)

DATA_FILE    = Path('../trades.json')
DELAY_HOURS  = int(os.getenv('DELAY_HOURS', '48'))
ADMIN_SECRET = os.getenv('ADMIN_SECRET', 'arena2024_api_secret')

# ─── HELPERS ─────────────────────────────────────────────────────────────────
def load_trades() -> list[dict]:
    if not DATA_FILE.exists():
        return []
    with open(DATA_FILE) as f:
        return json.load(f)

def save_trades(trades: list[dict]):
    with open(DATA_FILE, 'w') as f:
        json.dump(trades, f, indent=2, default=str)

def is_published(trade: dict) -> bool:
    """Returns True if trade has passed the 48h delay window."""
    ts = trade.get('open_timestamp')
    if not ts:
        return False
    try:
        opened = datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
        cutoff = datetime.now(timezone.utc) - timedelta(hours=DELAY_HOURS)
        return opened <= cutoff
    except Exception:
        return False

def sanitize_for_public(trade: dict) -> dict:
    """Remove sensitive fields before sending to public."""
    safe = {k: v for k, v in trade.items() if k not in ('raw_text', 'telegram_msg_id', 'channel')}
    # For open trades: round entry to nearest 50 (zone, not exact signal)
    if safe.get('status') == 'open':
        entry = safe.get('entry', 0)
        if entry:
            safe['entry_zone'] = f"{round(entry * 0.995, 0):.0f} – {round(entry * 1.005, 0):.0f}"
    return safe

def require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('X-Admin-Token', '')
        expected = hashlib.sha256(ADMIN_SECRET.encode()).hexdigest()
        if not hmac.compare_digest(token, expected):
            abort(401)
        return f(*args, **kwargs)
    return decorated

# ─── PUBLIC ENDPOINTS ────────────────────────────────────────────────────────

@app.get('/api/trades')
def get_public_trades():
    """Returns all trades that have passed the 48h delay. Sanitized."""
    trades = load_trades()
    published = [sanitize_for_public(t) for t in trades if is_published(t)]
    return jsonify({
        'trades':       published,
        'delay_hours':  DELAY_HOURS,
        'last_updated': datetime.now(timezone.utc).isoformat(),
        'count':        len(published),
    })

@app.get('/api/trades/active')
def get_active_trades():
    """Returns currently open trades that have passed the 48h delay."""
    trades = load_trades()
    active = [
        sanitize_for_public(t) for t in trades
        if t.get('status') == 'open' and is_published(t)
    ]
    return jsonify({
        'trades':      active,
        'delay_hours': DELAY_HOURS,
        'count':       len(active),
    })

@app.get('/api/trades/closed')
def get_closed_trades():
    """Returns all closed/stopped trades that passed the 48h delay."""
    trades = load_trades()
    closed = [
        sanitize_for_public(t) for t in trades
        if t.get('status') in ('closed', 'stopped', 'partial') and is_published(t)
    ]
    closed.sort(key=lambda t: t.get('close_timestamp') or '', reverse=True)
    return jsonify({'trades': closed, 'count': len(closed)})

@app.get('/api/leaderboard')
def get_leaderboard():
    """Computes monthly leaderboard from closed, realized trades."""
    trades = load_trades()
    closed = [
        t for t in trades
        if t.get('status') in ('closed', 'stopped', 'partial') and is_published(t)
    ]

    traders = {}
    for t in closed:
        name = t.get('trader')
        if not name:
            continue
        if name not in traders:
            traders[name] = {'spot': [], 'futures': []}
        bucket = 'spot' if t.get('market_type') == 'spot' else 'futures'
        traders[name][bucket].append(t.get('pnl') or 0)

    def calc(pnl_list):
        if not pnl_list:
            return None
        total   = sum(pnl_list)
        wins    = sum(1 for x in pnl_list if x > 0)
        win_rate = (wins / len(pnl_list)) * 100
        max_dd  = min(pnl_list)
        ra      = total / (abs(max_dd) + 1)
        score   = (total * 0.3) + (win_rate * 0.25) + (ra * 0.3) - (abs(max_dd) * 0.15)
        return {
            'total_return': round(total, 2),
            'win_rate':     round(win_rate, 1),
            'max_drawdown': round(max_dd, 2),
            'risk_adjusted': round(ra, 2),
            'trade_count':  len(pnl_list),
            'score':        round(score, 2),
        }

    result = {}
    for name, buckets in traders.items():
        all_pnl  = buckets['spot'] + buckets['futures']
        result[name] = {
            'overall':  calc(all_pnl),
            'spot':     calc(buckets['spot']),
            'futures':  calc(buckets['futures']),
        }

    return jsonify({'leaderboard': result, 'computed_at': datetime.now(timezone.utc).isoformat()})

@app.get('/api/trader/<name>')
def get_trader(name: str):
    """Returns all published trades for a specific trader."""
    trades = load_trades()
    trader_trades = [
        sanitize_for_public(t) for t in trades
        if t.get('trader', '').lower() == name.lower() and is_published(t)
    ]
    return jsonify({'trader': name, 'trades': trader_trades, 'count': len(trader_trades)})

@app.get('/api/stats')
def get_stats():
    """Homepage summary stats."""
    trades = load_trades()
    published = [t for t in trades if is_published(t)]
    closed = [t for t in published if t.get('status') in ('closed', 'stopped', 'partial')]
    wins = [t for t in closed if (t.get('pnl') or 0) > 0]
    active = [t for t in published if t.get('status') == 'open']

    win_rate = round((len(wins) / len(closed)) * 100, 1) if closed else 0
    best = max((t.get('pnl') or 0 for t in closed), default=0)

    return jsonify({
        'total_trades_logged': len(published),
        'active_trades':       len(active),
        'closed_trades':       len(closed),
        'combined_win_rate':   win_rate,
        'best_trade_pct':      round(best, 1),
        'traders':             ['Cash', 'Gauls', 'Titan', 'Bamp'],
    })

# ─── ADMIN ENDPOINTS (require X-Admin-Token header) ──────────────────────────

@app.get('/api/admin/trades')
@require_admin
def admin_get_all_trades():
    """Returns ALL trades including those still in the 48h delay window."""
    trades = load_trades()
    now = datetime.now(timezone.utc)
    for t in trades:
        ts = t.get('open_timestamp')
        if ts:
            opened = datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
            hours_ago = (now - opened).total_seconds() / 3600
            t['_delay_remaining_hours'] = max(0, round(DELAY_HOURS - hours_ago, 1))
            t['_is_published'] = is_published(t)
    return jsonify({'trades': trades, 'count': len(trades)})

@app.post('/api/admin/trade')
@require_admin
def admin_add_trade():
    """Manually add a trade (for corrections or manual entry)."""
    data = request.get_json()
    if not data:
        abort(400)
    trades = load_trades()
    new_id = max((t.get('id', 0) for t in trades), default=0) + 1
    record = {
        'id':              new_id,
        'trader':          data.get('trader', ''),
        'asset':           data.get('asset', '').upper(),
        'market_type':     data.get('market_type', 'futures'),
        'side':            data.get('side', 'long'),
        'entry':           data.get('entry', 0),
        'current':         data.get('entry', 0),
        'exit':            None,
        'sl':              data.get('sl', 0),
        'targets':         data.get('targets', []),
        'leverage':        data.get('leverage'),
        'status':          data.get('status', 'open'),
        'pnl':             data.get('pnl'),
        'notes':           data.get('notes', ''),
        'data_quality':    'complete' if data.get('leverage') else 'missing_leverage',
        'open_timestamp':  datetime.now(timezone.utc).isoformat(),
        'close_timestamp': None,
        'source':          'Manual',
        'channel':         'admin',
        'telegram_msg_id': None,
        'raw_text':        '',
    }
    trades.append(record)
    save_trades(trades)
    return jsonify({'success': True, 'trade': record}), 201

@app.patch('/api/admin/trade/<int:trade_id>')
@require_admin
def admin_update_trade(trade_id: int):
    """Update an existing trade (e.g. close it, set exit price)."""
    data = request.get_json()
    trades = load_trades()
    for t in trades:
        if t.get('id') == trade_id:
            allowed = ['status', 'exit', 'pnl', 'close_timestamp', 'current', 'notes', 'data_quality']
            for key in allowed:
                if key in data:
                    t[key] = data[key]
            if data.get('status') in ('closed', 'stopped', 'partial') and not t.get('close_timestamp'):
                t['close_timestamp'] = datetime.now(timezone.utc).isoformat()
            save_trades(trades)
            return jsonify({'success': True, 'trade': t})
    abort(404)

@app.delete('/api/admin/trade/<int:trade_id>')
@require_admin
def admin_flag_trade(trade_id: int):
    """
    We do NOT delete trades (anti-cherry-picking rule).
    Instead, we flag them as 'flagged' with a reason.
    """
    data = request.get_json() or {}
    trades = load_trades()
    for t in trades:
        if t.get('id') == trade_id:
            t['status'] = 'flagged'
            t['flag_reason'] = data.get('reason', 'manually flagged')
            t['flagged_at'] = datetime.now(timezone.utc).isoformat()
            save_trades(trades)
            return jsonify({'success': True, 'message': 'Trade flagged (not deleted — audit trail preserved)'})
    abort(404)

# ─── STATIC FILES ─────────────────────────────────────────────────────────────

@app.get('/')
def serve_index():
    return send_from_directory('..', 'trading-arena.html')

@app.get('/health')
def health():
    return jsonify({'status': 'ok', 'delay_hours': DELAY_HOURS})

# ─── MAIN ────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    debug = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    print(f'\n  Trading Arena API running on http://localhost:{port}')
    print(f'  Delay: {DELAY_HOURS}h  |  Data: {DATA_FILE.resolve()}\n')
    app.run(host='0.0.0.0', port=port, debug=debug)
