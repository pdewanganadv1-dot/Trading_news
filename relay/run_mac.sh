#!/bin/sh
# runs the relay on your Mac and exposes it with cloudflared (free, no account needed for a quick tunnel)
# 1) brew install cloudflared   2) cp .env.example .env && edit   3) ./run_mac.sh
set -e
cd "$(dirname "$0")"
python3 -m venv .venv 2>/dev/null || true
. .venv/bin/activate
pip install -q -r requirements.txt
set -a; . ./.env; set +a
uvicorn relay:app --host 127.0.0.1 --port "${PORT:-8080}" &
sleep 2
echo "relay on http://127.0.0.1:${PORT:-8080}/health  - now opening the tunnel; use the https URL it prints + /tv/$RELAY_TOKEN in TradingView"
cloudflared tunnel --url "http://127.0.0.1:${PORT:-8080}"
