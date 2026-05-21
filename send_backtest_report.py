"""Send backtest report to Telegram."""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(__file__))
os.environ["PERSISTENT_DIR"] = os.path.join(os.path.dirname(__file__), "data")

from app.config import settings
import httpx

TOKEN = settings.telegram_bot_token
CHAT_ID = settings.telegram_chat_id
BASE = f"https://api.telegram.org/bot{TOKEN}"

REPORT_DIR = os.path.join(os.path.dirname(__file__), "data", "backtest_reports")

# Find latest files
reports = sorted([f for f in os.listdir(REPORT_DIR) if f.endswith(".md")], reverse=True)
csvs = sorted([f for f in os.listdir(REPORT_DIR) if f.endswith(".csv")], reverse=True)
summaries = sorted([f for f in os.listdir(REPORT_DIR) if f.endswith(".json")], reverse=True)

if not summaries:
    print("No summary found")
    sys.exit(1)

summary_path = os.path.join(REPORT_DIR, summaries[0])
with open(summary_path) as f:
    summary = json.load(f)

csv_path = os.path.join(REPORT_DIR, csvs[0]) if csvs else None

# Build Telegram message — rank by 5D win rate
def wr(st):
    c = st.get("count_5d", 0)
    return (st["wins_5d"] / c * 100) if c > 0 else 0

top5 = sorted(
    [(n, s) for n, s in summary["indicator_stats"].items() if s.get("count_5d", 0) >= 3],
    key=lambda x: wr(x[1]),
    reverse=True
)[:5]

msg_lines = [
    f"📊 *30-Day Backtest — Accuracy Report*",
    f"",
    f"**Indicators**: {summary['indicators']} | **Stocks**: {summary['stocks']}",
    f"**Total signals**: {summary['total_signals']}",
    f"**Duration**: {summary['duration_seconds']:.0f}s",
    f"",
    f"*Top 5 by 5-Day Win Rate:*",
]

for name, st in top5:
    wr5 = f"{wr(st):.0f}%"
    ap5 = f"{st['avg_pnl_5d']:+.2f}%"
    msg_lines.append(f"  • *{name}* — {wr5} win rate, {ap5} avg, {st['buys']}B/{st['sells']}S on {st['stocks']} stocks")

msg_lines.append("")
msg_lines.append("_Daily timeframe | Confirmations: EMA20, EMA50, MACD, RSI, Vol, PA_")

msg = "\n".join(msg_lines)

def send_msg(text):
    r = httpx.post(f"{BASE}/sendMessage", json={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }, timeout=15)
    print(f"Message sent: {r.status_code}")

def send_doc(file_path):
    with open(file_path, "rb") as f:
        r = httpx.post(f"{BASE}/sendDocument", data={
            "chat_id": CHAT_ID,
        }, files={"document": (os.path.basename(file_path), f)}, timeout=30)
    print(f"Document sent: {r.status_code}")

# Send summary message
send_msg(msg)

# Send CSV
if csv_path:
    send_doc(csv_path)

print("\nDone!")
