from datetime import datetime, timedelta
from typing import List, Dict
from app.services.market_data_service import market_data_service


signal_records: List[Dict] = []
MAX_RECORDS = 500


async def record_signal(symbol: str, signal: str, confidence: float, price: float, reasons: list):
    entry = {
        'symbol': symbol.upper(),
        'signal': signal,
        'confidence': confidence,
        'entry_price': price,
        'timestamp': datetime.now().isoformat(),
        'resolved': False,
        'outcome': None,
        'exit_price': None,
        'pnl_pct': None,
    }
    signal_records.insert(0, entry)
    if len(signal_records) > MAX_RECORDS:
        signal_records.pop()


async def resolve_signals():
    now = datetime.now()
    for rec in signal_records:
        if rec['resolved']:
            continue
        age_hours = (now - datetime.fromisoformat(rec['timestamp'])).total_seconds() / 3600
        if age_hours < 4:
            continue
        try:
            price_data = await market_data_service.get_price_data(rec['symbol'].lower())
            if not price_data:
                continue
            exit_price = price_data['price']
            entry = rec['entry_price']
            pnl = ((exit_price - entry) / entry) * 100

            if rec['signal'] == 'BUY':
                outcome = 'win' if pnl > 0.5 else 'loss'
            elif rec['signal'] == 'SELL':
                outcome = 'win' if pnl < -0.5 else 'loss'
            else:
                outcome = 'neutral'

            rec['resolved'] = True
            rec['outcome'] = outcome
            rec['exit_price'] = exit_price
            rec['pnl_pct'] = round(pnl, 2)
        except Exception:
            pass


def get_accuracy_stats() -> Dict:
    total = len(signal_records)
    resolved = [r for r in signal_records if r['resolved']]
    wins = [r for r in resolved if r['outcome'] == 'win']
    losses = [r for r in resolved if r['outcome'] == 'loss']

    return {
        'total_signals': total,
        'resolved': len(resolved),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': round(len(wins) / len(resolved) * 100, 1) if resolved else 0,
        'avg_pnl': round(sum(r['pnl_pct'] for r in resolved if r['pnl_pct']) / len(resolved), 2) if resolved else 0,
        'by_symbol': {},
    }
