import json
import sqlite3
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from app.services.market_data_service import market_data_service
from app.config import settings


_persistent_dir = settings.persistent_dir
os.makedirs(_persistent_dir, exist_ok=True)
DB_PATH = os.path.join(_persistent_dir, 'signals.db')


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            signal TEXT NOT NULL,
            confidence REAL NOT NULL,
            entry_price REAL NOT NULL,
            timestamp TEXT NOT NULL,
            resolved INTEGER DEFAULT 0,
            outcome TEXT,
            exit_price REAL,
            pnl_pct REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signal_cache (
            symbol TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS realtime_cache (
            symbol TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sent_signals (
            symbol TEXT PRIMARY KEY,
            composite_key TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    """)
    return conn


async def record_signal(symbol: str, signal: str, confidence: float, price: float, reasons: list):
    conn = _get_db()
    conn.execute(
        "INSERT INTO signals (symbol, signal, confidence, entry_price, timestamp) VALUES (?, ?, ?, ?, ?)",
        (symbol.upper(), signal, confidence, price, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


async def resolve_signals():
    conn = _get_db()
    now = datetime.now()
    rows = conn.execute(
        "SELECT id, symbol, signal, entry_price, timestamp FROM signals WHERE resolved = 0"
    ).fetchall()
    for row in rows:
        age_hours = (now - datetime.fromisoformat(row['timestamp'])).total_seconds() / 3600
        if age_hours < 4:
            continue
        try:
            price_data = await market_data_service.get_price_data(row['symbol'].lower())
            if not price_data:
                continue
            exit_price = price_data['price']
            entry = row['entry_price']
            pnl = ((exit_price - entry) / entry) * 100

            if row['signal'] == 'BUY':
                outcome = 'win' if pnl > 0.5 else 'loss'
            elif row['signal'] == 'SELL':
                outcome = 'win' if pnl < -0.5 else 'loss'
            else:
                outcome = 'neutral'

            conn.execute(
                "UPDATE signals SET resolved = 1, outcome = ?, exit_price = ?, pnl_pct = ? WHERE id = ?",
                (outcome, exit_price, round(pnl, 2), row['id']),
            )
            conn.commit()
        except Exception:
            pass
    conn.close()


def get_accuracy_stats() -> Dict:
    conn = _get_db()
    total = conn.execute("SELECT COUNT(*) as c FROM signals").fetchone()['c']
    resolved = conn.execute("SELECT COUNT(*) as c FROM signals WHERE resolved = 1").fetchone()['c']
    wins = conn.execute("SELECT COUNT(*) as c FROM signals WHERE outcome = 'win'").fetchone()['c']
    losses = conn.execute("SELECT COUNT(*) as c FROM signals WHERE outcome = 'loss'").fetchone()['c']
    avg = conn.execute("SELECT COALESCE(AVG(pnl_pct), 0) as a FROM signals WHERE resolved = 1").fetchone()['a']
    conn.close()

    return {
        'total_signals': total,
        'resolved': resolved,
        'wins': wins,
        'losses': losses,
        'win_rate': round(wins / resolved * 100, 1) if resolved else 0,
        'avg_pnl': round(avg, 2),
        'by_symbol': {},
    }


def save_signal_cache(symbol: str, data: dict):
    conn = _get_db()
    conn.execute(
        "INSERT OR REPLACE INTO signal_cache (symbol, data, timestamp) VALUES (?, ?, ?)",
        (symbol, json.dumps(data), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def save_realtime_cache(symbol: str, data: dict):
    conn = _get_db()
    conn.execute(
        "INSERT OR REPLACE INTO realtime_cache (symbol, data, timestamp) VALUES (?, ?, ?)",
        (symbol, json.dumps(data), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def save_sent_signal(symbol: str, composite_key: str):
    conn = _get_db()
    conn.execute(
        "INSERT OR REPLACE INTO sent_signals (symbol, composite_key, timestamp) VALUES (?, ?, ?)",
        (symbol, composite_key, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def load_signal_cache() -> Dict[str, dict]:
    conn = _get_db()
    rows = conn.execute("SELECT symbol, data FROM signal_cache").fetchall()
    conn.close()
    result = {}
    for row in rows:
        try:
            result[row['symbol']] = json.loads(row['data'])
        except Exception:
            pass
    return result


def load_realtime_cache() -> Dict[str, dict]:
    conn = _get_db()
    rows = conn.execute("SELECT symbol, data FROM realtime_cache").fetchall()
    conn.close()
    result = {}
    for row in rows:
        try:
            result[row['symbol']] = json.loads(row['data'])
        except Exception:
            pass
    return result


def load_sent_signals() -> Dict[str, str]:
    conn = _get_db()
    rows = conn.execute("SELECT symbol, composite_key FROM sent_signals").fetchall()
    conn.close()
    return {row['symbol']: row['composite_key'] for row in rows}
