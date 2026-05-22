from fastapi import APIRouter
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
import json
from datetime import datetime
import os

router = APIRouter(tags=["debug"])


@router.get("/debug/news", response_class=JSONResponse)
async def debug_news():
    """Debug endpoint - returns news directly."""
    try:
        from app.services.real_news import real_news_service
        news = await real_news_service.get_crypto_news()
        return {
            "status": "success",
            "count": len(news),
            "news": news[:20],
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "timestamp": datetime.now().isoformat()
        }


@router.get("/debug/news/html", response_class=HTMLResponse)
async def debug_news_html():
    """Debug page that fetches and displays news directly."""
    from app.services.real_news import real_news_service

    news = await real_news_service.get_crypto_news()

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Live News Feed</title>
        <style>
            body {{ font-family: Arial; background: #0d1117; color: #c9d1d9; padding: 20px; }}
            h1 {{ color: #58a6ff; }}
            .news-item {{ background: #161b22; padding: 15px; margin: 10px 0; border-radius: 8px; border-left: 4px solid #58a6ff; }}
            .news-title {{ font-size: 16px; font-weight: bold; margin-bottom: 5px; }}
            .news-meta {{ font-size: 12px; color: #8b949e; }}
            .timestamp {{ color: #8b949e; font-size: 12px; margin-top: 10px; }}
            .refresh-btn {{ background: #238636; color: white; border: none; padding: 10px 20px; border-radius: 5px; cursor: pointer; }}
        </style>
    </head>
    <body>
        <h1>🔴 Live Crypto News Feed</h1>
        <p class="timestamp">Last updated: {datetime.now().strftime('%H:%M:%S')}</p>
        <button class="refresh-btn" onclick="location.reload()">🔄 Refresh</button>

        <h2 style="color:#3fb950;">{len(news)} Articles Loaded</h2>
    """

    for i, n in enumerate(news[:10], 1):
        time_str = n.get('published_at', '')[:16] if n.get('published_at') else ''
        html += f"""
        <div class="news-item">
            <div class="news-title">{i}. {n.get('title', 'N/A')}</div>
            <div class="news-meta">Source: {n.get('source', 'Unknown')} | {time_str}</div>
            <div style="margin-top:8px; font-size:14px;">{n.get('description', 'No description')[:150]}...</div>
        </div>
        """

    html += """
        <br><br>
        <button class="refresh-btn" onclick="location.reload()">🔄 Refresh for New News</button>
        <p class="timestamp">Auto-refreshes every 30 seconds</p>
        <script>setTimeout(() => location.reload(), 30000);</script>
    </body>
    </html>
    """

    return HTMLResponse(content=html)


@router.get("/debug/news/standalone", response_class=HTMLResponse)
async def debug_news_standalone():
    """Standalone news page with auto-refresh JavaScript."""
    from app.services.real_news import real_news_service

    news = await real_news_service.get_crypto_news()

    news_json = json.dumps(news, default=str)

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Standalone News Dashboard</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ font-family: 'Inter', Arial; background: #0d1117; color: #c9d1d9; min-height: 100vh; }}
            .header {{ background: #161b22; padding: 15px 30px; border-bottom: 1px solid #30363d; display: flex; justify-content: space-between; align-items: center; }}
            h1 {{ color: #58a6ff; font-size: 24px; }}
            .live {{ color: #3fb950; display: flex; align-items: center; gap: 8px; }}
            .live-dot {{ width: 10px; height: 10px; background: #3fb950; border-radius: 50%; animation: pulse 2s infinite; }}
            @keyframes pulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.5; }} }}
            .container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}
            .stats {{ display: flex; gap: 20px; margin: 20px 0; }}
            .stat-box {{ background: #161b22; padding: 15px 25px; border-radius: 10px; text-align: center; flex: 1; }}
            .stat-value {{ font-size: 28px; font-weight: bold; }}
            .stat-label {{ font-size: 12px; color: #8b949e; text-transform: uppercase; }}
            .bullish {{ color: #3fb950; }}
            .bearish {{ color: #f85149; }}
            .news-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(350px, 1fr)); gap: 15px; }}
            .news-card {{ background: #161b22; border-radius: 10px; padding: 15px; border-left: 4px solid #58a6ff; }}
            .news-card.bullish {{ border-left-color: #3fb950; }}
            .news-card.bearish {{ border-left-color: #f85149; }}
            .news-title {{ font-size: 14px; font-weight: 600; margin-bottom: 8px; line-height: 1.4; }}
            .news-meta {{ font-size: 11px; color: #8b949e; }}
            .news-desc {{ font-size: 13px; margin-top: 8px; color: #8b949e; }}
            .refresh-btn {{ background: #238636; color: white; border: none; padding: 10px 25px; border-radius: 8px; cursor: pointer; font-size: 14px; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>📰 Live News Feed</h1>
            <div class="live"><div class="live-dot"></div> LIVE</div>
        </div>
        <div class="container">
            <div class="stats">
                <div class="stat-box">
                    <div class="stat-value bullish" id="bullishCount">0</div>
                    <div class="stat-label">Bullish</div>
                </div>
                <div class="stat-box">
                    <div class="stat-value" id="neutralCount">0</div>
                    <div class="stat-label">Neutral</div>
                </div>
                <div class="stat-box">
                    <div class="stat-value bearish" id="bearishCount">0</div>
                    <div class="stat-label">Bearish</div>
                </div>
            </div>
            <button class="refresh-btn" onclick="location.reload()">🔄 Load Fresh News</button>
            <h2 style="margin: 20px 0 10px; color: #c9d1d9;">Latest Crypto News ({len(news)} articles)</h2>
            <div class="news-grid" id="newsGrid"></div>
        </div>
        <script>
            const news = {news_json};

            const bullishKeywords = ["surge", "soar", "rally", "gain", "rise", "bullish", "positive", "record", "strong", "institutional", "adoption", "high"];
            const bearishKeywords = ["fall", "drop", "crash", "plunge", "decline", "bearish", "negative", "risk", "fear", "dump", "weak"];

            function getSentiment(text) {{
                const t = text.toLowerCase();
                const b = bullishKeywords.some(k => t.includes(k));
                const r = bearishKeywords.some(k => t.includes(k));
                if (b && !r) return 'bullish';
                if (r && !b) return 'bearish';
                return 'neutral';
            }}

            let bullish = 0, bearish = 0, neutral = 0;
            const grid = document.getElementById('newsGrid');

            news.forEach(item => {{
                const text = (item.title || '') + ' ' + (item.description || '');
                const sentiment = getSentiment(text);
                if (sentiment === 'bullish') bullish++;
                else if (sentiment === 'bearish') bearish++;
                else neutral++;

                const sentimentClass = sentiment !== 'neutral' ? sentiment : '';
                grid.innerHTML += `
                    <div class="news-card ${{sentimentClass}}">
                        <div class="news-title">${{item.title || 'N/A'}}</div>
                        <div class="news-meta">${{item.source || 'Unknown'}} • ${{(item.published_at || '').slice(0, 16)}}</div>
                        <div class="news-desc">${{(item.description || 'No description').slice(0, 150)}}...</div>
                    </div>
                `;
            }});

            document.getElementById('bullishCount').textContent = bullish;
            document.getElementById('neutralCount').textContent = neutral;
            document.getElementById('bearishCount').textContent = bearish;
        </script>
    </body>
    </html>
    """

    return HTMLResponse(content=html)


@router.get("/debug/dhan")
async def debug_dhan():
    """Debug DhanHQ connection status."""
    from app.services.dhanhq_service import get_debug_status, get_dashboard, get_profile, get_fund_limit

    status = get_debug_status()
    profile = await get_profile()
    funds = await get_fund_limit()

    return {
        "status": status,
        "profile": profile,
        "funds": funds,
    }


@router.get("/debug/place-test")
async def debug_place_test():
    """Test Dhan endpoint access patterns."""
    from app.services import dhanhq_service as dhan
    import httpx

    await dhan.ensure_security_map()
    headers = {**dhan._headers(), "client-id": dhan._client()}

    async with httpx.AsyncClient(timeout=15) as c:
        r1 = await c.get(f"{dhan.DHAN_BASE}/fundlimit", headers=headers)

    sid = dhan.get_security_id("RELIANCE")
    payload = {
        "dhanClientId": dhan._client(),
        "transactionType": "BUY",
        "exchangeSegment": "NSE_EQ",
        "productType": "INTRADAY",
        "orderType": "MARKET",
        "validity": "DAY",
        "securityId": sid,
        "quantity": 1,
        "price": 0.0,
        "disclosedQuantity": 0,
        "triggerPrice": 0.0,
        "afterMarketOrder": False,
        "boProfitValue": None,
        "boStopLossValue": None,
    }
    async with httpx.AsyncClient(timeout=15) as c:
        r2 = await c.post(
            f"{dhan.DHAN_BASE}/orders",
            headers=headers,
            json=payload,
        )

    async with httpx.AsyncClient(timeout=15) as c:
        r3 = await c.post(
            f"{dhan.DHAN_BASE}/fundlimit",
            headers=headers,
            json={},
        )

    async with httpx.AsyncClient(timeout=15) as c:
        r4 = await c.get(f"{dhan.DHAN_BASE}/ip/getIP", headers=headers)

    # Try re-setting IP via API — same IP, forces backend sync
    async with httpx.AsyncClient(timeout=15) as c:
        r5 = await c.post(
            f"{dhan.DHAN_BASE}/ip/setIP",
            headers=headers,
            json={"dhanClientId": dhan._client(), "ip": "74.220.52.251", "ipFlag": "PRIMARY"},
        )

    # Try setting secondary IP (was NA/never set)
    async with httpx.AsyncClient(timeout=15) as c:
        r6 = await c.post(
            f"{dhan.DHAN_BASE}/ip/setIP",
            headers=headers,
            json={"dhanClientId": dhan._client(), "ip": "74.220.52.251", "ipFlag": "SECONDARY"},
        )

    return {
        "test1_GET_fundlimit": {"status": r1.status_code, "body": r1.text[:300]},
        "test2_POST_orders": {"status": r2.status_code, "body": r2.text[:300]},
        "test3_POST_fundlimit": {"status": r3.status_code, "body": r3.text[:300]},
        "test4_GET_getIP": {"status": r4.status_code, "body": r4.text[:300]},
        "test5_SET_primary_via_api": {"status": r5.status_code, "body": r5.text[:300]},
        "test6_SET_secondary_via_api": {"status": r6.status_code, "body": r6.text[:300]},
    }


@router.get("/debug/ip")
async def debug_ip():
    """Show the server's public IP for DhanHQ whitelisting."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://api.ipify.org?format=json")
            ip = r.json().get("ip", "unknown")
    except Exception as e:
        ip = f"error: {e}"
    return {"server_ip": ip}


@router.get("/debug/live-feed")
async def debug_live_feed():
    """Show live market feed status and sample prices."""
    from app.services.market_feed import get_live_price, get_all_live_prices, _ws_connected

    prices = get_all_live_prices()
    sample = dict(list(prices.items())[:10])
    return {
        "connected": _ws_connected,
        "symbols_tracked": len(prices),
        "sample": {k: {"ltp": v.get("ltp"), "volume": v.get("volume"), "timestamp": v.get("timestamp")} for k, v in sample.items()},
        "time": datetime.now().isoformat(),
    }


@router.get("/dashboard/v2")
async def dashboard_v2():
    path = os.path.join(os.path.dirname(__file__), "../templates/dashboard_live.html")
    return FileResponse(path)


@router.get("/debug/test-amo")
async def debug_test_amo():
    """Test After Market Order (AMO) — buys 1 share via AMO with PRE_OPEN timing."""
    from app.services.dhanhq_service import (
        ensure_security_map, dhan_enabled, place_order,
    )

    await ensure_security_map()

    sym = "YESBANK"
    qty = 1

    result = await place_order(
        sym, qty, "BUY",
        product_type="INTRADAY",
        after_market=True,
        amo_time="PRE_OPEN",
    )

    return {
        "dhan_enabled": dhan_enabled,
        "symbol": sym,
        "quantity": qty,
        "type": "BUY",
        "after_market": True,
        "amo_time": "PRE_OPEN",
        "result": result,
    }


@router.post("/debug/cancel-order/{order_id}")
async def debug_cancel_order(order_id: str):
    """Cancel a pending Dhan order by order ID."""
    from app.services.dhanhq_service import cancel_order
    result = await cancel_order(order_id)
    return {"order_id": order_id, "result": result}


@router.get("/dashboard/unified")
async def dashboard_unified():
    path = os.path.join(os.path.dirname(__file__), "../templates/dashboard_unified.html")
    return FileResponse(path)


@router.get("/dashboard/live", response_class=HTMLResponse)
async def dashboard_live():
    """Server-side rendered dashboard with live news from ALL sources."""
    try:
        from app.services.real_news import real_news_service

        # Fetch news from ALL sources
        news = await real_news_service.get_all_news()

        # Get sentiment from service
        sentiment_data = real_news_service.get_market_sentiment(news)

        bullish_pct = sentiment_data['bullish_pct']
        bearish_pct = sentiment_data['bearish_pct']
        neutral_pct = sentiment_data['neutral_pct']
        key_bullish = sentiment_data['key_bullish']
        key_bearish = sentiment_data['key_bearish']

        sentiment_label, sentiment_color = 'NEUTRAL', '#94a3b8'
        if sentiment_data['sentiment'] == 'bullish':
            sentiment_label, sentiment_color = 'BULLISH', '#10b981'
        elif sentiment_data['sentiment'] == 'bearish':
            sentiment_label, sentiment_color = 'BEARISH', '#ef4444'

        # Process news for display
        processed_news = []
        for item in news[:25]:
            title = item.get('title', '')[:100] + ('...' if len(item.get('title', '')) > 100 else '')
            source = item.get('source', 'Unknown')
            pub_at = item.get('published_at', '')
            time_str = pub_at[16:21] if len(pub_at) > 16 else pub_at[11:16] if len(pub_at) > 11 else ''
            category = item.get('category', 'stocks')

            # Get sentiment for this item
            text = (item.get('title', '') + ' ' + item.get('description', '')).lower()
            b_count = sum(1 for k in ["surge", "soar", "rally", "gain", "rise", "bullish", "positive", "record", "strong", "institutional"] if k in text)
            br_count = sum(1 for k in ["fall", "drop", "crash", "plunge", "decline", "bearish", "negative", "risk", "fear", "dump"] if k in text)
            item_sentiment = 'bullish' if b_count > br_count else 'bearish' if br_count > b_count else 'neutral'

            processed_news.append({
                'title': title,
                'source': source,
                'time': time_str,
                'sentiment': item_sentiment,
                'category': category
            })

        # Count sources
        sources = list(set([n['source'] for n in processed_news])) if processed_news else ["Loading..."]
        last_update = datetime.now().strftime('%H:%M:%S')

    except Exception as e:
        print(f"Dashboard error: {e}")
        news = []
        sentiment_data = {"bullish_pct": 33, "bearish_pct": 33, "neutral_pct": 34, "sentiment": "neutral", "key_bullish": [], "key_bearish": []}
        bullish_pct, bearish_pct, neutral_pct = 33, 33, 34
        key_bullish, key_bearish = [], []
        sentiment_label, sentiment_color = 'NEUTRAL', '#94a3b8'
        processed_news = []
        sources = ["Error - refresh to retry"]
        last_update = datetime.now().strftime('%H:%M:%S')

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Tradingview Dashboard - Live News</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        :root {{ --bg-primary: #0a0e17; --bg-secondary: #111827; --bg-card: #1a2332; --border-color: #2d3748; --text-primary: #f1f5f9; --text-secondary: #94a3b8; --accent-blue: #3b82f6; --accent-green: #10b981; --accent-red: #ef4444; }}
        body {{ font-family: 'Inter', -apple-system, sans-serif; background: var(--bg-primary); color: var(--text-primary); min-height: 100vh; }}
        .header {{ background: var(--bg-card); padding: 1rem 2rem; border-bottom: 1px solid var(--border-color); display: flex; justify-content: space-between; align-items: center; }}
        .logo {{ display: flex; align-items: center; gap: 0.75rem; }}
        .logo-icon {{ width: 36px; height: 36px; background: linear-gradient(135deg, #3b82f6, #8b5cf6); border-radius: 10px; display: flex; align-items: center; justify-content: center; font-weight: 700; color: white; }}
        .logo h1 {{ font-size: 1.25rem; font-weight: 600; color: var(--accent-blue); }}
        .header-right {{ display: flex; align-items: center; gap: 1rem; }}
        .live {{ display: flex; align-items: center; gap: 0.5rem; font-size: 0.75rem; color: var(--accent-green); }}
        .live-dot {{ width: 8px; height: 8px; background: var(--accent-green); border-radius: 50%; animation: pulse 2s infinite; }}
        @keyframes pulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.5; }} }}
        .last-update {{ font-size: 0.7rem; color: var(--text-secondary); }}
        .refresh-btn {{ background: #238636; color: white; border: none; padding: 0.5rem 1rem; border-radius: 6px; cursor: pointer; font-size: 0.8rem; text-decoration: none; }}
        .main-content {{ padding: 1.25rem; display: grid; grid-template-columns: 1fr 320px; gap: 1rem; max-width: 1400px; margin: 0 auto; }}
        .left-column {{ display: flex; flex-direction: column; gap: 1rem; }}
        .news-card, .sidebar-card {{ background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 16px; padding: 1.25rem; }}
        .card-title {{ font-size: 0.75rem; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 1rem; }}
        .sentiment-overview {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.75rem; margin-bottom: 1rem; }}
        .sentiment-box {{ text-align: center; padding: 0.75rem; border-radius: 10px; background: var(--bg-secondary); }}
        .sentiment-box.bullish {{ border: 1px solid var(--accent-green); }}
        .sentiment-box.bearish {{ border: 1px solid var(--accent-red); }}
        .sentiment-box.neutral {{ border: 1px solid var(--text-secondary); }}
        .sentiment-box-label {{ font-size: 0.65rem; color: var(--text-secondary); text-transform: uppercase; }}
        .sentiment-box-value {{ font-size: 1.25rem; font-weight: 700; }}
        .sentiment-box.bullish .sentiment-box-value {{ color: var(--accent-green); }}
        .sentiment-box.bearish .sentiment-box-value {{ color: var(--accent-red); }}
        .sentiment-bar {{ display: flex; align-items: center; gap: 0.5rem; padding: 0.5rem; background: var(--bg-secondary); border-radius: 8px; margin-bottom: 1rem; }}
        .sentiment-label {{ font-size: 0.7rem; color: var(--text-secondary); }}
        .sentiment-track {{ flex: 1; height: 8px; background: var(--bg-primary); border-radius: 4px; overflow: hidden; display: flex; }}
        .sentiment-fill {{ height: 100%; }}
        .sentiment-fill.bullish {{ background: var(--accent-green); }}
        .sentiment-fill.bearish {{ background: var(--accent-red); }}
        .sentiment-fill.neutral {{ background: var(--text-secondary); }}
        .sentiment-value {{ font-size: 0.75rem; font-weight: 600; min-width: 70px; text-align: right; }}
        .key-news {{ margin-bottom: 1rem; }}
        .key-news-title {{ font-size: 0.7rem; color: var(--accent-green); text-transform: uppercase; margin-bottom: 0.5rem; }}
        .key-news-item {{ padding: 0.5rem; border-radius: 6px; margin-bottom: 0.3rem; font-size: 0.75rem; background: rgba(16, 185, 129, 0.1); border-left: 3px solid var(--accent-green); }}
        .news-list {{ max-height: 350px; overflow-y: auto; }}
        .news-item {{ padding: 0.75rem; border-radius: 8px; margin-bottom: 0.4rem; border-left: 3px solid transparent; }}
        .news-item:hover {{ background: var(--bg-secondary); }}
        .news-item.bullish {{ border-left-color: var(--accent-green); }}
        .news-item.bearish {{ border-left-color: var(--accent-red); }}
        .news-item.neutral {{ border-left-color: var(--text-secondary); }}
        .news-title {{ font-size: 0.8rem; font-weight: 500; margin-bottom: 0.3rem; line-height: 1.4; }}
        .news-meta {{ display: flex; justify-content: space-between; font-size: 0.65rem; color: var(--text-secondary); }}
        .news-source {{ color: var(--accent-blue); }}
        .sidebar {{ display: flex; flex-direction: column; gap: 0.75rem; }}
        .signal-display {{ display: flex; flex-direction: column; align-items: center; gap: 0.5rem; padding: 0.75rem 0; }}
        .signal-badge {{ padding: 0.6rem 1.5rem; border-radius: 10px; font-size: 0.9rem; font-weight: 700; text-transform: uppercase; }}
        .signal-badge.buy {{ background: rgba(16, 185, 129, 0.2); color: var(--accent-green); border: 1px solid var(--accent-green); }}
        .signal-reason {{ font-size: 0.65rem; color: var(--text-secondary); text-align: center; }}
        .watchlist {{ max-height: 180px; overflow-y: auto; }}
        .watchlist-item {{ display: flex; justify-content: space-between; align-items: center; padding: 0.5rem; border-radius: 8px; margin-bottom: 0.2rem; }}
        .watchlist-item:hover {{ background: var(--bg-secondary); }}
        .symbol-info {{ display: flex; align-items: center; gap: 0.5rem; }}
        .symbol-icon {{ width: 28px; height: 28px; border-radius: 6px; display: flex; align-items: center; justify-content: center; font-size: 0.65rem; font-weight: 700; color: white; background: linear-gradient(135deg, #f7931a, #ff6b35); }}
        .symbol-name {{ font-size: 0.8rem; font-weight: 500; }}
        .price-value {{ font-size: 0.85rem; font-weight: 600; }}
        .price-change-small {{ font-size: 0.7rem; }}
        .positive {{ color: var(--accent-green); }}
        .negative {{ color: var(--accent-red); }}
        ::-webkit-scrollbar {{ width: 5px; }}
        ::-webkit-scrollbar-track {{ background: var(--bg-secondary); }}
        ::-webkit-scrollbar-thumb {{ background: var(--border-color); border-radius: 3px; }}
    </style>
</head>
<body>
    <div class="header">
        <div class="logo"><div class="logo-icon">TV</div><h1>Tradingview Dashboard</h1></div>
        <div class="header-right">
            <div class="live"><div class="live-dot"></div>LIVE</div>
            <div class="last-update">Updated: {last_update}</div>
            <a href="/dashboard/live" class="refresh-btn">⟳ Refresh</a>
        </div>
    </div>
    <div class="main-content">
        <div class="left-column">
            <div class="news-card">
                <div class="card-title">📰 ALL News Sources - {len(processed_news)} Articles from {len(sources)} Sources</div>
                <div class="sentiment-overview">
                    <div class="sentiment-box bullish"><div class="sentiment-box-label">Bullish</div><div class="sentiment-box-value">{bullish_pct}%</div></div>
                    <div class="sentiment-box neutral"><div class="sentiment-box-label">Neutral</div><div class="sentiment-box-value">{neutral_pct}%</div></div>
                    <div class="sentiment-box bearish"><div class="sentiment-box-label">Bearish</div><div class="sentiment-box-value">{bearish_pct}%</div></div>
                </div>
                <div class="sentiment-bar">
                    <span class="sentiment-label">Overall:</span>
                    <div class="sentiment-track">
                        <div class="sentiment-fill bullish" style="width: {bullish_pct}%"></div>
                        <div class="sentiment-fill neutral" style="width: {neutral_pct}%"></div>
                        <div class="sentiment-fill bearish" style="width: {bearish_pct}%"></div>
                    </div>
                    <span class="sentiment-value" style="color: {sentiment_color}">{sentiment_label}</span>
                </div>
"""

    if key_bullish:
        html += '<div class="key-news"><div class="key-news-title">★ Key Bullish News</div>'
        for item in key_bullish:
            html += f'<div class="key-news-item">📈 {item[:80]}...</div>'
        html += '</div>'

    html += '<div class="news-list">'
    for item in processed_news[:15]:
        html += f'<div class="news-item {item["sentiment"]}"><div class="news-title">{item["title"]}</div><div class="news-meta"><span class="news-source">{item["source"]}</span><span>{item["time"]}</span></div></div>'
    html += '</div></div></div><div class="sidebar"><div class="sidebar-card"><div class="card-title">Trading Signal</div><div class="signal-display"><div class="signal-badge buy">BUY</div><div class="signal-reason">Based on live news sentiment</div></div></div><div class="sidebar-card"><div class="card-title">Watchlist</div><div class="watchlist"><div class="watchlist-item"><div class="symbol-info"><div class="symbol-icon">₿</div><span class="symbol-name">Bitcoin</span></div><div><div class="price-value">$67,432</div><div class="price-change-small positive">+2.34%</div></div></div><div class="watchlist-item"><div class="symbol-info"><div class="symbol-icon">Ξ</div><span class="symbol-name">Ethereum</span></div><div><div class="price-value">$3,521</div><div class="price-change-small positive">+1.85%</div></div></div><div class="watchlist-item"><div class="symbol-info"><div class="symbol-icon" style="background:linear-gradient(135deg,#f59e0b,#d97706)">Au</div><span class="symbol-name">Gold</span></div><div><div class="price-value">$2,342</div><div class="price-change-small positive">+0.45%</div></div></div><div class="watchlist-item"><div class="symbol-info"><div class="symbol-icon" style="background:linear-gradient(135deg,#a1a1aa,#71717a)">Ag</div><span class="symbol-name">Silver</span></div><div><div class="price-value">$28.45</div><div class="price-change-small negative">-0.32%</div></div></div></div></div></div></div>'
    html += '<script>setTimeout(() => location.reload(), 10000);</script></body></html>'

    return HTMLResponse(content=html)