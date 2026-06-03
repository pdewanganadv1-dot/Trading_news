"""
Send ALMA-Optimized 1m backtest report to Telegram.
Shows exact entry/exit for every trade per stock.
"""
import sys, os, json, time, pickle, asyncio, httpx
sys.path.insert(0, os.path.dirname(__file__))
os.environ["PERSISTENT_DIR"] = os.path.join(os.path.dirname(__file__), "data")

from datetime import datetime
from app.services.strategy_builder import (
    LEADING_INDICATORS, CONFIRMATION_FILTERS,
)
from app.data.stocks import INDIAN_STOCKS
from app.config import settings

TOKEN = settings.telegram_bot_token
CHAT_ID = settings.telegram_chat_id
BASE = f"https://api.telegram.org/bot{TOKEN}"

CACHE_DIR = os.path.join(os.path.dirname(__file__), "data", "ohlc_1m_cache")
REPORT_DIR = os.path.join(os.path.dirname(__file__), "data")
MIN_BARS = 50  # 1m needs more bars for indicators

LIGHT_CONFS = ["EMA 20", "MACD", "RSI", "Volume", "Price Action"]

def load_all_1m():
    data = {}
    for symbol in INDIAN_STOCKS:
        path = os.path.join(CACHE_DIR, f"{symbol.lower()}.pkl")
        if not os.path.exists(path):
            continue
        with open(path, "rb") as f:
            df = pickle.load(f)
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        if len(df) >= MIN_BARS:
            data[symbol] = df
    return data

def bt_trades_1m(symbol, df):
    opens = [float(r["Open"]) for _, r in df.iterrows()]
    highs = [float(r["High"]) for _, r in df.iterrows()]
    lows = [float(r["Low"]) for _, r in df.iterrows()]
    closes = [float(r["Close"]) for _, r in df.iterrows()]
    volumes = [int(r["Volume"]) for _, r in df.iterrows()]
    dates = [str(idx) for idx in df.index]

    leading_func = LEADING_INDICATORS.get("ALMA")
    confs = [(n, CONFIRMATION_FILTERS[n]) for n in LIGHT_CONFS if n in CONFIRMATION_FILTERS]
    threshold = 2
    sl_pct = 5.0
    tp_pct = 0.0
    buy_only = True

    trades = []
    pos = False; ep = 0; esig = ""; entry_idx = 0; entry_date = ""

    for i in range(MIN_BARS, len(closes)):
        o, h, l, c, v = opens[:i+1], highs[:i+1], lows[:i+1], closes[:i+1], volumes[:i+1]
        try:
            ld = leading_func(o, h, l, c, v)
        except Exception:
            continue
        if ld.get("direction") == "NEUTRAL":
            continue
        ld_dir = ld["direction"]

        cl, cs = 0, 0
        for _, fn in confs:
            try:
                r = fn(o, h, l, c, v)
            except Exception:
                continue
            if r.get("confirmed"):
                if r["direction"] == "LONG": cl += 1
                elif r["direction"] == "SHORT": cs += 1

        signal = "HOLD"
        price = c[-1]
        if ld_dir == "LONG" and (1 + cl) >= threshold:
            signal = "BUY"
        elif ld_dir == "SHORT" and (1 + cs) >= threshold:
            signal = "SELL"
        if buy_only and signal == "SELL":
            signal = "HOLD"

        if not pos:
            if signal == "BUY":
                pos, ep, esig, entry_idx, entry_date = True, price, "BUY", i, dates[i]
        else:
            exit_reason = None
            exit_price = price
            if sl_pct > 0:
                if price <= ep * (1 - sl_pct / 100):
                    exit_reason = "stop_loss"
            if tp_pct > 0 and not exit_reason:
                if price >= ep * (1 + tp_pct / 100):
                    exit_reason = "take_profit"
            if not exit_reason and signal == "SELL":
                exit_reason = "signal"

            if exit_reason:
                pnl = round(((exit_price - ep) / ep) * 100, 2)
                trades.append({
                    "symbol": symbol,
                    "entry_date": entry_date,
                    "exit_date": dates[i],
                    "entry_price": round(ep, 2),
                    "exit_price": round(exit_price, 2),
                    "pnl": pnl,
                    "bars": i - entry_idx,
                    "exit_reason": exit_reason,
                })
                pos = False

    if pos:
        exit_price = closes[-1]
        pnl = round(((exit_price - ep) / ep) * 100, 2)
        trades.append({
            "symbol": symbol,
            "entry_date": entry_date,
            "exit_date": dates[-1],
            "entry_price": round(ep, 2),
            "exit_price": round(exit_price, 2),
            "pnl": pnl,
            "bars": len(closes) - 1 - entry_idx,
            "exit_reason": "end",
        })
    return trades

async def send_telegram(text, parse_mode="Markdown"):
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{BASE}/sendMessage", json={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": parse_mode,
        })
        return resp.json()

async def send_long(text, parse_mode="Markdown"):
    if len(text) <= 4000:
        return await send_telegram(text, parse_mode)
    parts = []
    for i in range(0, len(text), 3900):
        parts.append(text[i:i+3900])
    for p in parts:
        await send_telegram(p, parse_mode)
        await asyncio.sleep(0.3)

async def main():
    t0 = time.time()
    await send_telegram("⏳ *ALMA-Optimized 1m Backtest*\nLoading 1m cached data & running...")

    data = load_all_1m()
    print(f"Loaded {len(data)} stocks (1m data)")

    all_trades = []
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=8) as pool:
        fut_map = {pool.submit(bt_trades_1m, sym, df): sym for sym, df in data.items()}
        for fut in as_completed(fut_map):
            try:
                r = fut.result(timeout=120)
                if r:
                    all_trades.extend(r)
            except Exception as e:
                print(f"Error: {fut_map[fut]}: {e}")
            # Progress
            if len(all_trades) % 50 == 0:
                print(f"  ... {len(all_trades)} trades from {len(set(t['symbol'] for t in all_trades))} stocks")

    print(f"Total 1m trades: {len(all_trades)}")
    all_trades.sort(key=lambda t: (t["symbol"], t["entry_date"]))

    stock_stats = {}
    for t in all_trades:
        s = t["symbol"]
        if s not in stock_stats:
            stock_stats[s] = {"trades": [], "total_return": 0}
        stock_stats[s]["trades"].append(t)
        stock_stats[s]["total_return"] += t["pnl"]

    total_return = sum(t["pnl"] for t in all_trades)
    wins = [t for t in all_trades if t["pnl"] > 0]
    losses = [t for t in all_trades if t["pnl"] <= 0]
    wr = len(wins) / len(all_trades) * 100 if all_trades else 0
    avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0

    summary = (
        f"📊 *ALMA-Optimized 1m Backtest*\n"
        f"*Config:* ALMA + Light (5) | thr=2 | SL=5% fixed | No TP\n"
        f"*Data:* 8 days 1m | *Stocks:* {len(data)}\n"
        f"*Total Trades:* {len(all_trades)}\n"
        f"*Win Rate:* {wr:.1f}% ({len(wins)}W/{len(losses)}L)\n"
        f"*Total Return:* {total_return:+.2f}%\n"
        f"*Avg Win:* {avg_win:+.2f}% | *Avg Loss:* {avg_loss:+.2f}%\n"
        f"*Time:* {time.time()-t0:.0f}s\n\n"
        f"👇 *Per-stock breakdown below*"
    )
    await send_long(summary)
    await asyncio.sleep(1)

    ranked = sorted(stock_stats.items(), key=lambda x: x[1]["total_return"], reverse=True)

    for sym, stats in ranked:
        trades = stats["trades"]
        ret = stats["total_return"]
        n = len(trades)
        nw = len([t for t in trades if t["pnl"] > 0])
        nl = n - nw
        swr = nw / n * 100 if n > 0 else 0

        lines = [f"📌 *{sym.upper()}* — {n} trades, WR {swr:.0f}%, Return {ret:+.2f}%\n"]
        for t in trades[:15]:  # max 15 trades per stock to avoid message limits
            emoji = "🟢" if t["pnl"] > 0 else "🔴"
            short_ts = t["entry_date"][:16] + "→" + t["exit_date"][:16]
            lines.append(f"{emoji} {short_ts} "
                         f"| {t['entry_price']:.1f}→{t['exit_price']:.1f} "
                         f"| *{t['pnl']:+.2f}%* ({t['exit_reason']})")
        if len(trades) > 15:
            lines.append(f"  ... +{len(trades)-15} more trades")

        msg = "\n".join(lines)
        if len(msg) > 4000:
            for i in range(0, len(msg), 3900):
                await send_telegram(msg[i:i+3900])
                await asyncio.sleep(0.2)
        else:
            await send_telegram(msg)
            await asyncio.sleep(0.2)

    top5 = ranked[:5]
    bot5 = ranked[-5:]
    final = (
        f"📊 *1m Backtest Complete*\n\n"
        f"*Top 5 Stocks:*\n"
    )
    for sym, stats in top5:
        final += f"  🟢 {sym.upper()}: {len(stats['trades'])} trades, {stats['total_return']:+.2f}%\n"
    final += f"\n*Bottom 5 Stocks:*\n"
    for sym, stats in bot5:
        final += f"  🔴 {sym.upper()}: {len(stats['trades'])} trades, {stats['total_return']:+.2f}%\n"
    await send_long(final)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(REPORT_DIR, f"alma_1m_backtest_trades_{ts}.json")
    with open(json_path, "w") as fp:
        json.dump({
            "config": {"leading": "ALMA", "confirmations": LIGHT_CONFS, "threshold": 2, "sl_pct": 5, "trailing_sl": False, "timeframe": "1m"},
            "summary": {"stocks": len(data), "total_trades": len(all_trades), "win_rate": round(wr, 1),
                        "total_return": round(total_return, 2), "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2)},
            "per_stock": {sym: {"trades": [t for t in trades], "total_return": round(stats["total_return"], 2)}
                          for sym, stats in stock_stats.items()}
        }, fp, indent=2)
    print(f"Report saved: {json_path}")
    await send_telegram(f"✅ 1m report saved to `{json_path}`")

if __name__ == "__main__":
    asyncio.run(main())
