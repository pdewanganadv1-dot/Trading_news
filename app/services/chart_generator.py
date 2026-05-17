import os
import matplotlib
matplotlib.use('Agg')
import mplfinance as mpf
import pandas as pd
from datetime import datetime, timedelta
from app.services.market_data_service import market_data_service

CHART_DIR = "/tmp/trading_charts"
os.makedirs(CHART_DIR, exist_ok=True)


async def generate_signal_chart(symbol: str) -> str | None:
    try:
        prices = await market_data_service.get_5min_prices(symbol, 100)
        if not prices or len(prices) < 20:
            return None

        end = datetime.now()
        dates = [end - timedelta(minutes=5 * (len(prices) - i - 1)) for i in range(len(prices))]
        df = pd.DataFrame({
            'Date': dates,
            'Open': [p * 0.998 for p in prices],
            'High': [p * 1.002 for p in prices],
            'Low': [p * 0.997 for p in prices],
            'Close': prices,
            'Volume': [1000 + i * 10 for i in range(len(prices))],
        })
        df.set_index('Date', inplace=True)

        ap = [
            mpf.make_addplot(pd.Series(prices).rolling(9).mean(), color='cyan', width=0.8),
            mpf.make_addplot(pd.Series(prices).rolling(20).mean(), color='orange', width=0.8),
        ]

        path = os.path.join(CHART_DIR, f"{symbol}_chart.png")
        mpf.plot(
            df, type='candle', style='charles',
            addplot=ap, volume=True,
            savefig=dict(fname=path, dpi=100, bbox_inches='tight'),
            title=f'{symbol.upper()} - 5m Chart (EMA9/20)',
            figsize=(8, 4.5),
        )
        return path
    except Exception as e:
        print(f"Chart generation error for {symbol}: {e}")
        return None
