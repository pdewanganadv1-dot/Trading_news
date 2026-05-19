from datetime import datetime, timedelta
from typing import Optional
import pandas as pd
from nselib import derivatives


class OptionsChainService:
    FNO_INDICES = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]

    async def get_option_chain(self, symbol: str, expiry_date: Optional[str] = None):
        symbol = symbol.upper()
        trade_date = (datetime.now() - timedelta(days=1)).strftime("%d-%m-%Y")

        try:
            bhav = derivatives.fno_bhav_copy(trade_date)
        except Exception:
            trade_date = (datetime.now() - timedelta(days=2)).strftime("%d-%m-%Y")
            try:
                bhav = derivatives.fno_bhav_copy(trade_date)
            except Exception as e:
                return {"error": f"Failed to fetch bhav copy: {e}"}

        filtered = bhav[bhav["TckrSymb"] == symbol].copy()
        if filtered.empty:
            return {"error": f"No F&O data found for {symbol}"}

        options = filtered[filtered["FinInstrmTp"].isin(["IDO", "STO"])].copy()
        if options.empty:
            return {"error": f"No options data found for {symbol}"}

        if expiry_date:
            options = options[options["XpryDt"] == expiry_date].copy()

        expiry_dates = sorted(options["XpryDt"].unique().tolist())

        if not expiry_date and expiry_dates:
            target = trade_date.replace("-", "-")
            options["_expiry_ts"] = pd.to_datetime(options["XpryDt"], format="%d-%b-%Y", errors="coerce")
            nearest = options.loc[options["_expiry_ts"].idxmax()] if options.empty else options.iloc[0]
            if not options.empty:
                future_mask = options["_expiry_ts"] >= pd.to_datetime(trade_date, format="%d-%m-%Y", errors="coerce")
                if future_mask.any():
                    nearest_idx = options[future_mask]["_expiry_ts"].idxmin()
                    expiry_date = options.loc[nearest_idx, "XpryDt"]
                else:
                    expiry_date = expiry_dates[0]
            options = options[options["XpryDt"] == expiry_date].copy()

        calls = options[options["OptnTp"] == "CE"].copy()
        puts = options[options["OptnTp"] == "PE"].copy()

        merged = pd.merge(
            calls,
            puts,
            on=["StrkPric"],
            how="outer",
            suffixes=("_CE", "_PE"),
        )

        chain_rows = []
        for _, row in merged.iterrows():
            strike = row["StrkPric"]
            chain_rows.append({
                "strike": int(strike) if pd.notna(strike) else 0,
                "ce_oi": int(row.get("OpnIntrst_CE", 0)) if pd.notna(row.get("OpnIntrst_CE")) else 0,
                "ce_chng_oi": int(row.get("ChngInOpnIntrst_CE", 0)) if pd.notna(row.get("ChngInOpnIntrst_CE")) else 0,
                "ce_volume": int(row.get("TtlTradgVol_CE", 0)) if pd.notna(row.get("TtlTradgVol_CE")) else 0,
                "ce_ltp": float(row.get("LastPric_CE", 0)) if pd.notna(row.get("LastPric_CE")) else 0,
                "ce_close": float(row.get("ClsPric_CE", 0)) if pd.notna(row.get("ClsPric_CE")) else 0,
                "pe_oi": int(row.get("OpnIntrst_PE", 0)) if pd.notna(row.get("OpnIntrst_PE")) else 0,
                "pe_chng_oi": int(row.get("ChngInOpnIntrst_PE", 0)) if pd.notna(row.get("ChngInOpnIntrst_PE")) else 0,
                "pe_volume": int(row.get("TtlTradgVol_PE", 0)) if pd.notna(row.get("TtlTradgVol_PE")) else 0,
                "pe_ltp": float(row.get("LastPric_PE", 0)) if pd.notna(row.get("LastPric_PE")) else 0,
                "pe_close": float(row.get("ClsPric_PE", 0)) if pd.notna(row.get("ClsPric_PE")) else 0,
            })

        chain_rows.sort(key=lambda x: x["strike"])

        total_ce_oi = sum(r["ce_oi"] for r in chain_rows)
        total_pe_oi = sum(r["pe_oi"] for r in chain_rows)
        pcr_oi = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi else 0

        total_ce_vol = sum(r["ce_volume"] for r in chain_rows)
        total_pe_vol = sum(r["pe_volume"] for r in chain_rows)
        pcr_vol = round(total_pe_vol / total_ce_vol, 2) if total_ce_vol else 0

        max_pain = self._compute_max_pain(chain_rows)
        key_levels = self._compute_key_levels(chain_rows)

        underlying = None
        if not options.empty:
            underlying = float(options.iloc[0].get("UndrlygPric", 0)) if pd.notna(options.iloc[0].get("UndrlygPric")) else None

        return {
            "symbol": symbol,
            "trade_date": trade_date,
            "expiry_date": expiry_date,
            "expiry_dates": expiry_dates,
            "underlying": underlying or 0,
            "pcr_oi": pcr_oi,
            "pcr_vol": pcr_vol,
            "max_pain": max_pain,
            "key_levels": key_levels,
            "total_ce_oi": total_ce_oi,
            "total_pe_oi": total_pe_oi,
            "total_ce_vol": total_ce_vol,
            "total_pe_vol": total_pe_vol,
            "chain": chain_rows,
            "is_index": symbol in self.FNO_INDICES,
        }

    def _compute_max_pain(self, chain_rows: list) -> int:
        max_pain_strike = 0
        max_pain_val = float("inf")
        for row in chain_rows:
            ce_pain = sum(
                abs(r["strike"] - row["strike"]) * r["ce_oi"]
                for r in chain_rows
                if r["strike"] > row["strike"]
            )
            pe_pain = sum(
                abs(r["strike"] - row["strike"]) * r["pe_oi"]
                for r in chain_rows
                if r["strike"] < row["strike"]
            )
            total_pain = ce_pain + pe_pain
            if total_pain < max_pain_val:
                max_pain_val = total_pain
                max_pain_strike = row["strike"]
        return max_pain_strike

    def _compute_key_levels(self, chain_rows: list) -> dict:
        sorted_rows = sorted(chain_rows, key=lambda x: x["strike"])
        top_ce_oi = max(sorted_rows, key=lambda x: x["ce_oi"]) if sorted_rows else None
        top_pe_oi = max(sorted_rows, key=lambda x: x["pe_oi"]) if sorted_rows else None
        top_ce_chng = max(sorted_rows, key=lambda x: abs(x["ce_chng_oi"])) if sorted_rows else None
        top_pe_chng = max(sorted_rows, key=lambda x: abs(x["pe_chng_oi"])) if sorted_rows else None

        return {
            "max_ce_oi": {"strike": top_ce_oi["strike"], "oi": top_ce_oi["ce_oi"]} if top_ce_oi else None,
            "max_pe_oi": {"strike": top_pe_oi["strike"], "oi": top_pe_oi["pe_oi"]} if top_pe_oi else None,
            "max_ce_chng": {"strike": top_ce_chng["strike"], "chng": top_ce_chng["ce_chng_oi"]} if top_ce_chng else None,
            "max_pe_chng": {"strike": top_pe_chng["strike"], "chng": top_pe_chng["pe_chng_oi"]} if top_pe_chng else None,
        }

    async def get_top_fo_stocks(self, limit: int = 20):
        trade_date = (datetime.now() - timedelta(days=1)).strftime("%d-%m-%Y")
        try:
            bhav = derivatives.fno_bhav_copy(trade_date)
        except Exception:
            trade_date = (datetime.now() - timedelta(days=2)).strftime("%d-%m-%Y")
            bhav = derivatives.fno_bhav_copy(trade_date)

        options = bhav[bhav["FinInstrmTp"].isin(["IDO", "STO"])].copy()
        stocks = options.groupby("TckrSymb").agg(
            total_oi=("OpnIntrst", "sum"),
            total_volume=("TtlTradgVol", "sum"),
            total_value=("TtlTrfVal", "sum"),
            ce_oi=("OpnIntrst", lambda x: x[options.loc[x.index, "OptnTp"] == "CE"].sum() if any(options.loc[x.index, "OptnTp"] == "CE") else 0),
            pe_oi=("OpnIntrst", lambda x: x[options.loc[x.index, "OptnTp"] == "PE"].sum() if any(options.loc[x.index, "OptnTp"] == "PE") else 0),
        ).reset_index()
        stocks["pcr"] = (stocks["pe_oi"] / stocks["ce_oi"].replace(0, 1)).round(2)
        stocks = stocks.sort_values("total_oi", ascending=False).head(limit)
        return stocks.to_dict(orient="records")

    async def get_pcr_summary(self):
        rows = []
        for idx in self.FNO_INDICES:
            data = await self.get_option_chain(idx)
            if data and "pcr_oi" in data:
                rows.append({
                    "symbol": idx,
                    "pcr_oi": data["pcr_oi"],
                    "pcr_vol": data["pcr_vol"],
                    "max_pain": data["max_pain"],
                    "underlying": data["underlying"],
                    "oi_difference": data["total_ce_oi"] - data["total_pe_oi"],
                })
        return rows


options_chain_service = OptionsChainService()
