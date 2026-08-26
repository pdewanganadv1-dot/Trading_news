"""Static configuration: expiry-weekday regimes, contract specs, CAS timings.

Everything here is a *fact about the market* that the rest of the toolkit reads.
Update the REGIMES table when an exchange changes an expiry day.
"""
from __future__ import annotations

import datetime as dt

# ----------------------------------------------------------------------------
# Session clock (IST). Index data has one bar per minute from 09:15 to 15:29.
# ----------------------------------------------------------------------------
SESSION_OPEN = dt.time(9, 15)
SESSION_LAST_BAR = dt.time(15, 29)          # last 1-min bar of the cash session
SESSION_MINUTES = 375                        # 09:15 .. 15:29 inclusive

# Closing Auction Session (live from 2026-08-03 for F&O-eligible stocks, NSE & BSE)
CAS_START_DATE = dt.date(2026, 8, 3)
CAS_TIMELINE = {
    "reference_vwap_window": ("15:00", "15:15"),   # reference price = VWAP of 15:00-15:15
    "continuous_trading_ends": "15:15",             # F&O stocks stop continuous trading
    "transition": ("15:15", "15:20"),
    "order_entry_1": ("15:20", "15:25"),            # limit + market orders
    "order_entry_2": ("15:25", "15:30"),            # limit only; random close 15:28-15:30
    "matching": ("15:30", "15:35"),                 # single equilibrium price per stock
    "index_close_known": "15:35",                   # closing index published ~15:35
    "fo_close": "15:40",                            # index & stock derivatives trade till 15:40
    "price_band_pct": 3.0,                          # +/-3% around reference price
}

# ----------------------------------------------------------------------------
# Weekly-expiry weekday regimes. weekday: Mon=0 .. Fri=4. None = monthly only.
# Each tuple: (start_date, end_date_exclusive_or_None, weekday, note)
# ----------------------------------------------------------------------------
REGIMES = {
    "NIFTY": [
        (dt.date(2019, 2, 11), dt.date(2025, 9, 1), 3, "weekly Thursday (weekly options launched 11-Feb-2019)"),
        (dt.date(2025, 9, 1), None, 1, "weekly Tuesday (NSE/BSE expiry swap, 1-Sep-2025)"),
    ],
    "SENSEX": [
        (dt.date(2023, 5, 15), dt.date(2025, 1, 1), 4, "weekly Friday (relaunch 15-May-2023)"),
        (dt.date(2025, 1, 1), dt.date(2025, 9, 1), 1, "weekly Tuesday"),
        (dt.date(2025, 9, 1), None, 3, "weekly Thursday (from 1-Sep-2025)"),
    ],
    "BANKNIFTY": [
        (dt.date(2016, 5, 27), dt.date(2023, 9, 6), 3, "weekly Thursday"),
        (dt.date(2023, 9, 6), dt.date(2024, 11, 20), 2, "weekly Wednesday"),
        # weekly discontinued 20-Nov-2024 (SEBI: one weekly expiry per exchange). Monthly only after.
        (dt.date(2024, 11, 20), dt.date(2025, 9, 1), None, "monthly only (last Wednesday)"),
        (dt.date(2025, 9, 1), None, None, "monthly only (last Tuesday)"),
    ],
}

# Monthly expiry weekday (last such weekday of the month) per index/regime is the
# same weekday as the weekly one; for monthly-only regimes we specify explicitly.
MONTHLY_WEEKDAY = {
    "NIFTY": [(dt.date(2000, 1, 1), dt.date(2025, 9, 1), 3), (dt.date(2025, 9, 1), None, 1)],
    "SENSEX": [(dt.date(2000, 1, 1), dt.date(2023, 5, 15), 3), (dt.date(2023, 5, 15), dt.date(2025, 1, 1), 4),
               (dt.date(2025, 1, 1), dt.date(2025, 9, 1), 1), (dt.date(2025, 9, 1), None, 3)],
    "BANKNIFTY": [(dt.date(2000, 1, 1), dt.date(2023, 9, 6), 3), (dt.date(2023, 9, 6), dt.date(2025, 9, 1), 2),
                  (dt.date(2025, 9, 1), None, 1)],
}

# Windows in which the weekly contract was liquid enough for expiry-day statistics
LIQUID_WEEKLY_FROM = {
    "NIFTY": dt.date(2019, 2, 11),
    "SENSEX": dt.date(2023, 5, 15),
    "BANKNIFTY": dt.date(2016, 5, 27),
}
LIQUID_WEEKLY_TO = {"NIFTY": None, "SENSEX": None, "BANKNIFTY": dt.date(2024, 11, 20)}

# Contract specs (Aug 2026)
CONTRACT = {
    "NIFTY": {"strike_step": 50, "lot": 65, "exchange": "NSE", "tick": 0.05},
    "SENSEX": {"strike_step": 100, "lot": 20, "exchange": "BSE", "tick": 0.05},
    "BANKNIFTY": {"strike_step": 100, "lot": 30, "exchange": "NSE", "tick": 0.05},
}

# Cost model (per option unit, in index points): half-spread + brokerage/taxes.
# ATM NIFTY weekly options usually quote 0.05-0.50 wide; we assume 0.5 pt per side
# all-in for NIFTY, 1.0 for SENSEX (wider spreads), 1.0 for BANKNIFTY.
COST_PER_SIDE = {"NIFTY": 0.5, "SENSEX": 1.0, "BANKNIFTY": 1.0}

# Option-model defaults
VRP_MULTIPLIER = 1.10       # implied vol ~ 10% above trailing realised (typical VRP)
RV_LOOKBACK_DAYS = 20       # trailing sessions for realised intraday vol
CAS_AUCTION_VARIANCE_SHARE = 0.25   # share of the day's variance the market assigns to the
                                    # 15:15->close auction under CAS (Zerodha: ~50% of the
                                    # 0DTE straddle premium still intact at 15:15 => ~25% variance)
