"""
Compare leading indicators: PSAR vs Speedy+ALMA vs SuperTrend
Runs 180d daily backtest on key stocks for each.
"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(__file__))
os.environ["PERSISTENT_DIR"] = os.path.join(os.path.dirname(__file__), "data")

from app.services.strategy_builder import StrategyBuilder
from app.data.stocks import INDIAN_STOCKS

INDICATORS = ["PSAR", "Speedy+ALMA", "SuperTrend"]
# Test on a diverse set of stocks (whitelist-ish)
TEST_STOCKS = ["SBIN", "MOTHERSON", "HDFCBANK", "ASIANPAINT", "HCLTECH",
               "KOTAKBANK", "LT", "MARUTI", "SUNPHARMA", "TCS",
               "RELIANCE", "INFY", "BHARTIARTL", "NTPC", "ICICIBANK",
               "TATAMOTORS", "WIPRO", "AXISBANK", "BAJFINANCE", "TITAN"]

DAYS = 180
INTERVAL = "1d"

results = {}
for ind in INDICATORS:
    print(f"\n{'='*60}")
    print(f"Testing: {ind}")
    print(f"{'='*60}")

    sb = StrategyBuilder()
    sb.select_leading(ind)
    sb.buy_only = True

    ind_results = []
    for sym in TEST_STOCKS:
        bt = sb.backtest(sym, DAYS, INTERVAL)
        if "error" not in bt and bt.get("total_trades", 0) > 0:
            ind_results.append({
                "symbol": sym,
                "trades": bt["total_trades"],
                "win_rate": bt["win_rate"],
                "avg_return": bt["avg_return"],
                "total_return": bt["total_return"],
            })
            print(f"  {sym:14s} trades={bt['total_trades']:2d}  WR={bt['win_rate']:5.1f}%  "
                  f"avg={bt['avg_return']:+6.2f}%  total={bt['total_return']:+6.2f}%")
        else:
            msg = bt.get("error", "No trades")
            print(f"  {sym:14s} SKIP ({msg})")

    results[ind] = ind_results

# Summary
print("\n\n" + "="*60)
print("SUMMARY: Leading Indicator Comparison")
print("="*60)
print(f"{'Indicator':20s} {'Stocks':>6s} {'Trades':>8s} {'Win Rate':>10s} {'Avg Ret':>10s} {'Total Ret':>10s}")
print("-"*70)
for ind in INDICATORS:
    rows = results[ind]
    total_trades = sum(r["trades"] for r in rows)
    avg_wr = sum(r["win_rate"] for r in rows) / len(rows) if rows else 0
    avg_ret = sum(r["avg_return"] for r in rows) / len(rows) if rows else 0
    total_ret = sum(r["total_return"] for r in rows)
    print(f"{ind:20s} {len(rows):>6d} {total_trades:>8d} {avg_wr:>8.1f}%  {avg_ret:>+8.2f}%  {total_ret:>+8.2f}%")

# Per-stock comparison table
print("\n\nPer-Stock Comparison:")
print(f"{'Stock':14s}", end="")
for ind in INDICATORS:
    print(f" {ind:>14s} WR", end="")
    print(f" {ind:>14s} Ret", end="")
print()
print("-" * 70)
for sym in TEST_STOCKS:
    print(f"{sym:14s}", end="")
    for ind in INDICATORS:
        row = next((r for r in results[ind] if r["symbol"] == sym), None)
        if row:
            print(f" {row['win_rate']:>5.1f}% ({row['trades']:2d})   ", end="")
            print(f" {row['total_return']:>+7.2f}%    ", end="")
        else:
            print(f" {'N/A':>14s} {'N/A':>14s}", end="")
    print()

print(f"\nDone. Tested {len(INDICATORS)} indicators on {len(TEST_STOCKS)} stocks ({DAYS}d daily)")
