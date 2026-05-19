from datetime import datetime, timedelta
from typing import Dict, List, Optional
import pandas as pd
from nselib import capital_market
from app.services.market_edge_service import get_fii_dii_summary, get_fii_dii_history


POLITICIAN_LINKED_ENTITIES = {
    "Adani Group": ["ADANIENT", "ADANIPORTS", "ADANIENSOL", "ADANIGREEN", "ADANITRANS", "ADANIPOWER", "ADANIGAS", "ADANICEM", "NDTV"],
    "Tata Group": ["TATASTEEL", "TATAMOTORS", "TATAPOWER", "TATACONSUM", "TATACHEM", "TATAELXSI", "TRENT", "TITAN", "TCS", "VOLTAS"],
    "Reliance Group": ["RELIANCE", "RELIANCEIND"],
    "Birla Group": ["GRASIM", "HINDALCO", "ULTRACEMCO", "PIDILITIND", "IDEA", "ABOF"],
    "Ambani Family": ["RELIANCE", "RELIANCEIND", "JIOFIN"],
    "Bajaj Group": ["BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BAJAJHLDNG"],
    "Mahindra Group": ["M&M", "M&MFIN"],
    "Wadia Group": ["BRITANNIA", "GODREJCP", "GODREJPROP"],
    "Mittal Family": ["JSWSTEEL", "JSWENERGY", "JSWINFRA"],
    "Piramal Group": ["PIRAMALPH", "PIRAMALENT", "PIRAMALFIN"],
    "D-Mart": ["AVENUE"],
    "DMart": ["AVENUE"],
}


class PoliticianTradesService:
    async def get_bulk_deals_by_group(self, period: str = "6M"):
        try:
            df = capital_market.bulk_deal_data(period=period)
            if df is None or df.empty:
                return []
            for col in ["QuantityTraded", "TradePrice/Wght.Avg.Price"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(
                        df[col].astype(str).str.replace(",", "", regex=False), errors="coerce"
                    ).fillna(0)
            df["TotalValue"] = df["QuantityTraded"] * df["TradePrice/Wght.Avg.Price"]

            result = {}
            for group_name, symbols in POLITICIAN_LINKED_ENTITIES.items():
                group_deals = df[df["Symbol"].isin(symbols)].copy()
                if group_deals.empty:
                    result[group_name] = {"count": 0, "symbols_found": []}
                    continue
                found = group_deals["Symbol"].unique().tolist()
                buys = group_deals[group_deals["Buy/Sell"].str.upper() == "BUY"]
                sells = group_deals[group_deals["Buy/Sell"].str.upper() == "SELL"]
                result[group_name] = {
                    "count": len(group_deals),
                    "symbols_found": found,
                    "buy_value": round(buys["TotalValue"].sum(), 2) if not buys.empty else 0,
                    "sell_value": round(sells["TotalValue"].sum(), 2) if not sells.empty else 0,
                    "net": round(buys["TotalValue"].sum() - sells["TotalValue"].sum(), 2),
                    "buy_count": len(buys),
                    "sell_count": len(sells),
                    "total_value": round(group_deals["TotalValue"].sum(), 2),
                    "deals": group_deals.sort_values("Date", ascending=False).head(20).to_dict(orient="records"),
                }
            return result
        except Exception as e:
            return {"error": str(e)}

    async def get_fii_flow_for_stocks(self, symbols: List[str], days: int = 30) -> Dict:
        try:
            history = get_fii_dii_history(days)
            return {"history": history, "symbols": symbols}
        except Exception as e:
            return {"error": str(e)}

    async def get_politician_dashboard(self, period: str = "6M") -> Dict:
        bulk = await self.get_bulk_deals_by_group(period)
        fii_data = await get_fii_dii_summary()
        fii_history = get_fii_dii_history(30)
        trend = "rising" if len(fii_history) >= 2 and fii_history[-1].get("fii_net", 0) > fii_history[-2].get("fii_net", 0) else "falling" if len(fii_history) >= 2 else "flat"

        return {
            "groups": bulk if isinstance(bulk, dict) else {},
            "fiidii": {
                "current": fii_data,
                "trend": {"fii_trend": trend},
                "history": fii_history,
            },
            "period": period,
            "total_groups": len(POLITICIAN_LINKED_ENTITIES),
            "timestamp": datetime.now().isoformat(),
        }

    def get_tracked_groups(self) -> Dict:
        return {k: v for k, v in POLITICIAN_LINKED_ENTITIES.items()}


politician_trades_service = PoliticianTradesService()
