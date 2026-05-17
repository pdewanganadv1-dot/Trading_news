from typing import Dict, Optional
import yfinance as yf


def get_fundamentals(ticker: str) -> Optional[Dict]:
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        if not info or info.get("regularMarketPrice") is None:
            return None
        return {
            "ticker": ticker.upper(),
            "name": info.get("longName") or info.get("shortName") or ticker.upper(),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "market_cap": info.get("marketCap"),
            "pe_ratio": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "peg_ratio": info.get("pegRatio"),
            "price_to_book": info.get("priceToBook"),
            "eps": info.get("trailingEps"),
            "dividend_yield": info.get("dividendYield"),
            "beta": info.get("beta"),
            "high_52w": info.get("fiftyTwoWeekHigh"),
            "low_52w": info.get("fiftyTwoWeekLow"),
            "price_50dma": info.get("fiftyDayAverage"),
            "price_200dma": info.get("twoHundredDayAverage"),
            "volume_avg": info.get("averageVolume"),
            "revenue": info.get("totalRevenue"),
            "ebitda": info.get("ebitda"),
            "profit_margin": info.get("profitMargins"),
            "roe": info.get("returnOnEquity"),
            "debt_to_equity": info.get("debtToEquity"),
            "current_ratio": info.get("currentRatio"),
            "free_cash_flow": info.get("freeCashflow"),
            "price": info.get("regularMarketPrice"),
            "change_pct": info.get("regularMarketChangePercent"),
            "source": "yfinance",
        }
    except Exception as e:
        return {"ticker": ticker.upper(), "error": str(e)}
